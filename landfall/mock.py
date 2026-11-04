"""Mock mode: run Landfall with no accounts, no API keys and no network calls to any model.

It is on when LANDFALL_MOCK=1, or when neither META_API_KEY nor BASETEN_API_KEY is
set. The hosted copy also uses it for a visitor who draws a box without the access code.

Two things change, and nothing else:

* The model is replaced by fixed rules (`reply`). For a question about a finished
  area they pick one of the area tools by keywords, call it on the saved results,
  and write the answer from what the tool returned. For a drawn box they call the
  seven pipeline tools in order, then the overview, cost and needs tools, and write
  the first report.
* The pipeline tools replay a recorded run (`replay`) instead of fetching photos
  and calling Segment Anything and the models: the drawn box is matched to the
  nearest finished example area, and that area's saved results are streamed back
  stage by stage, as the real run streams them.

Every mock answer says that it is one, so nobody mistakes a replay for a new assessment.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from pathlib import Path

from .area import ORDER

NOTE = "Mock mode (no API keys): answered by fixed rules from the saved results, no AI model was called."
REPLAY_NOTE = (
    "Mock mode (no API keys or access code): this replays the recorded run of {label}, the nearest finished example, "
    "instead of assessing your box. No photos were downloaded and no AI model was called."
)
DEMO = Path(__file__).resolve().parents[1] / "demo"

PIPELINE = [
    "locate_imagery",
    "prepare_area",
    "measure_structures",
    "measure_flood",
    "look_for_debris",
    "draft_categories",
    "estimate_costs_and_needs",
]
REPORT = ["area_overview", "repair_costs", "urgent_needs"]
WORDS = {"none": "no damage seen", "affected": "slightly damaged", "minor": "damaged", "major": "badly damaged", "destroyed": "destroyed"}


def enabled() -> bool:
    """On by request, or whenever there is no key to call a model with."""
    if os.environ.get("LANDFALL_MOCK", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return not (os.environ.get("META_API_KEY") or os.environ.get("BASETEN_API_KEY"))


# ---- the stand-in model -------------------------------------------------------


def reply(messages: list[dict]) -> dict:
    """The next assistant message, in the chat API's shape: tool calls, or the answer."""
    start = max(i for i, m in enumerate(messages) if m["role"] == "user")
    asked = messages[start]["content"]
    done = [(m, json.loads(m["content"])) for m in messages[start + 1 :] if m["role"] == "tool"]
    called = [c["function"]["name"] for m in messages[start + 1 :] if m["role"] == "assistant" for c in m.get("tool_calls") or []]
    results = dict(zip(called, (r for _, r in done)))

    if "[The user drew a box" in asked:
        if "They uploaded their own photos" in asked:
            return _say("Mock mode can't check uploaded photos: placing them needs the photo search and Segment Anything. " + _try_examples())
        for name in PIPELINE + REPORT:
            if name not in results:
                if any("error" in r for r in results.values()):
                    break
                return _call(name, {"storm": _storm(asked)} if name == "locate_imagery" else {}, len(called))
        return _say(_first_report(results))

    question = asked.split("\n\n[Current area:")[0].lower()
    if not results:
        name, args = _pick(question)
        return _call(name, args, len(called))
    name, result = next(iter(results.items()))
    return _say(_answer(name, result))


