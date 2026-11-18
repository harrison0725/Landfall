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
from dataclasses import dataclass, field, replace

import numpy as np

from . import http
from .fema import Category, Measures, flood_status, verify

# Muse Spark Open runs on Baseten by default. Its weights are open, so any
# OpenAI-compatible server hosting it (vLLM, for example) works: set OPEN_MODEL_URL.
OPEN_URL = os.environ.get("OPEN_MODEL_URL", "https://inference.baseten.co/v1/chat/completions")
OPEN_MODEL = os.environ.get("BASETEN_MODEL", "meta-models/muse-spark-open")
SPARK_URL = "https://api.meta.ai/v1/chat/completions"
SPARK_MODEL = "muse-spark-1.3"
ESCALATE_BELOW = 0.7
MAX_REVISIONS = 2

HAZARDS = {
    "hurricane": dict(
        event="a hurricane",
        categories="""Categories (FEMA PDA Guide, Individual Assistance), as seen from the air:
- none: no visible damage.
- affected: cosmetic damage only, e.g. debris around the house, a few shingles.
- minor: repairable damage, e.g. part of the roof covering missing, a blue tarp on the roof.
- major: structural damage, e.g. large parts of the roof gone, walls collapsed, house shifted.
- destroyed: the house is gone or a total loss: empty slab or lot, pile of debris where it stood.

""",
        flooding="""Flooding is not damage here: judge the category only by damage to the building itself (roof, walls, windows and doors, foundation, porches and decks). Don't raise the category because water stood around the house or got inside it; Landfall states flooding separately, beside the category. If the photos show that water got inside (a waterline or mud line on the walls, soaked furniture, drywall or carpet piled at the curb), say so in the evidence, but that alone is not damage.

""",
        careful="""Be careful: trees (including ones that have grown over the roof since the before photo) and long shadows can hide a roof without damaging it, a house can lean differently in the two photos, and the pre-storm photo is from Feb 2023, so a lot that was empty then may have a new house now, and a building may have been torn down on purpose since. The post-storm photo was taken a few days after the storm, so if the old footprint is now covered by weeds or grass, new pavement, parking stripes, fencing or a construction site, and there is no wreckage, the building was cleared months before the storm: that is demolition, not storm damage. Set demolished_before_storm to true, call it none, say so in the evidence, and set needs_human to true. A storm leaves wreckage, scattered debris or a bare slab with debris around it. A tree the storm blew down onto a house is damage, not cover: its trunk or broken limbs lie across the roof.

""",
    ),
    "wildfire": dict(
        event="a wildfire",
        categories="""Categories (FEMA PDA Guide, Individual Assistance), as seen from the air after a wildfire:
- none: no visible damage. Burned trees, grass or a scorched yard around an intact roof are not damage to the house.
- affected: cosmetic damage only, e.g. a scorched fence, deck edge or outbuilding beside an intact house, ash on the roof.
- minor: repairable damage, e.g. a scorched or partly burned patch of roof or one wall.
- major: structural damage, e.g. a large part of the roof burned through or fallen in, while walls still stand.
- destroyed: burned down: grey ash and rubble over the footprint, a bare foundation or slab, a chimney standing alone, twisted metal roofing, burned-out car shells beside it.

In a burn area most houses are either destroyed or untouched, often side by side: a fire jumps from house to house. Judge each house on its own footprint, not on its neighbors.

""",
        flooding="",
        careful="""Be careful: trees (including ones that have grown over the roof since the before photo) and long shadows can hide a roof without damaging it, a house can lean differently in the two photos, and the before photo can be a year or more older, so a lot that was empty then may have a new house now, and a building may have been torn down on purpose since. Smoke haze can grey the after photo: a roof that only looks dull under haze, with its outline, ridges and shadows intact, is standing. If the old footprint is now covered by grass, new pavement or a construction site and there is no ash or rubble, the building was cleared before the fire: set demolished_before_storm to true, call it none, say so in the evidence, and set needs_human to true.

""",
    ),
    "earthquake": dict(
        event="an earthquake",
        categories="""Categories (FEMA PDA Guide, Individual Assistance), as seen from the air after an earthquake:
- none: no visible damage.
- affected: cosmetic damage only, e.g. a little fallen plaster, tiles or rubble beside an intact building.
- minor: repairable damage, e.g. a parapet, balcony or part of one wall fallen, roof tiles slipped.
- major: structural damage, e.g. part of the building collapsed, a storey pancaked, the building tilted or leaning on a neighbor.
- destroyed: collapsed: a heap of rubble, concrete slabs stacked flat, or the building gone with rubble where it stood.

An earthquake can collapse a building while its roof slab stays in one piece, so a whole roof seen from above does not prove the building stands. Look for what a collapse leaves: rubble spilling past the outline into the street or yard, a roof that sits lower, tilted or shifted off the outline, a much shorter shadow than before, slabs at odd angles. Roof loss measured by Segment Anything can be near zero on a pancaked building; trust the photos over that number. Cleared rubble and bare ground where a building stood a week earlier means it was destroyed.

""",
        flooding="",
        careful="""Be careful: tall buildings lean off their outline by different amounts in the two satellite photos, and shadows change with the time of day; neither is damage. Tents, cars and people in the street after a quake are not damage either. If the old footprint is now covered by grass, new pavement or a construction site with no rubble, the building was cleared before the quake: set demolished_before_storm to true, call it none, say so in the evidence, and set needs_human to true.

""",
    ),
    "flood": dict(
        event="a river flood",
        categories="""Categories (FEMA PDA Guide, Individual Assistance), as seen from the air after a flood:
- none: no visible damage.
- affected: cosmetic damage only, e.g. a mud or debris line in the yard, flood debris against the house.
- minor: repairable damage, e.g. part of the roof or a wall damaged, a deck or porch torn off.
- major: structural damage, e.g. walls broken open, the house shifted on its foundation, a large part missing.
- destroyed: the house is gone or a total loss: swept off its foundation, carried away, an empty slab, a pile of wreckage where it stood or wreckage of it lodged elsewhere.

""",
        flooding="""Flooding is not damage here: judge the category only by damage to the building itself (roof, walls, windows and doors, foundation, porches and decks). Don't raise the category because water stood around the house or got inside it; Landfall states flooding separately, beside the category. If the photos show that water got inside (a waterline or mud line on the walls, soaked furniture, drywall or carpet piled at the curb), say so in the evidence, but that alone is not damage. Mud, sand or silt over the yard, flattened grass pointing downstream and debris piled against trees and fences show where the water went; a house the water carried off, broke open or shifted is damaged, and that counts.

""",
        careful="""Be careful: trees (including ones that have grown over the roof since the before photo) and long shadows can hide a roof without damaging it, a house can lean differently in the two photos, and the before photo can be a year or more older, so a lot that was empty then may have a new house now, and a building may have been torn down on purpose since. A flood leaves mud, wreckage and scattered debris. If the old footprint is now covered by grass, new pavement or a construction site and none of that is there, the building was cleared before the flood: set demolished_before_storm to true, call it none, say so in the evidence, and set needs_human to true.

""",
    ),
}
SYSTEM_TEMPLATE = """You help a county damage-assessment team draft a FEMA Preliminary Damage Assessment after {event}. Your output is a draft that trained assessors will check, never an official determination.

You get one building at a time: an image with the pre-storm photo on the left and the post-storm photo on the right (the post-storm photo has been contrast-stretched). In the US these are aerial photos; elsewhere they are satellite images, which are taken at an angle, so walls show and tall buildings lean off their outline, and may have cloud or cloud shadow over a building: if cloud hides the building, say so, keep confidence low and set needs_human. The building's outline is drawn in yellow on both. You also get measurements made with Segment Anything masks.

{categories}{flooding}The two photos come from different cameras on different days, so the same undamaged building can look different in them: seen from another angle it leans a different way and shifts off its outline, a different side wall shows, a pitched roof's two faces get lighter or darker with the sun, and shadows fall elsewhere. None of that is damage, and neither is a roof that only sits a little off the yellow outline. Call damage only for what a camera angle can't explain: roof covering or decking missing (bare patches, exposed rafters, holes), a tarp, a collapsed or displaced structure, or wreckage. When the only difference is angle, lighting or position, call it none.

{careful}"""
ANSWER = """Answer with JSON only, no other text:
{"category": "none|affected|minor|major|destroyed", "confidence": 0.0-1.0, "evidence": "one or two sentences naming what you see in the photos", "needs_human": true|false, "demolished_before_storm": true|false}"""


