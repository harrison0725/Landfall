"""Chance of damage by type of natural disaster and its category: hurricane
Category 1 to 5, tornado EF0 to EF5, earthquake shaking from strong to
extreme, flood water 1 to 10 feet deep, plus two scenarios measured from
real inspected buildings (storm surge like Helene at Cedar Key, and a
wind-driven urban wildfire like Los Angeles in 2025).

Where each number comes from (tables built by scripts/build_hazards.py):
- Wind: FEMA's Hazus Hurricane Model v5.1 building fragility curves (as
  published by the NHERI SimCenter Damage and Loss Model Library), by wall
  material, stories, building-rules era and land cover. Hazus needs roof
  details a satellite photo can't show; each era gets what its rules required
  (an assumption, written in the build script) and the rest are averaged.
  Tornadoes use the same curves at EF-scale gusts: Hazus has no tornado
  model, and tornado winds load buildings harder than hurricane winds of the
  same speed, so tornado odds are likely too low.
- Earthquake: the Hazus Earthquake Model v6.1 fragility curves in equivalent
  peak ground acceleration, by building type and seismic design level, with
  the design level from year built and state following the Hazus guidance.
  Shaking levels are turned into ground acceleration with the USGS ShakeMap
  conversion (Worden et al., 2012).
- Flood: the share of surveyed Cedar Key buildings (DesignSafe PRJ-6245)
  with the same foundation whose first floor is below the water, and for
  "severe", the depth above the floor where the Hazus Flood FIA depth-damage
  curve reaches half the building's value (FEMA's substantial damage).

"Damaged" means Hazus damage state 2 (moderate) or worse; "severe" means
damage state 3 or worse (wind) or extensive or worse (earthquake). For
floods, "damaged" means water above the first floor (flooding, which
Landfall states beside the damage category rather than counting it).
"""

from __future__ import annotations

import bisect
import json
import math
from functools import lru_cache
from pathlib import Path

HISTORY = Path(__file__).resolve().parent / "history"


def _gust(sustained_mph: float) -> int:
    """Peak 3-second gust over open ground for a 1-minute sustained wind
    (gust factor 1.22, from the Durst curve used in ASCE 7)."""
    return round(sustained_mph * 1.22)


def _pga_for_mmi(mmi: float) -> float:
    """Peak ground acceleration (g) for a shaking intensity, inverting the
    USGS ShakeMap conversion for MMI V and up (Worden et al., 2012)."""
    return round(10 ** ((mmi + 1.60) / 3.70) / 980.665, 2)


SCENARIOS: dict[str, dict] = {
    "hurricane": {
        "name": "Hurricane (wind)",
        "unit": "mph gust",
        # the middle of each Saffir-Simpson category's 1-minute wind (Category 5: 165 mph)
        "categories": [
            ("cat1", "Category 1", _gust(85)), ("cat2", "Category 2", _gust(103)), ("cat3", "Category 3", _gust(120)),
            ("cat4", "Category 4", _gust(143)), ("cat5", "Category 5", _gust(165)),
        ],
        "default": "cat3",
        "note": "wind only, at the storm's peak strength; most of a town sees less, and storm surge near the coast is separate (see storm surge)",
    },
    "surge": {
        "name": "Hurricane storm surge (coast)",
        "unit": "",
        "categories": [("helene", "Like Helene at Cedar Key (about 9 ft of surge)", None)],
        "default": "helene",
        "note": "measured: the share of look-alike buildings damaged in Cedar Key after Helene (2024)",
    },
    "tornado": {
        "name": "Tornado",
        "unit": "mph gust",
        # the middle of each Enhanced Fujita 3-second gust range (EF5: 215 mph)
        "categories": [
            ("ef0", "EF0", 75), ("ef1", "EF1", 98), ("ef2", "EF2", 123),
            ("ef3", "EF3", 150), ("ef4", "EF4", 183), ("ef5", "EF5", 215),
        ],
        "default": "ef2",
        "note": "if the tornado's path crosses the building (most buildings in a square are not in it); Hazus hurricane curves, likely too low for tornadoes",
    },
    "earthquake": {
        "name": "Earthquake",
        "unit": "g peak ground acceleration",
        "categories": [
            ("mmi6", "Strong shaking (MMI VI)", _pga_for_mmi(6)), ("mmi7", "Very strong (MMI VII)", _pga_for_mmi(7)),
            ("mmi8", "Severe (MMI VIII)", _pga_for_mmi(8)), ("mmi9", "Violent (MMI IX)", _pga_for_mmi(9)),
            ("mmi10", "Extreme (MMI X)", _pga_for_mmi(10)),
        ],
        "default": "mmi8",
        "note": "shaking at the building, which depends on distance from the fault and the ground, not only the magnitude",
    },
    "flood": {
        "name": "Flood",
        "unit": "ft of water above the ground",
        "categories": [("ft1", "1 ft of water", 1.0), ("ft3", "3 ft", 3.0), ("ft6", "6 ft", 6.0), ("ft10", "10 ft", 10.0)],
        "default": "ft3",
        "note": "damaged here means water above the first floor (flooding); severe means repairs over half the building's value",
    },
    "wildfire": {
        "name": "Wildfire",
        "unit": "",
        "categories": [("la2025", "Wind-driven fire reaching the streets (like Los Angeles, 2025)", None)],
        "default": "la2025",
        "note": "measured: the share of look-alike buildings damaged in the 2025 Eaton and Palisades fires",
    },
}
# the past disaster each measured scenario's buildings come from (odds.HISTORIES key)
MEASURED = {"surge": "hurricane", "wildfire": "wildfire"}
# which scenario a square opens on, by the disaster picked in the menu
OPEN_ON = {"hurricane": "hurricane", "wildfire": "wildfire", "flood": "flood", "earthquake": "earthquake", "tornado": "tornado"}
SOURCES = {
    "hurricane": "FEMA Hazus Hurricane Model v5.1 building damage curves (via the NHERI SimCenter Damage and Loss Model Library), with roof details assumed from each era's building rules",
    "tornado": "FEMA Hazus Hurricane Model v5.1 building damage curves at tornado wind speeds (Hazus has no tornado model, so likely too low)",
    "earthquake": "FEMA Hazus Earthquake Model v6.1 building damage curves (via the NHERI SimCenter Damage and Loss Model Library), shaking converted with USGS ShakeMap (Worden et al., 2012)",
    "flood": "first-floor heights of surveyed Cedar Key buildings (DesignSafe PRJ-6245) and FEMA Hazus Flood FIA depth-damage curves",
}