def _call(name: str, args: dict, n: int) -> dict:
    return {"role": "assistant", "content": "", "tool_calls": [{"id": f"mock_{n}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def _say(text: str) -> dict:
    return {"role": "assistant", "content": text}


def _storm(asked: str) -> str:
    m = re.search(r"storm: ([a-z0-9_]+)", asked)
    return m.group(1) if m else "helene"


def _pick(q: str) -> tuple[str, dict]:
    m = re.search(r"\b([a-z]{0,3}\d{2,4})\b", q)
    if m and re.search(r"\bb\d", q):
        return "get_building", {"building_id": m.group(1)}
    if re.search(r"visit|first|priorit|inspect|route|field|team", q):
        return "plan_inspections", {"max_stops": 10}
    if re.search(r"cost|repair|money|\$|dollar|price|value", q):
        return "repair_costs", {"limit": 5}
    if re.search(r"household|displac|people|famil|lodging|business|need", q):
        return "urgent_needs", {"limit": 5}
    if re.search(r"flood|water|surge", q):
        return "find_buildings", {"min_flood_ft": 0.5, "sort_by": "flood", "limit": 10}
    for cat, word in (("destroyed", "destroy"), ("major", "badly|major"), ("minor", "minor"), ("affected", "slight|affected")):
        if re.search(word, q):
            return "find_buildings", {"category": cat, "limit": 10}
    if re.search(r"fema|pda|total|count|how many", q):
        return "pda_totals", {}
    return "area_overview", {}


def _try_examples() -> str:
    return "You can still pick a finished area from the Area menu and ask about it, or set META_API_KEY and BASETEN_API_KEY to run the real agent."


def _answer(name: str, r: dict) -> str:
    if "error" in r:
        return f"{r['error']}. {_try_examples()}\n\n_{NOTE}_"
    if name == "plan_inspections":
        stops = r["stops"]
        lines = [f"{s['stop']}. {s['id']}: {WORDS.get(s.get('category'), s.get('category'))}" for s in stops]
        text = f"Visit these {len(stops)} buildings first, in this walking order (about {r['walk_m']} m):\n\n" + "\n".join(lines)
    elif name == "repair_costs":
        lo, hi = r["total_usd"]
        text = f"Repairs here would cost roughly ${lo:,.0f} to ${hi:,.0f} in all. The costliest buildings: " + ", ".join(
            f"{x['id']} (${x['cost_usd'][0]:,.0f} to ${x['cost_usd'][1]:,.0f})" for x in r["costliest"]
        ) + "."
    elif name == "urgent_needs":
        text = (
            f"About {r['households_likely_displaced']} households are likely out of their homes, and {r['households_possibly_displaced']} more possibly. "
            "Reach these first: " + ", ".join(f"{x['id']} ({WORDS.get(x['category'], x['category'])})" for x in r["most_urgent_first"]) + "."
        )
    elif name == "find_buildings":
        shown = r["shown"]
        def one(x: dict) -> str:
            water = f", {x['flood_ft']:.1f} ft of water" if x.get("flood_ft") else ""
            return f"{x['id']} ({WORDS.get(x.get('category'), x.get('category'))}{water})"

        text = f"{r['matches']} buildings match. " + ("Here are the first ones: " + ", ".join(one(x) for x in shown) + "." if shown else "")
    elif name == "get_building":
        text = f"{r['id']} is drafted as {WORDS.get(r.get('category'), r.get('category'))}. {r.get('evidence') or ''}".strip()
    elif name == "pda_totals":
        text = "Draft counts for the FEMA assessment: " + ", ".join(f"{v['count']} {WORDS[k]}" for k, v in r.items() if k in WORDS) + "."
    else:
        text = _overview(r)
    return f"{text}\n\n_{NOTE}_"


def _overview(r: dict) -> str:
    by = ", ".join(f"{n} {WORDS.get(c, c)}" for c, n in r["by_category"].items())
    water = (
        f" Water reached {r['buildings_with_water_around_them']} buildings, up to {r['deepest_water_ft']} ft deep."
        if r["buildings_with_water_around_them"]
        else ""
    )
    return f"{r['buildings_assessed']} buildings were checked here: {by}. {r['sent_to_human_review']} need a visit to confirm.{water}"


def _first_report(results: dict) -> str:
    loc = results.get("locate_imagery", {})
    if "error" in loc:
        return f"{loc['error']} {_try_examples()}"
    parts = [loc.get("mock_note", ""), _overview(results["area_overview"])]
    costs = results.get("repair_costs", {})
    if "total_usd" in costs:
        lo, hi = costs["total_usd"]
        parts.append(f"Repairs would cost roughly ${lo:,.0f} to ${hi:,.0f}.")
    needs = results.get("urgent_needs", {})
    if "households_likely_displaced" in needs:
        first = ", ".join(x["id"] for x in needs["most_urgent_first"][:5])
        parts.append(f"About {needs['households_likely_displaced']} households are likely displaced; reach {first} first.")
    parts.append("Every call is a draft for a trained assessor.")
    return " ".join(p for p in parts if p)


# ---- the recorded run -----------------------------------------------------------


def nearest_example(root: Path, box: tuple[float, float, float, float]) -> str | None:
    """The finished example area closest to the box's center."""
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    best, best_d = None, math.inf
    names = {d.name for d in DEMO.iterdir() if d.is_dir() and (d / "spec.json").exists()} if DEMO.is_dir() else set()
    for name in sorted(names):
        d = root / name
        if not (d / "assessments.json").exists():
            continue
        x, y = json.loads((d / "spec.json").read_text())["center"]
        dist = math.hypot((x - cx) * math.cos(math.radians(cy)), y - cy)
        if dist < best_d:
            best, best_d = name, dist
    return best


def _label(d: Path) -> str:
    return (d / "label.txt").read_text().strip() if (d / "label.txt").exists() else d.name


def locate(root: Path, box: tuple[float, float, float, float]) -> dict:
    name = nearest_example(root, box)
    if not name:
        return {"error": "Mock mode has no finished example area to replay"}
    d = root / name
    spec = json.loads((d / "spec.json").read_text())
    return {
        "area": name,
        "bbox": spec["bbox"],
        "flight": spec.get("noaa_flight") or None,
        "buildings_to_assess": len(json.loads((d / "assessments.json").read_text())),
        "mock_note": REPLAY_NOTE.format(label=_label(d)),
    }


PAUSE = float(os.environ.get("LANDFALL_MOCK_PAUSE", "0.6"))  # seconds per stage, so the plan ticks off visibly


def replay(root: Path, area: str, stage: str, log) -> None:
    """Stream one stage of the area's recorded run, in the real run's event shapes."""
    import time

    time.sleep(PAUSE)
    d = root / area
    drafts = json.loads((d / "assessments.json").read_text())
    if stage == "prepare":
        al = json.loads((d / "alignment.json").read_text())["summary"] if (d / "alignment.json").exists() else {}
        log("buildings", found=len(drafts), kept=len(drafts))
        if al:
            log("aligned", matched=al.get("aligned", 0), from_neighbors=al.get("placed_from_neighbors", 0), unaligned=al.get("unaligned", 0))
    elif stage == "segment":
        loss = [(a["id"], a.get("structure_loss")) for a in drafts if a.get("structure_loss") is not None]
        log(
            "structure_loss",
            measured=len(loss),
            of=len(drafts),
            over_60=[i for i, v in loss if v >= 0.6],
            over_25=[i for i, v in loss if 0.25 <= v < 0.6],
        )
    elif stage == "flood":
        depth = [a.get("flood_ft") or 0 for a in drafts]
        log("flood", flooded=sum(v > 0 for v in depth), of=len(drafts), max_ft=round(max(depth, default=0), 1))
    elif stage == "debris":
        log("warning", text="mock mode: debris and tarp findings are already part of the recorded drafts")
    elif stage == "assess":
        order = sorted(drafts, key=lambda a: -ORDER.index(a["category"]) if a["category"] in ORDER else 0)
        log("assess_plan", buildings=len(drafts), first=[a["id"] for a in order[:5]])
        for a in order:
            time.sleep(PAUSE / 40)
            log("building", id=a["id"], category=a["category"], confidence=a.get("confidence"), needs_human=a.get("needs_human"), evidence=a.get("evidence") or "")
        log("assessed", counts=dict(Counter(a["category"] for a in drafts)), review=sum(bool(a.get("needs_human")) for a in drafts))