STREET_NOTE = """Street-level photos (Mapillary) taken after the disaster follow the aerial pair, one image each, with where each was taken from. A photo from above sees only the roof; these show what it can't: walls and siding torn off or punched in, broken or boarded windows and doors, a house shifted on or knocked off its foundation or pilings, a porch, deck or stairs torn away, a waterline or mud line on the walls, soaked furniture, drywall or carpet piled at the curb (a sign water got inside: say so, but it is flooding, not damage). Use them for the parts of the house the aerial pair can't see, and let clear damage to the building in them raise your call: walls broken open or a house off its foundation is major. They can show a neighbour, a car or a tree instead of this house: the building is where the photo looks (its middle), at the distance given; if you can't tell which building is this one, ignore the photo and say so. Street photos taken weeks later may show repairs or cleanup already done.

From the street photos, also estimate how high the lowest living floor is above the ground in feet (the top of the steps up to the front door, or the floor above pilings or a garage; count the steps at about 0.6 ft each). Give null if you can't see it. Add two fields to your JSON answer: "first_floor_ft": number or null, and "street_evidence": one sentence on what the street photos show of this building, or why they don't help.

"""


def street_content(photos: list[dict]) -> list[dict]:
    """The street photos as message parts, each introduced by where it was taken."""
    parts: list[dict] = []
    for i, ph in enumerate(photos, 1):
        kind = "360° photo, cut to the view toward the building" if ph.get("is_pano") else "photo"
        parts.append({"type": "text", "text": f"Street photo {i}: {kind} taken {ph['captured']}, about {ph['distance_m']:.0f} m from the building, looking {_compass(ph['bearing'])} toward it."})
        parts.append({"type": "image_url", "image_url": {"url": ph["data_url"]}})
    return parts


