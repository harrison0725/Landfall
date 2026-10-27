"""The area-level agent: answers an emergency manager's questions about a
whole town and drafts the PDA summary.

The per-building drafts (agent.py) are the facts. This agent never looks at
photos itself; it works through tools that read those drafts, so every number
it reports comes from a tool result and every building it names can be
opened. Tools are plain functions over the area's data folder, which keeps
them testable without a model.

The model is Muse Spark Open on Baseten by default (open weights), with
Muse Spark on Meta Model API as an option. Both speak OpenAI-style tool calls.
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .agent import OPEN_MODEL, OPEN_URL, SPARK_MODEL, SPARK_URL

ORDER = ["none", "affected", "minor", "major", "destroyed"]
MAX_STEPS = 12

SYSTEM = """You are Landfall, an assistant for a county emergency management team after a hurricane. You work on one area at a time. Damage drafts already exist for every building in the area: a FEMA Preliminary Damage Assessment category (none, affected, minor, major, destroyed) made from before/after aerial photos, Segment Anything measurements, and surveyed flood depth.

Rules:
- Use the tools for every number and every building you mention. Never guess a count, a depth or a building id.
- These are drafts for trained assessors, not official determinations. Say so in anything meant to be shared.
- Name buildings by id and say why each one matters (category, flood depth, review flag).
- Aerial photos can't see inside a house. Where water stood around a house but the first-floor height is unknown, the draft says affected; the real category may be higher. Point this out when flood matters to the answer.
- Copy coordinates exactly as the tools give them ("lat, lon"; latitude is positive, longitude negative here).
- Be brief and concrete. Lead with the answer."""


@dataclass
class Area:
    name: str
    buildings: dict[str, dict]  # id -> merged record
    meta: dict

    @classmethod
    def load(cls, root: Path, name: str) -> Area:
        d = root / name
        feats = {f["properties"]["id"]: f["properties"] for f in json.loads((d / "buildings.geojson").read_text())["features"]}
        drafts = {a["id"]: a for a in json.loads((d / "assessments.json").read_text())}
        flood = json.loads((d / "flood.json").read_text()) if (d / "flood.json").exists() else {"buildings": []}
        ground = {b["id"]: b.get("ground_ft") for b in flood["buildings"]}
        summary = json.loads((d / "alignment.json").read_text())["summary"]
        buildings = {}
        for bid, a in drafts.items():
            p = feats.get(bid, {})
            buildings[bid] = {
                "id": bid,
                "category": a.get("category", "error"),
                "confidence": a.get("confidence"),
                "needs_human": a.get("needs_human", True),
                "evidence": a.get("evidence", a.get("error", "")),
                "decided_by": a.get("decided_by"),
                "structure_loss": a.get("structure_loss"),
                "flood_ft": a.get("flood_ft"),
                "ground_ft": ground.get(bid),
                "debris_share": a.get("debris_share"),
                "tarp_share": a.get("tarp_share"),
                "lon": p.get("centroid", [None, None])[0],
                "lat": p.get("centroid", [None, None])[1],
                "area_m2": p.get("area_m2"),
                "objections": [o for t in a.get("trace", []) for o in t.get("verifier", [])],
            }
        meta = {
            "post_storm_photos": f"NOAA emergency response flight {summary.get('noaa_flight')}",
            "pre_storm_photos": f"USDA NAIP, {summary.get('naip_date')}",
            "water_surface_ft_navd88": flood.get("surface_ft_navd88"),
            "water_observations": flood.get("observations", []),
        }
        return cls(name, buildings, meta)

    # ---- tools -------------------------------------------------------------

    def area_overview(self) -> dict:
        b = list(self.buildings.values())
        counts = Counter(x["category"] for x in b)
        flooded = [x for x in b if (x["flood_ft"] or 0) > 0]
        return {
            "area": self.name,
            "buildings_assessed": len(b),
            "by_category": {c: counts.get(c, 0) for c in ORDER if counts.get(c)},
            "sent_to_human_review": sum(x["needs_human"] for x in b),
            "buildings_with_water_around_them": len(flooded),
            "deepest_water_ft": max((x["flood_ft"] for x in flooded), default=0),
            "sources": self.meta,
        }

    def find_buildings(
        self,
        category: str | None = None,
        at_least: str | None = None,
        needs_review: bool | None = None,
        min_flood_ft: float | None = None,
        sort_by: str = "severity",
        limit: int = 15,
    ) -> dict:
        rows = list(self.buildings.values())
        if category:
            rows = [x for x in rows if x["category"] == category]
        if at_least in ORDER:
            rows = [x for x in rows if x["category"] in ORDER and ORDER.index(x["category"]) >= ORDER.index(at_least)]
        if needs_review is not None:
            rows = [x for x in rows if x["needs_human"] == needs_review]
        if min_flood_ft is not None:
            rows = [x for x in rows if (x["flood_ft"] or 0) >= min_flood_ft]
        rows.sort(key=_sort_key(sort_by))
        limit = max(1, min(int(limit), 50))
        return {
            "matches": len(rows),
            "shown": [_brief(x) for x in rows[:limit]],
        }

    def get_building(self, building_id: str) -> dict:
        x = self.buildings.get(building_id)
        if not x:
            return {"error": f"no building {building_id!r} in {self.name}"}
        out = {k: v for k, v in x.items() if k not in ("lon", "lat")}
        if x["lat"] is not None:
            out["lat_lon"] = f"{x['lat']:.5f}, {x['lon']:.5f}"
        return out

    def plan_inspections(self, max_stops: int = 15, include_flood_only: bool = True) -> dict:
        """Which buildings a field team should visit, in walking order.

        Picks drafts that are severe or that the agent was unsure about, most
        urgent first, then orders the visit as a nearest-neighbor walk from
        the most urgent one.
        """
        def urgent(x: dict) -> bool:
            if x["category"] in ("minor", "major", "destroyed"):
                return True
            if x["needs_human"] and x["category"] != "none":
                return True
            return include_flood_only and (x["flood_ft"] or 0) >= 1.5

        picks = sorted((x for x in self.buildings.values() if urgent(x) and x["lon"] is not None), key=_sort_key("severity"))
        picks = picks[: max(1, min(int(max_stops), 60))]
        if not picks:
            return {"stops": [], "walk_m": 0}
        route = [picks.pop(0)]
        while picks:
            last = route[-1]
            nxt = min(picks, key=lambda x: _meters(last, x))
            picks.remove(nxt)
            route.append(nxt)
        walk = sum(_meters(a, b) for a, b in zip(route, route[1:]))
        return {
            "stops": [{"stop": i + 1, **_brief(x), "lat_lon": f"{x['lat']:.5f}, {x['lon']:.5f}"} for i, x in enumerate(route)],
            "walk_m": round(walk),
        }

    def pda_totals(self) -> dict:
        """Counts in PDA form, with how many of each are flood-limited."""
        b = list(self.buildings.values())
        out = {}
        for c in ORDER[1:]:
            rows = [x for x in b if x["category"] == c]
            out[c] = {
                "count": len(rows),
                "needs_review": sum(x["needs_human"] for x in rows),
                "water_around_house_1_5ft_plus": sum((x["flood_ft"] or 0) >= 1.5 for x in rows),
            }
        out["note"] = (
            "Water 1.5 ft or more above ground could put 18 inches or more in the living space of a house built at grade, "
            "which is Major under the PDA guide. Aerial photos do not show first-floor height, so those houses are drafted "
            "at the category the photos support and need a field check."
        )
        return out

    # ---- plumbing ----------------------------------------------------------

    def call(self, name: str, args: dict) -> dict:
        fn = {
            "area_overview": self.area_overview,
            "find_buildings": self.find_buildings,
            "get_building": self.get_building,
            "plan_inspections": self.plan_inspections,
            "pda_totals": self.pda_totals,
        }.get(name)
        if fn is None:
            return {"error": f"unknown tool {name}"}
        try:
            return fn(**args)
        except TypeError as e:
            return {"error": f"bad arguments for {name}: {e}"}


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "area_overview",
            "description": "Totals for the area: buildings assessed, count per category, how many were sent to human review, flooding, and the data sources.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_buildings",
            "description": "List buildings matching filters, most severe first by default. Each row has id, category, confidence, review flag, flood depth, structure loss and a short piece of evidence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": ORDER, "description": "exactly this category"},
                    "at_least": {"type": "string", "enum": ORDER, "description": "this category or worse"},
                    "needs_review": {"type": "boolean"},
                    "min_flood_ft": {"type": "number", "description": "water at least this deep above ground"},
                    "sort_by": {"type": "string", "enum": ["severity", "flood", "structure_loss", "least_confident"]},
                    "limit": {"type": "integer", "description": "rows to return, at most 50"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_building",
            "description": "Everything known about one building: category, confidence, evidence, measurements, location, and what the checker objected to.",
            "parameters": {"type": "object", "properties": {"building_id": {"type": "string"}}, "required": ["building_id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_inspections",
            "description": "Pick the buildings a field team should visit (severe drafts, unsure drafts, deep water) and order them as a walking route. Returns stops with coordinates and the walking distance.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_stops": {"type": "integer"},
                    "include_flood_only": {"type": "boolean", "description": "also visit houses whose only issue is 1.5 ft+ of water around them"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pda_totals",
            "description": "Counts per PDA category (affected, minor, major, destroyed) with how many need review and how many had 1.5 ft or more of water around them.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def _sort_key(by: str):
    if by == "flood":
        return lambda x: -(x["flood_ft"] or 0)
    if by == "structure_loss":
        return lambda x: -(x["structure_loss"] or 0)
    if by == "least_confident":
        return lambda x: x["confidence"] if x["confidence"] is not None else 0
    return lambda x: (-(ORDER.index(x["category"]) if x["category"] in ORDER else 5), not x["needs_human"], -(x["flood_ft"] or 0))


def _brief(x: dict) -> dict:
    ev = x["evidence"] or ""
    return {
        "id": x["id"],
        "category": x["category"],
        "confidence": x["confidence"],
        "needs_review": x["needs_human"],
        "flood_ft": x["flood_ft"],
        "structure_loss": x["structure_loss"],
        "evidence": ev if len(ev) <= 160 else ev[:157] + "...",
    }


def _meters(a: dict, b: dict) -> float:
    k = 111_320.0
    dx = (a["lon"] - b["lon"]) * k * math.cos(math.radians((a["lat"] + b["lat"]) / 2))
    dy = (a["lat"] - b["lat"]) * k
    return math.hypot(dx, dy)


def unknown_ids(area: Area, text: str) -> list[str]:
    """Building ids in an answer that don't exist in the area: the one
    hallucination a reader can't catch by looking."""
    import re

    return sorted({m for m in re.findall(r"\bb\d{3,}\b", text) if m not in area.buildings})


