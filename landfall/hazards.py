"""Chance of damage by type of natural disaster and its category: hurricane
Category 1 to 5, tornado EF0 to EF5, earthquake shaking from strong to
extreme, flood water 1 to 10 feet deep, plus two scenarios measured from
real inspected buildings (storm surge like Helene at Cedar Key, and a
wind-driven urban wildfire like Los Angeles in 2025).

Wind, shaking and flood use damage curves: for each kind of building, the
wind speed (or ground shaking, or water depth) at which half of such
buildings are damaged, and how quickly the chance rises around it (a
lognormal curve, the form FEMA's Hazus loss model uses). The values here are
approximate, set to the ranges in Hazus and post-storm studies. They are not
the official Hazus tables, and they have not yet been checked against this
project's own field data, so treat the numbers as rough.

"Damaged" means more than light damage (for wind, more than a few shingles:
roof cover torn, windows or doors broken); "severe" means badly damaged or
destroyed. For floods, "damaged" means water above the first floor (shown as
flooding, not counted as damage to the structure elsewhere in Landfall) and
"severe" means 4 feet or more of water inside.
"""

from __future__ import annotations

import math

# 3-second gusts at about 10 m over open ground, roughly 1.2 times the
# 1-minute sustained wind in the middle of each Saffir-Simpson category
# (tornado EF numbers are already 3-second gusts)
SCENARIOS: dict[str, dict] = {
    "hurricane": {
        "name": "Hurricane (wind)",
        "unit": "mph gust",
        "categories": [
            ("cat1", "Category 1", 100), ("cat2", "Category 2", 125), ("cat3", "Category 3", 145),
            ("cat4", "Category 4", 170), ("cat5", "Category 5", 195),
        ],
        "default": "cat3",
        "note": "wind only; storm surge near the coast adds water damage (see the storm-surge choice)",
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
        "categories": [
            ("ef0", "EF0", 75), ("ef1", "EF1", 98), ("ef2", "EF2", 123),
            ("ef3", "EF3", 150), ("ef4", "EF4", 183), ("ef5", "EF5", 215),
        ],
        "default": "ef2",
        "note": "if the tornado's path crosses the building; most buildings in a square are not in the path",
    },
    "earthquake": {
        "name": "Earthquake",
        "unit": "g peak ground acceleration",
        # Modified Mercalli shaking and a typical peak ground acceleration for it
        "categories": [
            ("mmi6", "Strong shaking (MMI VI)", 0.12), ("mmi7", "Very strong (MMI VII)", 0.22),
            ("mmi8", "Severe (MMI VIII)", 0.40), ("mmi9", "Violent (MMI IX)", 0.75), ("mmi10", "Extreme (MMI X)", 1.20),
        ],
        "default": "mmi8",
        "note": "shaking at the building, which depends on distance from the fault and the ground, not only the magnitude",
    },
    "flood": {
        "name": "Flood",
        "unit": "ft of water above the ground",
        "categories": [("ft1", "1 ft of water", 1.0), ("ft3", "3 ft", 3.0), ("ft6", "6 ft", 6.0), ("ft10", "10 ft", 10.0)],
        "default": "ft3",
        "note": "damaged here means water above the first floor (flooding); severe means 4 ft or more inside",
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

BETA_WIND, BETA_QUAKE, BETA_FLOOD = 0.22, 0.64, 0.5


def scenario_ids() -> list[str]:
    return [f"{t}:{c[0]}" for t, s in SCENARIOS.items() for c in s["categories"]]


def default_scenario(kind: str) -> str:
    t = OPEN_ON.get(kind, "hurricane")
    return f"{t}:{SCENARIOS[t]['default']}"


def menu() -> list[dict]:
    """The disaster types and their categories, for the map panel."""
    return [{"type": t, "name": s["name"], "note": s["note"], "default": s["default"],
             "categories": [{"id": c[0], "label": c[1], "intensity": c[2], "unit": s["unit"]} for c in s["categories"]]}
            for t, s in SCENARIOS.items()]


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _curve(x: float, median: float, beta: float) -> float:
    if x <= 0:
        return 0.0
    return _phi(math.log(x / median) / beta)


# ---------------------------------------------------------------- building eras by hazard


def wind_era(year: int | None, state: str | None) -> str:
    if not year:
        return "unknown"
    if state == "FL":  # code changes after Andrew (1994), Florida Building Code (2002)
        return "old" if year < 1994 else "mid" if year < 2002 else "modern"
    return "old" if year < 2000 else "modern"  # International Residential Code era


def quake_era(year: int | None, state: str | None) -> str:
    if not year:
        return "unknown"
    if state is None:  # abroad (Myanmar)
        return "old" if year < 2012 else "mid"
    return "old" if year < 1976 else "mid" if year < 1997 else "modern"


def flood_era(year: int | None) -> str:
    if not year:
        return "unknown"
    return "old" if year < 1975 else "modern"


# ---------------------------------------------------------------- damage curves


def wind_medians(material: str | None, era: str, green: str | None, stories: float | None) -> tuple[float, float]:
    """Gust (mph) at which half of such buildings are damaged / badly damaged."""
    m = (material or "").lower()
    if "manufactured" in m:
        dmg, sev = 95.0, 125.0
        up = {"old": (0, 0), "mid": (15, 18), "modern": (15, 18), "unknown": (7, 9)}[era]  # HUD wind zones, 1994
    else:
        dmg, sev = 130.0, 175.0  # one-story wood frame, older code
        if "masonry" in m or "concrete" in m or "steel" in m:
            dmg, sev = dmg + 8, sev + 12
        up = {"old": (0, 0), "mid": (10, 12), "modern": (25, 30), "unknown": (8, 10)}[era]
    dmg, sev = dmg + up[0], sev + up[1]
    if green and green.startswith("mostly trees"):
        dmg, sev = dmg - 5, sev - 3  # falling trees and limbs
    if stories and stories >= 2:
        dmg, sev = dmg - 3, sev - 3  # taller walls and roofs catch more wind
    return dmg, sev


def quake_medians(material: str | None, era: str) -> tuple[float, float]:
    """Peak ground acceleration (g) at which half of such buildings are
    damaged (moderate) / badly damaged (extensive or worse)."""
    m = (material or "").lower()
    if "manufactured" in m:
        return 0.20, 0.40  # slides off its piers
    if "masonry" in m:  # older masonry is often unreinforced
        return {"old": (0.22, 0.36), "mid": (0.35, 0.60), "modern": (0.50, 0.90), "unknown": (0.28, 0.46)}[era]
    if "concrete" in m or "steel" in m:
        return {"old": (0.25, 0.45), "mid": (0.40, 0.75), "modern": (0.55, 1.05), "unknown": (0.32, 0.60)}[era]
    return {"old": (0.45, 0.85), "mid": (0.60, 1.10), "modern": (0.75, 1.40), "unknown": (0.52, 0.95)}[era]  # wood frame


FLOOR_FT = {"slab": 1.0, "fill": 1.5, "crawlspace": 3.0, "basement": 2.0, "solid wall": 4.0, "piers": 6.0, "piles": 8.0}


def first_floor_ft(foundation: str | None, era: str) -> float:
    """Typical height of the first floor above the ground."""
    h = FLOOR_FT.get((foundation or "").lower(), 2.0)
    if era == "modern":
        h += 1.0  # built after flood-elevation rules (only binding in flood zones)
    return h


def modeled(t: str, intensity: float, b: dict, state: str | None) -> tuple[float, float]:
    """(chance damaged, chance badly damaged) for one building in one scenario."""
    y = b.get("year_built")
    if t in ("hurricane", "tornado"):
        d, s = wind_medians(b.get("material"), wind_era(y, state), b.get("green"), b.get("stories"))
        return _curve(intensity, d, BETA_WIND), _curve(intensity, s, BETA_WIND)
    if t == "earthquake":
        d, s = quake_medians(b.get("material"), quake_era(y, state))
        return _curve(intensity, d, BETA_QUAKE), _curve(intensity, s, BETA_QUAKE)
    if t == "flood":
        ff = first_floor_ft(b.get("foundation"), flood_era(y))
        return _curve(intensity, ff, BETA_FLOOD), _curve(intensity, ff + 4.0, BETA_FLOOD)
    raise ValueError(t)


def class_key(b: dict, state: str | None) -> tuple:
    """Buildings with the same key get the same odds in every scenario."""
    from .structures import code_era

    y = b.get("year_built")
    st = b.get("stories")
    return (b.get("material"), b.get("foundation"), b.get("green"), bool(st and st >= 2),
            wind_era(y, state), quake_era(y, state), flood_era(y),
            code_era(y, state, "hurricane"), code_era(y, state, "wildfire"))


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
