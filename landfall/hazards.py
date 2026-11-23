"""Chance of damage by type of natural disaster and its category: hurricane
Category 1 to 5, tornado EF0 to EF5, earthquake shaking from strong to
extreme, flood water 1 to 10 feet deep, plus two scenarios measured from
real inspected buildings (storm surge like Helene at Cedar Key, and a
wind-driven urban wildfire like Los Angeles in 2025).

Where each number comes from (tables built by scripts/build_hazards.py):
- Hurricane wind: FEMA's Hazus Hurricane Model v5.1 building fragility
  curves (as published by the NHERI SimCenter Damage and Loss Model
  Library). The roof and opening details Hazus needs (deck nailing,
  roof-to-wall connection, shutters, water barrier) come from the NHERI
  SimCenter asset representation rulesets, which assign them from year built
  and the site's ASCE 7 design wind speed (read from ASCE's hazard map).
- Tornado: the Enhanced Fujita scale's own damage indicators (NWS Storm
  Prediction Center): the wind speeds that do each degree of damage to a
  house or a manufactured home. They don't vary with age, so tornado odds
  don't either.
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
        "note": "if the tornado's path crosses the building (most buildings in a square are not in it); the EF scale doesn't tell houses apart by age",
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
    "hurricane": "FEMA Hazus Hurricane Model v5.1 building damage curves (via the NHERI SimCenter Damage and Loss Model Library), with roof details from the SimCenter rulesets by year built and the ASCE 7 design wind speed here",
    "tornado": "the Enhanced Fujita scale's damage indicators for houses and manufactured homes (NWS Storm Prediction Center)",
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
ASCE_WIND = "https://gis.asce.org/arcgis/rest/services/ASCE/wind2010_A/ImageServer/identify"
NOW_YEAR = 2026  # the rulesets treat garages on homes under 30 years old as standard


@lru_cache(maxsize=256)
def design_wind(lon: float, lat: float) -> float | None:
    """ASCE 7-10 Risk Category II ultimate design wind speed (mph) at a
    place, from ASCE's own hazard map service; None where it has no value
    (offshore, or the service is down)."""
    import requests

    try:
        r = requests.get(ASCE_WIND, params={
            "geometry": json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}}),
            "geometryType": "esriGeometryPoint", "returnGeometry": "false", "f": "json"}, timeout=20)
        v = r.json().get("value")
        return round(float(v)) if v not in (None, "NoData") else None
    except Exception:  # noqa: BLE001 - without it the rulesets average over speeds
        return None


def _code_speed(year: int, v: float) -> float:
    """The design wind speed in the terms of the code in force: from 2016
    (IRC 2015) codes use ASCE 7-10 ultimate speeds; earlier codes used ASCE
    7-05 basic speeds, about V_ult x sqrt(0.6) (ASCE 7-10, section C26.5.1).
    The rulesets' thresholds before 2016 are in those older terms."""
    return v if year >= 2016 else v * math.sqrt(0.6)


def _hpr(year: int, v: float) -> bool:
    """Hurricane-prone region (IRC 2015 from 2016, earlier IRC before)."""
    return _code_speed(year, v) > (115 if year >= 2016 else 90)


def _wbd(year: int, v: float, flood_zone: str | None) -> bool:
    """Wind-borne debris region: within the hurricane-prone region, a coastal
    flood zone with a high design speed, or a higher design speed anywhere."""
    near, anywhere = (130, 140) if year >= 2016 else (110, 120)
    coastal = flood_zone == "coastal" or (flood_zone or "").upper().startswith(("A", "V"))
    c = _code_speed(year, v)
    return _hpr(year, v) and ((coastal and c >= near) or c >= anywhere)


def _either(p: float, yes, no) -> list[tuple]:
    return [(yes, p), (no, 1 - p)] if 0 < p < 1 else [(yes if p >= 1 else no, 1.0)]


def wind_configs(mat: str, year: int, stories: int, v: float, flood_zone: str | None, terrain: str) -> list[tuple[str, float]]:
    """The Hazus wind models a building may be, with weights, by the NHERI
    SimCenter asset representation rulesets (WSF: Lake Charles testbed;
    MSF and MH: Atlantic County testbed, github.com/NHERI-SimCenter/
    AssetRepresentationRulesets). Their coin flips become weights; facts
    Landfall can't see take the rulesets' defaults (garage unknown, deck
    sheathing unknown, wood-truss roof on masonry), and roof shape is half
    gable, half hip."""
    h = "2" if stories >= 2 else "1"
    wbd = _wbd(year, v, flood_zone)
    c = _code_speed(year, v)
    out: list[tuple[str, float]] = []
    if mat == "manufactured":
        if year <= 1994:
            tag = "PHUD" if year <= 1976 else "76HUD"
            for sh, p1 in _either(0.45 if wbd else 0.0, 1, 0):
                for td, p2 in _either(0.45, 1, 0):
                    out.append((f"MH.{tag}.{sh}.{td}.{terrain}", p1 * p2))
        else:  # HUD wind zone by design speed (an approximation of the HUD zone map)
            zone = "III" if v >= 150 else "II" if v >= 130 else "I"
            out.append((f"MH.94HUD{zone}.{int(c >= 100)}.{int(c >= 70)}.{terrain}", 1.0))
        return out
    shutter_p = (1.0 if wbd else 0.0) if year > 2000 else (0.45 if wbd else 0.0)
    rda = ("8s" if c > (130 if year >= 2016 else 100) else "8d")
    for shape in ("gab", "hip"):
        if mat == "masonry":
            rwc = "strap" if _hpr(year, v) else "tnail"
            for sh, p1 in _either(shutter_p if year < 2000 else (1.0 if wbd else 0.0), 1, 0):
                for swr, p2 in _either(0.6, 1, 0):
                    out.append((f"M.SF.{h}.{shape}.{rwc}.trs.{rda}.{sh}.{swr}.no.1.null.{terrain}", 0.5 * p1 * p2))
            continue
        swr_p = 0.6 if year > 2000 else 0.0
        deck = rda if year > 2000 else "6d"
        rwc = ("strap" if _hpr(year, v) else "tnail") if year > 2015 else ("strap" if c > 110 else "tnail") if year > 1992 else "tnail"
        # garage unknown: the ruleset assigns a standard garage, which Hazus ties to no shutters
        for swr, p2 in _either(swr_p, 1, 0):
            out.append((f"W.SF.{h}.{shape}.{swr}.{deck}.{rwc}.std.0.{terrain}", 0.5 * p2))
    return out


