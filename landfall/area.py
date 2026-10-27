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
from .fema import INTERNATIONAL

ORDER = ["none", "affected", "minor", "major", "destroyed"]
MAX_STEPS = 12

SYSTEM = """You are Landfall, an assistant for a county emergency management team after a hurricane. You work on one area at a time. Damage drafts already exist for every building in the area: a FEMA Preliminary Damage Assessment category (none, affected, minor, major, destroyed) made from before/after aerial photos, Segment Anything measurements, and surveyed flood depth.

Rules:
- Use the tools for every number and every building you mention. Never guess a count, a depth or a building id.
- These are drafts for trained assessors, not official determinations. Say so in anything meant to be shared.
- Name buildings by id and say why each one matters (category, flood depth, review flag).
- Aerial photos can't see inside a house. Where water stood around a house but the first-floor height is unknown, the draft says affected; the real category may be higher. Point this out when flood matters to the answer.
- Copy coordinates exactly as the tools give them ("lat, lon").
- Outside the US, also give the international grade (Copernicus EMS: no visible damage, possibly damaged, damaged, destroyed) that the tools report, and say flood depth and repair cost are unknown when the tools say so.
- Be brief and concrete. Lead with the answer."""


@dataclass
class Area:
    name: str
    buildings: dict[str, dict]  # id -> merged record
    meta: dict
    costs: dict | None = None
    needs: dict | None = None

    @classmethod
    def load(cls, root: Path, name: str) -> Area:
        d = root / name
        feats = {f["properties"]["id"]: f["properties"] for f in json.loads((d / "buildings.geojson").read_text())["features"]}
        drafts = {a["id"]: a for a in json.loads((d / "assessments.json").read_text())}
        flood = json.loads((d / "flood.json").read_text()) if (d / "flood.json").exists() else {"buildings": []}
        ground = {b["id"]: b.get("ground_ft") for b in flood["buildings"]}
        summary = json.loads((d / "alignment.json").read_text())["summary"]
        costs = json.loads((d / "costs.json").read_text()) if (d / "costs.json").exists() else None
        needs = json.loads((d / "needs.json").read_text()) if (d / "needs.json").exists() else None
        cost_of = {c["id"]: c for c in (costs or {}).get("buildings", [])}
        need_of = {n["id"]: n for n in (needs or {}).get("buildings", [])}
        buildings = {}
        for bid, a in drafts.items():
            p = feats.get(bid, {})
            buildings[bid] = {
                "id": bid,
                "category": a.get("category", "error"),
                "international_grade": INTERNATIONAL.get(a.get("category", ""), None),
                "confidence": a.get("confidence"),
                "needs_human": a.get("needs_human", True),
                "evidence": a.get("evidence", a.get("error", "")),
                "decided_by": a.get("decided_by"),
                "demolished_before_storm": a.get("demolished", False),
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
            if bid in cost_of:
                c = cost_of[bid]
                buildings[bid]["repair_cost_usd"] = [c["cost_low_usd"], c["cost_high_usd"]]
                buildings[bid]["building_value_usd"] = c["value_usd"]
                buildings[bid]["substantial_damage"] = "likely" if c["substantial_damage_likely"] else "possible" if c["substantial_damage_possible"] else "no"
                buildings[bid]["first_floor"] = c.get("floor_basis")
            if bid in need_of:
                n = need_of[bid]
                buildings[bid]["households"] = n["households"]
                buildings[bid]["displaced"] = "likely" if n["likely_displaced"] else "possibly" if n["possibly_displaced"] else "no"
                buildings[bid]["water_inside_ft"] = n["water_inside_ft"]
                buildings[bid]["urgency"] = n["score"]
                buildings[bid]["urgency_reasons"] = n["reasons"]
        meta = {
            "post_storm_photos": summary.get("post_source") or f"NOAA emergency response flight {summary.get('noaa_flight')}",
            "pre_storm_photos": summary.get("pre_source") or f"USDA NAIP, {summary.get('naip_date')}",
            "water_surface_ft_navd88": flood.get("surface_ft_navd88"),
            "water_observations": flood.get("observations", []),
        }
        if costs:
            r = costs["rate"]
            meta["building_values"] = (
                f"{r['source']}: ${r['usd_per_sqft']:.0f}/sq ft"
                if r.get("country")
                else f"{r['source']}, {r['roll_year']} roll: median ${r['usd_per_sqft']:.0f}/sq ft from {r['homes']} homes nearby"
            )
        area = cls(name, buildings, meta)
        area.costs, area.needs = costs, needs
        return area

    # ---- tools -------------------------------------------------------------

    def area_overview(self) -> dict:
        b = list(self.buildings.values())
        counts = Counter(x["category"] for x in b)
        flooded = [x for x in b if (x["flood_ft"] or 0) > 0]
        return {
            "area": self.name,
            "buildings_assessed": len(b),
            "by_category": {c: counts.get(c, 0) for c in ORDER if counts.get(c)},
            "by_international_grade": dict(Counter(INTERNATIONAL[x["category"]] for x in b if x["category"] in INTERNATIONAL)),
            "sent_to_human_review": sum(x["needs_human"] for x in b),
            "gone_before_the_storm": [x["id"] for x in b if x["demolished_before_storm"]],
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

    def repair_costs(self, limit: int = 10) -> dict:
        """Area repair cost range and the costliest buildings."""
        if not self.costs:
            return {"error": "costs not estimated yet for this area"}
        c = self.costs
        rows = sorted(c["buildings"], key=lambda x: -x["cost_high_usd"])[: max(1, min(int(limit), 50))]
        return {
            "total_usd": [c["total_low_usd"], c["total_high_usd"]],
            "substantially_damaged_likely": c["substantial_damage_likely"],
            "substantially_damaged_possible": len(c["substantial_damage_possible"]),
            "building_values": self.meta.get("building_values"),
            "costliest": [
                {"id": x["id"], "cost_usd": [x["cost_low_usd"], x["cost_high_usd"]], "value_usd": x["value_usd"], "driver": x["driver"], "first_floor": x["floor_basis"]}
                for x in rows
            ],
            "note": c.get("note")
            or "Planning ranges from tax-roll values (market value, not replacement cost) and FEMA P-784 flood bands; the 50% substantial-damage call is the floodplain manager's.",
        }

    def urgent_needs(self, limit: int = 10) -> dict:
        """Households likely out of their homes, and who to reach first."""
        if not self.needs:
            return {"error": "needs not estimated yet for this area"}
        n = self.needs
        rows = n["buildings"][: max(1, min(int(limit), 50))]
        return {
            "households_likely_displaced": n["households_likely_displaced"],
            "households_possibly_displaced": n["households_possibly_displaced"],
            "businesses_or_lodging_hit": n.get("businesses_or_lodging_hit", []),
            "most_urgent_first": [
                {
                    "id": r["id"],
                    "category": r["category"],
                    "households": r["households"],
                    "displaced": "likely" if r["likely_displaced"] else "possibly" if r["possibly_displaced"] else "no",
                    "water_inside_ft": r["water_inside_ft"],
                    "why": r["reasons"],
                    "lat_lon": f"{self.buildings[r['id']]['lat']:.5f}, {self.buildings[r['id']]['lon']:.5f}" if r["id"] in self.buildings else None,
                }
                for r in rows
            ],
            "note": "Households come from residential units on the tax roll; water inside uses the first-floor estimate from year built and flood zone.",
        }

    # ---- plumbing ----------------------------------------------------------

    def call(self, name: str, args: dict) -> dict:
        fn = {
            "area_overview": self.area_overview,
            "find_buildings": self.find_buildings,
            "get_building": self.get_building,
            "plan_inspections": self.plan_inspections,
            "pda_totals": self.pda_totals,
            "repair_costs": self.repair_costs,
            "urgent_needs": self.urgent_needs,
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
    {
        "type": "function",
        "function": {
            "name": "repair_costs",
            "description": "Repair cost range for the area and the costliest buildings, from local tax-roll building values and FEMA flood-damage bands, with FEMA's 50% substantial-damage flags.",
            "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "urgent_needs",
            "description": "Households likely or possibly displaced, businesses and lodging hit, and the buildings to reach first with the reasons and coordinates.",
            "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}}},
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


def model_for(use_spark: bool) -> tuple[str, str | None, str]:
    if use_spark:
        return SPARK_URL, os.environ.get("META_API_KEY"), SPARK_MODEL
    return OPEN_URL, os.environ.get("BASETEN_API_KEY"), OPEN_MODEL


def run_tools(
    messages: list[dict],
    tools: list[dict],
    call,
    use_spark: bool = False,
    max_steps: int = MAX_STEPS,
    max_tokens: int = 8000,
    on_event=None,
) -> Answer:
    """The tool loop: let the model call tools until it answers in words.

    `messages` is extended in place, so a caller can keep the conversation
    going. `call(name, args)` runs a tool; `on_event(kind, **fields)` hears
    each step as it happens.
    """
    from .agent import _post

    url, key, model = model_for(use_spark)
    emit = on_event or (lambda kind, **f: None)
    steps: list[dict] = []
    for _ in range(max_steps):
        msg = _post(url, key, {"model": model, "messages": messages, "tools": tools, "max_tokens": max_tokens})["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        text = msg.get("content") or ""
        messages.append({"role": "assistant", "content": text, **({"tool_calls": calls} if calls else {})})
        if not calls:
            return Answer(text, steps, model)
        if text.strip():
            emit("thought", text=text.strip())
        for c in calls:
            name = c["function"]["name"]
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            args = args if isinstance(args, dict) else {}
            emit("tool", name=name, args=args)
            result = call(name, args)
            emit("tool_result", name=name, result=result)
            steps.append({"tool": name, "args": args})
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(result, default=str)})
    messages.append({"role": "user", "content": "Answer now with what you have; no more tools."})
    msg = _post(url, key, {"model": model, "messages": messages, "max_tokens": max_tokens})["choices"][0]["message"]
    messages.append({"role": "assistant", "content": msg.get("content") or ""})
    return Answer(msg.get("content") or "", steps, model)


def ask(area: Area, question: str, use_spark: bool = False, max_tokens: int = 8000) -> Answer:
    """One question about an assessed area."""
    messages: list[dict] = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    return run_tools(messages, TOOLS, area.call, use_spark=use_spark, max_tokens=max_tokens)


PDA_REQUEST = """Write the draft Preliminary Damage Assessment summary for this area, for the county emergency manager to forward to the state. Use markdown with these sections:

1. Headline: two sentences on what happened here and the totals.
2. Damage by category: a table of affected, minor, major and destroyed counts, with how many of each need a field check.
3. Flooding: how deep the water was, how it was measured, and why the flood-only houses may be undercounted.
4. Destroyed and major: each building by id with one line of evidence.
5. Who needs help first: households likely displaced, businesses and lodging hit, and the five most urgent buildings with why.
6. Repair cost: the area range, how many buildings are likely substantially damaged under FEMA's 50% rule, and where the values come from.
7. Field inspection plan: the route for the first team, with the number of stops and the walking distance.
8. Limits: what aerial drafts can't see, and that these are drafts for trained assessors."""
