"""The per-building assessment loop.

Muse Spark Open (open weights, on Baseten) looks at the before/after photo
pair with the measurements and proposes a FEMA category. The deterministic
verifier in fema.py checks the call against the measurements; if it objects,
the model reads the objection and revises (up to MAX_REVISIONS times). Calls
the open model is unsure about go to Muse Spark on Meta Model API for a
second opinion, which is checked by the same verifier.

Every step is kept in the trace, so a reviewer can see why a building got
its category.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import time
from dataclasses import dataclass, field

import numpy as np

from .fema import Category, Measures, verify

OPEN_URL = "https://inference.baseten.co/v1/chat/completions"
OPEN_MODEL = os.environ.get("BASETEN_MODEL", "meta-models/muse-spark-open")
SPARK_URL = "https://api.meta.ai/v1/chat/completions"
SPARK_MODEL = "muse-spark-1.3"
ESCALATE_BELOW = 0.7
MAX_REVISIONS = 2

SYSTEM = """You help a county damage-assessment team draft a FEMA Preliminary Damage Assessment after a hurricane. Your output is a draft that trained assessors will check, never an official determination.

You get one building at a time: an image with the pre-storm aerial photo on the left and the post-storm photo on the right (the post-storm flight is darker; it has been brightened). The building's outline is drawn in yellow on both. You also get measurements made with Segment Anything masks.

Categories (FEMA PDA Guide, Individual Assistance), as seen from the air:
- none: no visible damage.
- affected: cosmetic damage only, e.g. debris around the house, a few shingles.
- minor: repairable damage, e.g. part of the roof covering missing, a blue tarp on the roof.
- major: structural damage, e.g. large parts of the roof gone, walls collapsed, house shifted.
- destroyed: the house is gone or a total loss: empty slab or lot, pile of debris where it stood.

Be careful: trees and long shadows can hide a roof without damaging it, a house can lean differently in the two photos, and the pre-storm photo is from Feb 2023, so a lot that was empty then may have a new house now.

Answer with JSON only, no other text:
{"category": "none|affected|minor|major|destroyed", "confidence": 0.0-1.0, "evidence": "one or two sentences naming what you see in the photos", "needs_human": true|false}"""


@dataclass
class Assessment:
    building_id: str
    category: Category
    confidence: float
    evidence: str
    needs_human: bool
    decided_by: str  # model id that made the final call
    verified: bool
    trace: list[dict] = field(default_factory=list)


def pair_image(pre: np.ndarray, post: np.ndarray, outline: list[tuple[float, float]], scale: int = 3) -> str:
    """Side-by-side pre|post PNG with the outline, as a data URL."""
    from PIL import Image, ImageDraw

    def prep(a: np.ndarray, brighten: bool) -> Image.Image:
        a = np.asarray(a, dtype=np.uint8)
        if brighten:
            lo, hi = np.percentile(a, [1, 99])
            if hi > lo:
                a = np.clip((a.astype(np.float32) - lo) * 255 / (hi - lo), 0, 255).astype(np.uint8)
        im = Image.fromarray(a).resize((a.shape[1] * scale, a.shape[0] * scale), Image.LANCZOS)
        ImageDraw.Draw(im).polygon([(x * scale, y * scale) for x, y in outline], outline=(255, 255, 0), width=2)
        return im

    a, b = prep(pre, False), prep(post, True)
    out = Image.new("RGB", (a.width + b.width + 8, max(a.height, b.height)), (255, 255, 255))
    out.paste(a, (0, 0))
    out.paste(b, (a.width + 8, 0))
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def describe(m: Measures, extra: dict) -> str:
    lines = []
    if extra.get("structure_loss") is None:
        lines.append("Structure loss: not measured (the pre-storm roof was not found on the outline).")
    else:
        lines.append(f"Share of the pre-storm structure not found in the post-storm photo: {m.roof_loss:.0%}.")
    lines.append(f"Alignment: {extra.get('status', 'unknown')} (matched = its own roof was found; neighbors = placed from nearby houses because its roof was not found where expected).")
    lines.append("Flood depth: not measured yet." if m.flood_ft is None else f"Estimated flood depth: {m.flood_ft:.1f} ft.")
    return "\n".join(lines)


def _chat(url: str, key: str, model: str, messages: list[dict], max_tokens: int = 3000) -> str:
    import requests

    for attempt in range(4):
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "messages": messages, "max_tokens": max_tokens},
            timeout=180,
        )
        if (r.status_code == 429 or r.status_code >= 500) and attempt < 3:
            time.sleep(2 ** (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()["choices"][0]["message"].get("content") or ""
    raise AssertionError("unreachable")


def parse_answer(text: str) -> dict:
    """Pull the JSON object out of a model reply. Raises ValueError if there
    is none or the category is not one of ours."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"no JSON in reply: {text[:200]!r}")
    a = json.loads(m.group(0))
    name = str(a.get("category", "")).strip().lower()
    cats = {c.name.lower(): c for c in Category}
    if name not in cats:
        raise ValueError(f"unknown category {name!r}")
    return dict(
        category=cats[name],
        confidence=float(a.get("confidence", 0.0)),
        evidence=str(a.get("evidence", "")).strip(),
        needs_human=bool(a.get("needs_human", False)),
    )