def scenario_ids() -> list[str]:
    return [f"{t}:{c[0]}" for t, s in SCENARIOS.items() for c in s["categories"]]


def default_scenario(kind: str) -> str:
    t = OPEN_ON.get(kind, "hurricane")
    return f"{t}:{SCENARIOS[t]['default']}"


def menu() -> list[dict]:
    """The disaster types and their categories, for the map panel."""
    return [{"type": t, "name": s["name"], "note": s["note"], "default": s["default"], "source": SOURCES.get(t),
             "categories": [{"id": c[0], "label": c[1], "intensity": c[2], "unit": s["unit"]} for c in s["categories"]]}
            for t, s in SCENARIOS.items()]


@lru_cache(maxsize=4)
def _table(name: str) -> dict:
    return json.loads((HISTORY / name).read_text())


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


# ---------------------------------------------------------------- wind (Hazus hurricane)

WIND_MATERIAL = {"wood frame": "wood", "masonry": "masonry", "concrete": "concrete", "steel": "steel", "manufactured (mobile) home": "manufactured"}


def wind_era(year: int | None, state: str | None, material: str = "wood") -> str:
    if material in ("concrete", "steel"):
        return "any"
    if not year:
        return "unknown"
    if material == "manufactured":  # HUD standards, 1976 and 1994
        return "phud" if year < 1976 else "hud76" if year < 1994 else "hud94_2" if state == "FL" else "hud94_1"
    if state == "FL":  # post-Andrew changes (1994), Florida Building Code (2002)
        return "old" if year < 1994 else "mid" if year < 2002 else "modern_fl"
    return "old" if year < 2000 else "modern"  # International Residential Code era


def wind_key(b: dict, state: str | None) -> str:
    mat = WIND_MATERIAL.get(b.get("material") or "", "wood")  # unknown walls: wood frame, the most common
    st = b.get("stories")
    h = "2" if mat in ("wood", "masonry") and st and st >= 2 else "1"
    land = "light trees" if (b.get("green") or "").startswith("mostly trees") else "suburban"
    return f"{mat}|{h}|{wind_era(b.get('year_built'), state, mat)}|{land}"


def wind(gust: float, b: dict, state: str | None) -> tuple[float, float]:
    t = _table("hazus_wind.json")
    c, xs = t["curves"][wind_key(b, state)], t["speeds_mph"]
    i = min(max(bisect.bisect_right(xs, gust) - 1, 0), len(xs) - 2)
    f = min(max((gust - xs[i]) / (xs[i + 1] - xs[i]), 0.0), 1.0)
    return tuple(c[k][i] + f * (c[k][i + 1] - c[k][i]) for k in ("damaged", "severe"))


# ---------------------------------------------------------------- earthquake (Hazus)

# seismic design level by region and year built, after the Hazus guidance
# (high: the western states of UBC zones 3-4; moderate: New Madrid and Charleston)
HIGH_SEISMIC = {"CA", "AK", "WA", "OR", "NV", "UT", "HI", "ID", "MT", "WY"}
MODERATE_SEISMIC = {"SC", "TN", "MO", "AR", "KY"}