@dataclass
class Answer:
    text: str
    steps: list[dict]
    model: str


def ask(area: Area, question: str, use_spark: bool = False, max_tokens: int = 8000) -> Answer:
    """Run the tool loop until the model answers in words."""
    from .agent import _post

    url, key, model = (SPARK_URL, os.environ.get("META_API_KEY"), SPARK_MODEL) if use_spark else (OPEN_URL, os.environ.get("BASETEN_API_KEY"), OPEN_MODEL)
    messages: list[dict] = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    steps: list[dict] = []
    for _ in range(MAX_STEPS):
        msg = _post(url, key, {"model": model, "messages": messages, "tools": TOOLS, "max_tokens": max_tokens})["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        messages.append({"role": "assistant", "content": msg.get("content") or "", **({"tool_calls": calls} if calls else {})})
        if not calls:
            return Answer(msg.get("content") or "", steps, model)
        for c in calls:
            name = c["function"]["name"]
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            result = area.call(name, args if isinstance(args, dict) else {})
            steps.append({"tool": name, "args": args})
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(result)})
    messages.append({"role": "user", "content": "Answer now with what you have; no more tools."})
    msg = _post(url, key, {"model": model, "messages": messages, "max_tokens": max_tokens})["choices"][0]["message"]
    return Answer(msg.get("content") or "", steps, model)


PDA_REQUEST = """Write the draft Preliminary Damage Assessment summary for this area, for the county emergency manager to forward to the state. Use markdown with these sections:

1. Headline: two sentences on what happened here and the totals.
2. Damage by category: a table of affected, minor, major and destroyed counts, with how many of each need a field check.
3. Flooding: how deep the water was, how it was measured, and why the flood-only houses may be undercounted.
4. Destroyed and major: each building by id with one line of evidence.
5. Field inspection plan: the route for the first team, with the number of stops and the walking distance.
6. Limits: what aerial drafts can't see, and that these are drafts for trained assessors."""