def _converse(url: str, key: str, model: str, messages: list[dict], m: Measures, trace: list[dict]) -> tuple[dict, bool]:
    """Ask, verify, and let the model revise until the verifier is satisfied."""
    answer = None
    for round_ in range(MAX_REVISIONS + 1):
        text = _chat(url, key, model, messages)
        try:
            answer = parse_answer(text)
        except (ValueError, json.JSONDecodeError) as e:
            trace.append(dict(model=model, round=round_, error=str(e)))
            messages += [
                {"role": "assistant", "content": text},
                {"role": "user", "content": "Answer with the JSON object only."},
            ]
            continue
        v = verify(answer["category"], m)
        trace.append(dict(model=model, round=round_, answer={**answer, "category": answer["category"].name}, verifier=v.problems))
        if v.ok:
            return answer, True
        if round_ > 0 and answer["needs_human"]:
            # The model looked again and stands by a call the numbers disagree
            # with. That disagreement is for an assessor, not for us to force.
            return answer, False
        messages += [
            {"role": "assistant", "content": text},
            {
                "role": "user",
                "content": "The checker objected:\n- "
                + "\n- ".join(v.problems)
                + "\nLook again. The measurements can be wrong (trees, shadows, a house that leans or was"
                " misplaced): if the photos show they are, keep your call, say what the measurement got"
                " wrong in the evidence, and set needs_human to true. Answer with the JSON object only.",
            },
        ]
    if answer is None:
        raise RuntimeError(f"{model} gave no usable answer")
    return answer, False


def assess(
    building_id: str,
    pre: np.ndarray,
    post: np.ndarray,
    outline: list[tuple[float, float]],
    m: Measures,
    extra: dict,
    escalate: bool = True,
) -> Assessment:
    image = pair_image(pre, post, outline)
    prompt = f"Building {building_id}.\n{describe(m, extra)}"
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": image}}]},
    ]
    trace: list[dict] = []
    answer, ok = _converse(OPEN_URL, os.environ["BASETEN_API_KEY"], OPEN_MODEL, messages, m, trace)
    decided_by = OPEN_MODEL

    if escalate and (answer["confidence"] < ESCALATE_BELOW or not ok):
        second = [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt
                        + f"\n\nA first reviewer said {answer['category'].name.lower()} "
                        f"(confidence {answer['confidence']:.2f}): {answer['evidence']}\n"
                        "Give your own independent call.",
                    },
                    {"type": "image_url", "image_url": {"url": image}},
                ],
            },
        ]
        answer, ok = _converse(SPARK_URL, os.environ["META_API_KEY"], SPARK_MODEL, second, m, trace)
        decided_by = SPARK_MODEL

    return Assessment(
        building_id=building_id,
        category=answer["category"],
        confidence=answer["confidence"],
        evidence=answer["evidence"],
        needs_human=answer["needs_human"] or not ok or not m.aligned,
        decided_by=decided_by,
        verified=ok,
        trace=trace,
    )