def _compass(deg: float) -> str:
    return ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"][int((deg % 360) / 45 + 0.5) % 8]


def system(kind: str = "hurricane") -> str:
    """The building prompt for one kind of disaster: what each FEMA category
    looks like from the air after it."""
    if kind not in HAZARDS or kind == "hurricane":
        return SYSTEM_TEMPLATE.format(**HAZARDS["hurricane"]) + ANSWER
    text = SYSTEM_TEMPLATE.format(**HAZARDS[kind])
    return text.replace("pre-storm photo", "before photo").replace("post-storm photo", "after photo") + ANSWER


SYSTEM = system()


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
    demolished: bool = False  # the building was gone before the storm
    first_floor_ft: float | None = None  # read off street photos, when there were any
    street_photos: int = 0
    flood: dict | None = None  # flooding, stated beside the category (fema.flood_status)


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
        lines.append(f"Structure loss: not measured ({extra.get('note') or 'the pre-storm roof was not found on the outline'}). Judge from the photos.")
    else:
        lines.append(f"Share of the pre-storm structure not found in the post-storm photo: {m.roof_loss:.0%}.")
    if (extra.get("tree_share") or 0) >= 0.05:
        lines.append(
            f"Tree canopy covers about {extra['tree_share']:.0%} of the pre-storm structure in the post-storm photo. "
            "That part was left out of the structure loss: leaves hide a roof without removing it. Look at what the tree is. "
            "A living tree that has grown or leans over the roof (green crown, trunk beside the house, often already over it "
            "in the pre-storm photo) is not damage. A tree that fell on the house (trunk or broken limbs lying across the roof, "
            "a root plate torn up beside it) is damage: at least minor, and major if the roof under it is broken open."
        )
    if extra.get("uploaded"):
        lines.append(
            "These are photos the user uploaded (a drone or plane shot), not NOAA or satellite imagery. They may be taken at an angle, "
            "so walls can show and roofs lean off the outline, and the outline was traced on the photo by Segment Anything, so it can be loose."
            if not extra.get("archive_before")
            else "These are photos the user uploaded (a drone or plane shot) for the post-storm side; the pre-storm side is the archive's aerial or "
            "satellite photo of the same spot, taken months or years earlier with a different camera, light and angle. Differences in colour, "
            "shadows and sharpness between the two are not damage."
        )
    if (extra.get("before_years") or 0) >= 2:
        lines.append(
            f"The pre-storm photo is about {extra['before_years']:.0f} years older than the disaster. A house, addition, pool or shed that is new "
            "since then, or a building torn down on purpose, is not damage. Call damage only where the disaster shows: wreckage, debris, "
            "a tarp, ash, mud or a broken roof. When a change could be ordinary building work, call it none and set needs_human to true."
        )
    lines.append(f"Alignment: {extra.get('status', 'unknown')} (matched = its own roof was found; neighbors = placed from nearby houses because its roof was not found where expected).")
    if extra.get("debris_share") is not None:
        if m.debris_share >= 0.05:
            lines.append(
                f"Segment Anything marked possible debris piles over {m.debris_share:.0%} of the yard (about 8 m around the house). "
                "It sometimes marks dark tree canopy as debris, so check the photo."
            )
        else:
            lines.append("Segment Anything found no debris piles around the house (it misses light, scattered debris).")
    if m.tarp_share >= 0.05:
        lines.append(f"Segment Anything marked {m.tarp_share:.0%} of the roof as a blue tarp. Check it is not just a blue-tinted roof.")
    if m.flood_ft is None:
        lines.append("Flood depth: not measured.")
    elif m.flood_ft == 0:
        lines.append("Flood depth: the storm water surface did not reach the ground here.")
    else:
        lines.append(
            f"Storm water stood about {m.flood_ft:.1f} ft above the ground at this building "
            "(surveyed high-water mark and tide gauge minus lidar ground elevation). "
            "This is reported as flooding beside your category; it is not damage and doesn't change the category."
        )
    return "\n".join(lines)