def wind_key(b: dict, state: str | None) -> tuple:
    mat = WIND_MATERIAL.get(b.get("material") or "", "wood")  # unknown walls: wood frame, the most common
    land = "70" if (b.get("green") or "").startswith("mostly trees") else "35"
    y = b.get("year_built")
    # the rulesets only look at which side of these years a building falls
    y = None if not y else next((cut for cut in (1976, 1992, 1994, 2000, 2015) if y <= cut), 2016)
    fz = "coastal" if (b.get("flood_zone") or "").upper().startswith(("A", "V")) else None
    return (mat, y, min(int(b.get("stories") or 1), 2), b.get("v_ult"), fz, land)


@lru_cache(maxsize=4096)
def _wind_odds(key: tuple) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Chance of damage state 2+ and 3+ at each stored gust speed."""
    mat, year, stories, v, fz, land = key
    t = _table("hazus_wind.json")
    models = t["models"]
    if mat in ("concrete", "steel"):
        m = models[f"{mat}|{'light trees' if land == '70' else 'suburban'}"]
        return tuple(m[0]), tuple(m[1])
    years = [year] if year else [1960, 1990, 2010]  # unknown: an even mix of eras
    speeds = [v] if v else [110, 130, 150]  # unknown design wind: an even mix
    acc = [[0.0] * len(t["speeds_mph"]) for _ in range(2)]
    total = 0.0
    for y in years:
        for vv in speeds:
            for mid, w in wind_configs(mat, y, stories, vv, fz, land):
                m = models.get(mid)
                if m is None:
                    continue
                total += w
                for k in range(2):
                    for i, p in enumerate(m[k]):
                        acc[k][i] += w * p
    return tuple(x / total for x in acc[0]), tuple(x / total for x in acc[1])


def wind(gust: float, b: dict, state: str | None) -> tuple[float, float]:
    xs = _table("hazus_wind.json")["speeds_mph"]
    d, s = _wind_odds(wind_key(b, state))
    i = min(max(bisect.bisect_right(xs, gust) - 1, 0), len(xs) - 2)
    f = min(max((gust - xs[i]) / (xs[i + 1] - xs[i]), 0.0), 1.0)
    return d[i] + f * (d[i + 1] - d[i]), s[i] + f * (s[i + 1] - s[i])


# ---------------------------------------------------------------- tornado (Enhanced Fujita scale)

# Wind speeds for degrees of damage in the Enhanced Fujita scale's damage
# indicators (NWS Storm Prediction Center; Texas Tech, 2006): expected, lower
# and upper bound, mph. Damaged = roof deck uplift and over 20% of the roof
# cover gone (houses) or sliding off the piers (manufactured homes); severe =
# large sections of the roof structure removed / roof and walls destroyed.
EF_DAMAGE = {
    "house": {"damaged": (97, 81, 116), "severe": (122, 104, 142)},  # DI 2, one- and two-family residences
    "manufactured": {"damaged": (87, 72, 103), "severe": (105, 87, 123)},  # DI 3, single-wide manufactured homes
}


def tornado(gust: float, b: dict) -> tuple[float, float]:
    """Treats each bound as the 5th and 95th percentile of the speed that
    does that damage."""
    kind = "manufactured" if "manufactured" in (b.get("material") or "") else "house"

    def p(exp: float, lo: float, hi: float) -> float:
        beta = math.log(hi / lo) / (2 * 1.645)
        return _phi(math.log(gust / exp) / beta)

    return p(*EF_DAMAGE[kind]["damaged"]), p(*EF_DAMAGE[kind]["severe"])


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
    if t == "hurricane":
        return wind(intensity, b, state)
    if t == "tornado":
        return tornado(intensity, b)
    if t == "earthquake":
        return quake(intensity, b, state)
    if t == "flood":
        return flood(intensity, b)
    raise ValueError(t)


def class_key(b: dict, state: str | None) -> tuple:
    """Buildings with the same key get the same odds in every scenario."""
    from .structures import code_era

    y = b.get("year_built")
    return (wind_key(b, state), "manufactured" in (b.get("material") or ""), quake_model(b, state), b.get("foundation"), (b.get("stories") or 1) >= 2,
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