def design_level(year: int | None, state: str | None) -> str:
    if state is None:  # abroad: Myanmar's first seismic code, 2012
        return "LC" if year and year >= 2012 else "PC"
    y = year or 1960  # unknown: the middle era
    if state in HIGH_SEISMIC:
        return "PC" if y < 1941 else "MC" if y < 1976 else "HC"
    if state in MODERATE_SEISMIC:
        return "PC" if y < 1941 else "LC" if y < 1976 else "MC"
    return "PC" if y < 1941 else "LC"


def quake_model(b: dict, state: str | None) -> str:
    lvl = design_level(b.get("year_built"), state)
    m = (b.get("material") or "").lower()
    if "manufactured" in m:
        return f"MH.{lvl}"
    if "masonry" in m:  # assumed unreinforced when built before any seismic code
        return "URM.L.PC" if lvl == "PC" else f"RM1.L.{lvl}"
    if "concrete" in m:
        return f"C2.L.{lvl}"
    if "steel" in m:
        return f"S1.L.{lvl}"
    return f"W1.{lvl}"


def quake(pga: float, b: dict, state: str | None) -> tuple[float, float]:
    m = _table("hazus_quake.json")["models"][quake_model(b, state)]
    p = lambda ls: _phi(math.log(pga / m[ls][0]) / m[ls][1])  # noqa: E731
    return p("ls2"), p("ls3")


# ---------------------------------------------------------------- flood


def flood(depth_ft: float, b: dict) -> tuple[float, float]:
    t = _table("flood_floors.json")
    floors = t["first_floor_ft"].get(b.get("foundation") or "", t["first_floor_ft"]["any"])
    half = t["half_value_ft_above_floor"]["two_floors" if (b.get("stories") or 1) >= 2 else "one_floor"]
    share = lambda d: bisect.bisect_left(floors, d) / len(floors)  # noqa: E731 - floors below the water
    return share(depth_ft), share(depth_ft - half)


def modeled(t: str, intensity: float, b: dict, state: str | None) -> tuple[float, float]:
    """(chance damaged, chance badly damaged) for one building in one scenario."""
    if t in ("hurricane", "tornado"):
        return wind(intensity, b, state)
    if t == "earthquake":
        return quake(intensity, b, state)
    if t == "flood":
        return flood(intensity, b)
    raise ValueError(t)


def class_key(b: dict, state: str | None) -> tuple:
    """Buildings with the same key get the same odds in every scenario."""
    from .structures import code_era

    y = b.get("year_built")
    return (wind_key(b, state), quake_model(b, state), b.get("foundation"), (b.get("stories") or 1) >= 2,
            b.get("material"), b.get("green"), code_era(y, state, "hurricane"), code_era(y, state, "wildfire"))


def odds_table(buildings: list[dict], state: str | None) -> tuple[list[dict], list[int]]:
    """Every scenario's odds for each kind of building in the square.

    Returns (classes, index): classes[i] = {"odds": {scenario: [damaged,
    severe]}, ...}; index[n] is building n's class. Measured scenarios come
    from look-alikes in landfall/history, with their count and 90% range."""
    from .odds import history, lookup

    keys: dict[tuple, int] = {}
    members: list[dict] = []
    index = []
    for b in buildings:
        k = class_key(b, state)
        if k not in keys:
            keys[k] = len(members)
            members.append(b)
        index.append(keys[k])
    classes = []
    for b in members:
        odds: dict[str, list] = {}
        extra: dict[str, dict] = {}
        for t, s in SCENARIOS.items():
            for cid, _label, x in s["categories"]:
                sid = f"{t}:{cid}"
                if t in MEASURED:
                    kind = MEASURED[t]
                    past = history(kind)
                    if not past:
                        odds[sid] = [None, None]
                        continue
                    r = lookup(b, past, state, kind)
                    odds[sid] = [r["chance_damaged"], r["chance_badly_damaged_or_destroyed"]]
                    extra[sid] = [*r["chance_damaged_90pct"], r["look_alikes"]]  # 90% range and how many look-alikes
                else:
                    d, sv = modeled(t, x, b, state)
                    odds[sid] = [round(d, 2), round(sv, 2)]
        classes.append({"odds": odds, "measured": extra})
    return classes, index


def by_category(buildings: list[dict], classes: list[dict], index: list[int], t: str) -> list[dict]:
    """Average chance in the square for every category of one disaster type."""
    out = []
    for cid, label, _x in SCENARIOS[t]["categories"]:
        sid = f"{t}:{cid}"
        vals = [classes[i]["odds"][sid] for i in index if classes[i]["odds"][sid][0] is not None]
        if not vals:
            out.append({"category": label, "average_chance_damaged": None})
            continue
        out.append({
            "category": label,
            "average_chance_damaged": round(sum(v[0] for v in vals) / len(vals), 2),
            "average_chance_badly_damaged_or_destroyed": round(sum(v[1] for v in vals) / len(vals), 2),
            "expected_damaged": round(sum(v[0] for v in vals)),
        })
    return out