def _post(url: str, key: str, body: dict) -> dict:
    """POST a chat completion, retrying rate limits and server errors."""
    if url == SPARK_URL and "prompt_cache_key" not in body:
        # Meta's API reuses a prompt's unchanged start (the instructions) from
        # cache; one key per model keeps those requests on the same servers
        body = {**body, "prompt_cache_key": f"landfall-{body.get('model', '')}"}
    for attempt in range(4):
        r = http.post(url, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=180)
        if (r.status_code == 429 or r.status_code >= 500) and attempt < 3:
            time.sleep(2 ** (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise AssertionError("unreachable")


# How long the Muse models think before answering. "low" answers in a few
# seconds instead of ten or more; set LANDFALL_REASONING_EFFORT=default to
# leave it to the model.
REASONING_EFFORT = os.environ.get("LANDFALL_REASONING_EFFORT", "low")


def _chat(url: str, key: str, model: str, messages: list[dict], max_tokens: int = 8000, schema: dict | None = None) -> str:
    """One reply's text. With a JSON `schema`, the API holds the reply to it."""
    body = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}}
    if REASONING_EFFORT != "default":
        body["reasoning_effort"] = REASONING_EFFORT
    out = _post(url, key, body)
    return out["choices"][0]["message"].get("content") or ""


def muse_chat(model: str, messages: list[dict], max_tokens: int = 8000, schema: dict | None = None) -> tuple[str, str]:
    """One reply from a Muse model on Meta's API, or from Muse Spark Open on
    Baseten when Meta's API won't serve that model here: (text, model used).
    Some hosts get "not found" for the Muse models from Meta's API while the
    same key works elsewhere (the hosted copy on Render does), and the
    open-weight model answers the same prompts."""
    import requests

    key = os.environ.get("META_API_KEY")
    if key:
        try:
            return _chat(SPARK_URL, key, model, messages, max_tokens, schema), model
        except requests.HTTPError as e:
            if e.response is None or e.response.status_code not in (401, 403, 404):
                raise
    if not os.environ.get("BASETEN_API_KEY"):
        raise RuntimeError(f"{model} isn't available here and there's no Baseten key for Muse Spark Open")
    return _chat(OPEN_URL, os.environ["BASETEN_API_KEY"], OPEN_MODEL, messages, max_tokens, schema), OPEN_MODEL


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
        demolished=bool(a.get("demolished_before_storm", False)),
        first_floor_ft=_feet(a.get("first_floor_ft")),
        street_evidence=str(a.get("street_evidence") or "").strip(),
    )


def _feet(x) -> float | None:
    """A first-floor height the model read off street photos, if it's plausible."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if 0 <= v <= 30 else None


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
        if answer["demolished"]:
            # No building stood here during the storm, so the measurement
            # floors don't apply. An assessor confirms it on the ground.
            trace.append(dict(model=model, round=round_, answer={**answer, "category": answer["category"].name}, verifier=[]))
            return answer, False
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
    kind: str = "hurricane",
    street: list[dict] | None = None,
) -> Assessment:
    image = pair_image(pre, post, outline)
    prompt_system = system(kind)
    if street:
        prompt_system = prompt_system.replace(ANSWER, STREET_NOTE + ANSWER)
    extra_parts = street_content(street) if street else []
    prompt = f"Building {building_id}.\n{describe(m, extra)}"
    messages = [
        {"role": "system", "content": prompt_system},
        {"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": image}}, *extra_parts]},
    ]
    trace: list[dict] = []
    answer, ok = _converse(OPEN_URL, os.environ["BASETEN_API_KEY"], OPEN_MODEL, messages, m, trace)
    decided_by = OPEN_MODEL

    # Destroyed is the call with the most at stake (and the one demolition
    # since the pre-storm photo imitates), so it always gets a second look.
    if escalate and (answer["confidence"] < ESCALATE_BELOW or not ok or answer["category"] is Category.DESTROYED):
        second = [
            {"role": "system", "content": prompt_system},
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
                    *extra_parts,
                ],
            },
        ]
        try:
            answer, ok = _converse(SPARK_URL, os.environ["META_API_KEY"], SPARK_MODEL, second, m, trace)
            decided_by = SPARK_MODEL
        except Exception as e:  # the second opinion is optional; keep the first
            trace.append(dict(model=SPARK_MODEL, error=f"second opinion failed: {e}"[:300]))
            ok = False

    category = answer["category"]
    evidence = answer["evidence"]
    first_floor = answer.get("first_floor_ft") if street else None
    if street and answer.get("street_evidence"):
        evidence += f" Street photos: {answer['street_evidence']}"
    if first_floor is not None and m.first_floor_ft is None:
        # the street photos show how high the floor is, so the flood note can
        # say whether water reached the living space
        m = replace(m, first_floor_ft=first_floor)
    flood = flood_status(m)
    if answer["demolished"]:
        category = Category.NONE

    return Assessment(
        building_id=building_id,
        category=category,
        confidence=answer["confidence"],
        evidence=evidence,
        needs_human=answer["needs_human"] or not ok or not m.aligned or answer["demolished"],
        decided_by=decided_by,
        verified=ok,
        trace=trace,
        demolished=answer["demolished"],
        first_floor_ft=first_floor,
        street_photos=len(street or []),
        flood=flood,
    )
