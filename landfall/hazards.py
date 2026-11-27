"""Chance of damage, and expected repair cost as a share of the building's
value, by type of natural disaster and its category: hurricane
Category 1 to 5 (wind and storm surge together), tornado EF0 to EF5, earthquake shaking from strong to
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
- Hurricane wind inland: the Kaplan and DeMaria (1995) inland decay model
  (J. Applied Meteorology 34: 2499-2512), from the square's distance to the
  nearest Gulf or Atlantic coast (Natural Earth coastline).
- Hurricane storm surge: NOAA National Hurricane Center Storm Surge Risk
  Maps (SLOSH "maximum of maximums" at high tide, one map per category): the
  live 10 m map for whether and in which band water reaches each building,
  and the national data (history/surge, built by scripts/build_surge.py) for
  its depth in feet. Combined with wind by the Hazus hurricane rule for
  wind plus flood losses.
- Tornado: the Enhanced Fujita scale's own damage indicators (NWS Storm
  Prediction Center): the wind speeds that do each degree of damage to a
  house or a manufactured home. They don't vary with age, so tornado odds
  don't either.
- Earthquake: the Hazus Earthquake Model v6.1 fragility curves in equivalent
  peak ground acceleration, by building type and seismic design level, with
  the design level from year built and state following the Hazus guidance.
  Shaking levels are turned into ground acceleration with the USGS ShakeMap
  conversion (Worden et al., 2012).
- Water (flood and surge): first-floor heights of surveyed Cedar Key
  buildings (DesignSafe PRJ-6245) with the same foundation give how deep the
  water is above the floor; damage to the building itself (walls, floors,
  foundation) at that depth is the share measured in the same survey after
  Helene, V zones apart. Flooding (water above the first floor) is given as
  its own number and is not counted as damage.
- Expected repair cost: Hazus Hurricane v5.1 loss functions (wind), the
  Hazus Earthquake v6.1 repair cost ratios for houses (LF.RES1) and the
  Hazus Flood FIA depth-damage curves (A or V zone). The EF scale has no
  repair costs, so tornadoes have none.

"Damaged" means Hazus damage state 2 (moderate) or worse; "severe" means
damage state 3 or worse (wind) or extensive or worse (earthquake); for
water, survey damage state 2+ and 4+ of walls, floors or foundation.
"""

from __future__ import annotations

import bisect
import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np

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
        "name": "Hurricane (wind + storm surge)",
        "unit": "mph gust",
        # the middle of each Saffir-Simpson category's 1-minute wind (Category 5: 165 mph)
        "categories": [
            ("cat1", "Category 1", _gust(85)), ("cat2", "Category 2", _gust(103)), ("cat3", "Category 3", _gust(120)),
            ("cat4", "Category 4", _gust(143)), ("cat5", "Category 5", _gust(165)),
        ],
        "default": "cat3",
        "note": "if the eye comes ashore at the nearest coast and heads straight here; wind weakens inland and the surge is the worst case for that category at high tide",
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
        "note": "damage means harm to the walls, floors or foundation as measured after Helene (A zones, where waves were small); water inside is shown as flooding, not damage",
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
    "hurricane": "FEMA Hazus Hurricane Model v5.1 building damage and repair cost curves (via the NHERI SimCenter Damage and Loss Model Library), with roof details from the SimCenter rulesets by year built and the ASCE 7 design wind speed here; wind weakening inland from Kaplan and DeMaria (1995); storm surge depth from the National Hurricane Center's Storm Surge Risk Maps, water damage measured after Helene (DesignSafe PRJ-6245), with Hazus's rule for combining wind and flood losses",
    "tornado": "the Enhanced Fujita scale's damage indicators for houses and manufactured homes (NWS Storm Prediction Center)",
    "earthquake": "FEMA Hazus Earthquake Model v6.1 building damage curves (via the NHERI SimCenter Damage and Loss Model Library), shaking converted with USGS ShakeMap (Worden et al., 2012)",
    "flood": "first-floor heights and measured wall, floor and foundation damage of surveyed Cedar Key buildings after Helene (DesignSafe PRJ-6245), and FEMA Hazus Flood FIA depth-damage curves for repair cost",
    "earthquake_loss": "Hazus v6.1 repair cost ratios for houses",
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


@lru_cache(maxsize=8)
def _table(name: str) -> dict:
    return json.loads((HISTORY / name).read_text())


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _interp(xs: list[float], ps: list[float], x: float) -> float:
    if x <= xs[0]:
        return ps[0]
    if x >= xs[-1]:
        return ps[-1]
    i = bisect.bisect_right(xs, x) - 1
    return ps[i] + (x - xs[i]) / (xs[i + 1] - xs[i]) * (ps[i + 1] - ps[i])


# ---------------------------------------------------------------- hurricane wind inland

# Kaplan and DeMaria (1995), "A simple empirical model for predicting the
# decay of tropical cyclone winds after landfall", J. Appl. Meteor. 34:
# V(t) = Vb + (R * V0 - Vb) * exp(-alpha * t), maximum 1-minute wind in knots,
# t hours after landfall. R is the drop from sea to land at the coast.
KD_VB_KT, KD_R, KD_ALPHA = 26.7, 0.9, 0.095
# forward speed over land: the median of 174 US hurricane landfalls since 1950 (NHC best tracks,
# scripts/forward_speed.py); the range shown beside it is their 10th and 90th percentiles
FORWARD_MPH = 14.0
SLOW_MPH, FAST_MPH = 6.0, 28.0
MPH_PER_KT = 1.15078


@lru_cache(maxsize=1)
def _coast():
    from shapely.geometry import MultiLineString

    return MultiLineString(_table("coast.json")["lines"])


@lru_cache(maxsize=512)
def coast_miles(lon: float, lat: float) -> float:
    """Miles from a point to the nearest Gulf or Atlantic coast (0 offshore
    or on the beach)."""
    from shapely import affinity
    from shapely.geometry import Point

    kx = 69.17 * math.cos(math.radians(lat))  # miles per degree of longitude here
    c = affinity.scale(_coast(), xfact=kx, yfact=69.17, origin=(0, 0))
    return round(c.distance(Point(lon * kx, lat * 69.17)), 1)


def inland_gust(landfall_gust: float, miles: float) -> int:
    """Gust here when a storm with this peak gust at landfall travels this
    far inland (Kaplan and DeMaria 1995; gust factor 1.22 both ways)."""
    v0 = landfall_gust / 1.22 / MPH_PER_KT
    t = max(miles, 0.0) / FORWARD_MPH
    v = KD_VB_KT + (KD_R * v0 - KD_VB_KT) * math.exp(-KD_ALPHA * t)
    return round(v * MPH_PER_KT * 1.22)


# ---------------------------------------------------------------- hurricane storm surge (NHC)

NHC_SURGE = "https://mapservices.weather.noaa.gov/tropical/rest/services/tropical/StormSurgeRisk/MapServer/export"
SURGE_LAYERS = {"cat1": 5, "cat2": 9, "cat3": 13, "cat4": 17, "cat5": 21}  # US Gulf and East coast maps
# the maps' legend: water above the ground, ft; band 4 ("greater than 9 ft")
# is taken as 9 to 12 ft, so deeper surge is undercounted
SURGE_COLORS = {(0, 112, 255): 1, (255, 255, 115): 2, (255, 170, 0): 3, (230, 0, 0): 4, (156, 156, 156): 0}
SURGE_BANDS = {0: "none", 1: "up to 3 ft", 2: "3 to 6 ft", 3: "6 to 9 ft", 4: "over 9 ft"}
SURGE_INLAND_MI = 50  # farther from the coast than this, the maps have no surge


def surge_bands(bbox: tuple[float, float, float, float], points: list[tuple[float, float]]) -> list[tuple[int, ...]] | None:
    """For each point, the surge band (0 to 4) in a Category 1 to 5 storm,
    from the National Hurricane Center's Storm Surge Risk Maps; None when
    the maps can't be read."""
    import io

    import numpy as np
    import requests
    from PIL import Image

    x0, y0, x1, y1 = bbox
    pad = 0.002
    x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
    w = min(1200, max(100, round((x1 - x0) * 111000 * math.cos(math.radians(y0)) / 10)))  # about 10 m a pixel
    h = min(1200, max(100, round((y1 - y0) * 111000 / 10)))
    palette = np.array(list(SURGE_COLORS))
    bands = np.array(list(SURGE_COLORS.values()))
    per_cat = []
    for cid, layer in SURGE_LAYERS.items():
        try:
            r = requests.get(NHC_SURGE, params={
                "bbox": f"{x0},{y0},{x1},{y1}", "bboxSR": 4326, "imageSR": 4326, "size": f"{w},{h}",
                "layers": f"show:{layer}", "format": "png32", "transparent": "true", "f": "image"}, timeout=60)
            im = np.array(Image.open(io.BytesIO(r.content)).convert("RGBA")).astype(int)
        except Exception:  # noqa: BLE001
            return None
        rgb, alpha = im[..., :3], im[..., 3]
        dist = ((rgb[..., None, :] - palette[None, None]) ** 2).sum(-1)
        band = np.where((alpha > 0) & (dist.min(-1) < 900), bands[dist.argmin(-1)], 0)
        # a building's own pixel can be a gap in the map (its footprint); take the deepest within ~15 m
        from numpy.lib.stride_tricks import sliding_window_view

        padded = np.pad(band, 1)
        band = sliding_window_view(padded, (3, 3)).max(axis=(-1, -2))
        vals = []
        for lon, lat in points:
            px = min(max(int((lon - x0) / (x1 - x0) * w), 0), w - 1)
            py = min(max(int((y1 - lat) / (y1 - y0) * h), 0), h - 1)
            vals.append(int(band[py, px]))
        per_cat.append(vals)
    return [tuple(v) for v in zip(*per_cat)]


BAND_FT = {1: (1, 3), 2: (3, 6), 3: (6, 9), 4: (9, 40)}  # the map's bands, in the data's whole feet


@lru_cache(maxsize=5)
def _surge_data(cid: str):
    p = HISTORY / "surge" / f"{cid}.npz"
    return np.load(p) if p.exists() else None


def surge_depth(cid: str, lon: float, lat: float, band: int | None) -> int:
    """Water above the ground (whole ft) at a point in a Category 1 to 5
    storm. band is the live 10 m map's band there (None if unread): it says
    whether water reaches the building; the 160 m depth data says how deep,
    kept inside that band."""
    d = _surge_data(cid)
    depth = 0
    if d is not None:
        res = float(d["res"][0])
        key = f"t{math.floor(lon)}_{math.floor(lat)}"
        if key in d.files:
            a = d[key]
            n = a.shape[0]
            r = min(n - 1, int((math.floor(lat) + 1 - lat) / res))
            c = min(n - 1, int((lon - math.floor(lon)) / res))
            win = a[max(r - 1, 0):r + 2, max(c - 1, 0):c + 2]
            win = win[(win > 0) & (win < 99)]  # 99: behind a levee
            depth = int(a[r, c]) if 0 < a[r, c] < 99 else (int(win.max()) if band and win.size else 0)
    if band is None:
        return depth
    if not band:
        return 0
    lo, hi = BAND_FT[band]
    return min(max(depth, lo), hi) if depth else (lo + hi) // 2 if band < 4 else 10


def site(bbox: tuple[float, float, float, float]) -> dict:
    """The hurricane facts of a square: miles to the coast and the gust here
    in each category if the eye comes ashore at the nearest coast."""
    lon, lat = round((bbox[0] + bbox[2]) / 2, 3), round((bbox[1] + bbox[3]) / 2, 3)
    try:
        mi = coast_miles(lon, lat)
    except Exception:  # noqa: BLE001
        mi = 0.0
    gusts = {cid: inland_gust(g, mi) for cid, _l, g in SCENARIOS["hurricane"]["categories"]}
    # the same storm moving slowly or fast (the 10th and 90th percentile of US landfalls):
    # inland this changes the odds a lot (eval/assumption_checks.md), so the panel shows it
    global FORWARD_MPH
    base = FORWARD_MPH
    try:
        FORWARD_MPH = SLOW_MPH
        slow = {cid: inland_gust(g, mi) for cid, _l, g in SCENARIOS["hurricane"]["categories"]}
        FORWARD_MPH = FAST_MPH
        fast = {cid: inland_gust(g, mi) for cid, _l, g in SCENARIOS["hurricane"]["categories"]}
    finally:
        FORWARD_MPH = base
    return {"coast_miles": mi, "gusts": gusts, "forward_mph": FORWARD_MPH, "gusts_slow": slow, "gusts_fast": fast,
            "slow_mph": SLOW_MPH, "fast_mph": FAST_MPH,
            "surge_checked": mi <= SURGE_INLAND_MI}


# ---------------------------------------------------------------- unknown year built

def _decades(state: str | None) -> dict[int, float]:
    """Share of homes built in each decade (1930 = 1939 or earlier, 2020 = 2020 on), in the
    state, else the US: Census ACS 2019-2023, table B25034 (scripts/build_housing_age.py)."""
    t = _table("housing_age.json")
    return {int(k): v for k, v in (t.get(state or "") or t["US"]).items()}


def _years_share(dec: dict[int, float], lo: int, hi: int) -> float:
    """Share built from lo to hi (inclusive), spreading each decade evenly over its years."""
    tot = 0.0
    for d, w in dec.items():
        a, b = (1900 if d == 1930 else d), (2025 if d == 2020 else d + 9)  # 1939-or-earlier counted as one block
        overlap = max(0, min(b, hi) - max(a, lo) + 1)
        tot += w * overlap / (b - a + 1)
    return tot


@lru_cache(maxsize=64)
def year_weights(state: str | None) -> tuple[tuple[int, float], ...]:
    """For a building whose year built is unknown: (year, weight) on each side of the years the
    wind rulesets look at, weighted by when the state's homes were built."""
    dec = _decades(state)
    bins = [(1976, 1800, 1976), (1992, 1977, 1992), (1994, 1993, 1994), (2000, 1995, 2000), (2015, 2001, 2015), (2016, 2016, 2100)]
    return tuple((y, _years_share(dec, lo, hi)) for y, lo, hi in bins)


@lru_cache(maxsize=64)
def era_weights(state: str | None) -> tuple[tuple[int, float], ...]:
    """(representative year, weight) for the calibration's build eras: before 1976, 1976-2001, 2002 on."""
    dec = _decades(state)
    return ((1960, _years_share(dec, 1800, 1975)), (1990, _years_share(dec, 1976, 2001)), (2010, _years_share(dec, 2002, 2100)))


# ---------------------------------------------------------------- wind (Hazus hurricane)

WIND_MATERIAL = {"wood frame": "wood", "masonry": "masonry", "concrete": "concrete", "steel": "steel", "manufactured (mobile) home": "manufactured"}
ASCE_WIND = "https://gis.asce.org/arcgis/rest/services/ASCE/wind2010_A/ImageServer/identify"
# ASCE 7-10 Risk Category II design wind across the US interior (Fig. 26.5-1A), used only when
# ASCE's map service can't be reached and the place is this far from the Gulf or Atlantic coast
INLAND_DESIGN_MPH, INLAND_DESIGN_MI = 115, 150
NOW_YEAR = __import__("datetime").date.today().year  # the rulesets treat garages on homes under 30 years old as standard


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
    gable, half hip (no national count of roof shapes exists, and the two
    are the common shapes, so each gets an even weight; the rulesets
    themselves draw roof shape from local data where it exists)."""
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
    mat = WIND_MATERIAL.get(b.get("material") or "", "wood")  # unknown walls: wood frame (about 93% of new US single-family homes, Census Survey of Construction; NSI usually gives the walls)
    land = "70" if (b.get("green") or "").startswith("mostly trees") else "35"
    y = b.get("year_built")
    # the rulesets only look at which side of these years a building falls
    y = None if not y else next((cut for cut in (1976, 1992, 1994, 2000, 2015) if y <= cut), 2016)
    fz = "coastal" if (b.get("flood_zone") or "").upper().startswith(("A", "V")) else None
    return (mat, y, min(int(b.get("stories") or 1), 2), b.get("v_ult"), fz, land, None if y else state)


@lru_cache(maxsize=4096)
def _wind_odds(key: tuple) -> tuple[tuple[float, ...], ...]:
    """Chance of damage state 2+ and 3+, and expected repair cost as a share
    of the building's value, at each stored gust speed."""
    mat, year, stories, v, fz, land, state = key
    t = _table("hazus_wind.json")
    models = t["models"]
    if mat in ("concrete", "steel"):
        m = models[f"{mat}|{'light trees' if land == '70' else 'suburban'}"]
        return tuple(m[0]), tuple(m[1]), tuple(m[2])
    # unknown year built: the state's homes by when they were built (Census ACS)
    years = [(year, 1.0)] if year else list(year_weights(state))
    # unknown design wind (ASCE's map service down near the coast): an even mix over the coastal range
    speeds = [v] if v else [110, 130, 150]
    acc = [[0.0] * len(t["speeds_mph"]) for _ in range(3)]
    total = 0.0
    for y, wy in years:
        for vv in speeds:
            for mid, w in wind_configs(mat, y, stories, vv, fz, land):
                m = models.get(mid)
                if m is None:
                    continue
                total += w * wy
                for k in range(3):
                    for i, p in enumerate(m[k]):
                        acc[k][i] += w * wy * p
    return tuple(tuple(x / total for x in row) for row in acc)


# Below 100 mph the calibration fades to Hazus, gone at 70 mph: the surveys' lowest wind band
# (80-90 mph) found 0-3% of roofs damaged (Cedar Key 9 of 342, Harvey 0 of 42), below the fitted
# curve's 7-12%, and storms this weak are common, so an overestimate here adds up over the years
CAL_FROM_MPH = 100.0
CAL_ZERO_MPH = 70.0


def _recal(params: list[float], p: float, year: int | None, state: str | None = None) -> float:
    """Hazus odds adjusted to what surveys found (landfall/history/
    calibration.json; scripts/research_eval.py): logit(p) = a + b logit(p) +
    c [built 2002+] + d [built 1976-2001]. Unknown year built: averaged over
    the build eras of the state's homes (Census ACS)."""
    a, b, c, d = params
    q = min(max(p, 1e-4), 1 - 1e-4)
    if not year:
        return sum(w * _recal(params, p, y) for y, w in era_weights(state))
    z = a + b * math.log(q / (1 - q)) + c * (year >= 2002) + d * (1976 <= year < 2002)
    return 1 / (1 + math.exp(-z))


USE_CALIBRATION = True  # scripts/research_eval.py turns it off to score Landfall as it was


def calibration() -> dict | None:
    if not USE_CALIBRATION:
        return None
    try:
        return _table("calibration.json")
    except FileNotFoundError:
        return None


def wind(gust: float, b: dict, state: str | None) -> tuple[float, float, float]:
    """(chance damaged, chance severe, expected repair cost / value).
    Hazus wind odds, recalibrated against roof damage surveyed after six
    hurricanes: Hazus says almost no damage below about 110 mph gusts, while
    surveys found 10-45% of roofs damaged there, and it is too sure at
    the top end. Repair cost follows the recalibrated odds (see
    _matching_loss)."""
    xs = _table("hazus_wind.json")["speeds_mph"]
    rows = _wind_odds(wind_key(b, state))
    d, s, loss = (_interp(xs, list(row), gust) for row in rows)
    cal = calibration()
    if cal and gust > CAL_ZERO_MPH:
        y = b.get("year_built")
        cd, cs = _recal(cal["wind"]["damaged"], d, y, state), _recal(cal["wind"]["severe"], s, y, state)
        fade = min(1.0, (gust - CAL_ZERO_MPH) / (CAL_FROM_MPH - CAL_ZERO_MPH))
        d, s = d + fade * (cd - d), s + fade * (cs - s)
        s = min(s, d)
        loss = _matching_loss(xs, rows, d, loss)
    return d, s, loss


def _matching_loss(xs: list[float], rows: tuple, d: float, loss: float) -> float:
    """Expected repair cost / value that goes with the recalibrated chance of
    damage: Hazus's own repair cost at the gust where Hazus gives that
    chance. Below Hazus's lowest stored gust it shrinks in proportion."""
    dr, lr = list(rows[0]), list(rows[2])
    if d <= dr[0]:
        return lr[0] * d / dr[0] if dr[0] > 0 else loss
    for i in range(1, len(xs)):
        if dr[i] >= d:
            lo, hi = dr[i - 1], dr[i]
            return _interp(xs, lr, xs[i - 1] + (xs[i] - xs[i - 1]) * ((d - lo) / (hi - lo) if hi > lo else 1.0))
    return lr[-1]


def combine_losses(wind_loss: float, flood_loss: float) -> float:
    """Hazus Hurricane's combined wind and flood loss (its 11 x 11 table,
    read between the steps); it counts damage both can do only once."""
    t = _table("hazus_wind.json")["combine_wind_flood"]
    w, f = min(max(wind_loss, 0.0), 1.0) * 10, min(max(flood_loss, 0.0), 1.0) * 10
    i, j = min(int(w), 9), min(int(f), 9)
    a, c = w - i, f - j
    return ((1 - a) * (1 - c) * t[i][j] + a * (1 - c) * t[i + 1][j] + (1 - a) * c * t[i][j + 1] + a * c * t[i + 1][j + 1])


def hurricane(gust: float, depth: float, b: dict, state: str | None) -> tuple[float, float, float, float]:
    """Wind and storm surge together. Damaged: wind damage state 2+, or
    walls, floors or foundation damaged by the water (as measured after
    Helene); severe likewise. Treated as independent, which is roughly
    right because roofs fail from wind and walls and foundations from water.
    The last number is the chance of water above the first floor."""
    wd, ws, wl = wind(gust, b, state)
    if not depth:
        return wd, ws, wl, 0.0
    fd, fs, fl, wet = flood_at([depth], b, surge=True, state=state)
    return 1 - (1 - wd) * (1 - fd), 1 - (1 - ws) * (1 - fs), combine_losses(wl, fl), wet


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

# Seismic design level, after the Hazus guidance (high: the old UBC zones 3-4; moderate: zone
# 2B; low elsewhere), by year built. Where the place is known, the zone comes from the ASCE 7-16
# seismic design category there (USGS design maps service, site class D, risk category II):
# category D or above covers the old zones 3 and 4 and category C about zone 2 (FEMA P-749,
# section 4); otherwise from the state, as a fallback.
HIGH_SEISMIC = {"CA", "AK", "WA", "OR", "NV", "UT", "HI", "ID", "MT", "WY"}
MODERATE_SEISMIC = {"SC", "TN", "MO", "AR", "KY"}
USGS_DESIGN = "https://earthquake.usgs.gov/ws/designmaps/asce7-16.json"


@lru_cache(maxsize=256)
def seismic_level(lon: float, lat: float) -> str | None:
    """"high", "moderate" or "low" from the ASCE 7-16 short-period design acceleration SDS at a
    place (seismic design category D+ at SDS >= 0.50 g, C at 0.33-0.50, A-B below); None if the
    service can't be reached."""
    import requests

    try:
        r = requests.get(USGS_DESIGN, params={"latitude": lat, "longitude": lon, "riskCategory": "II", "siteClass": "D",
                                              "title": "landfall"}, timeout=20)
        sds = r.json()["response"]["data"]["sds"]
    except Exception:  # noqa: BLE001
        return None
    if sds is None:
        return None
    return "high" if sds >= 0.50 else "moderate" if sds >= 0.33 else "low"


def design_level(year: int | None, state: str | None, seismic: str | None = None) -> str:
    if state is None:  # abroad: Myanmar's first seismic code, 2012
        return "LC" if year and year >= 2012 else "PC"
    zone = seismic or ("high" if state in HIGH_SEISMIC else "moderate" if state in MODERATE_SEISMIC else "low")
    y = year or 1960
    if zone == "high":
        return "PC" if y < 1941 else "MC" if y < 1976 else "HC"
    if zone == "moderate":
        return "PC" if y < 1941 else "LC" if y < 1976 else "MC"
    return "PC" if y < 1941 else "LC"


def quake_model(b: dict, state: str | None) -> str:
    lvl = design_level(b.get("year_built"), state, b.get("seismic"))
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


def quake(pga: float, b: dict, state: str | None) -> tuple[float, float, float]:
    """Unknown year built (US): averaged over the eras the design levels turn on (before 1941,
    1941-1975, 1976 on), weighted by when the state's homes were built (Census ACS)."""
    if not b.get("year_built") and state:
        dec = _decades(state)
        parts = [(1930, _years_share(dec, 1800, 1940)), (1960, _years_share(dec, 1941, 1975)), (1990, _years_share(dec, 1976, 2100))]
        got = [quake(pga, {**b, "year_built": y}, state) for y, _w in parts]
        return tuple(sum(w * g[k] for (_y, w), g in zip(parts, got)) for k in range(3))
    t = _table("hazus_quake.json")
    m = t["models"][quake_model(b, state)]
    p = [_phi(math.log(pga / m[f"ls{k}"][0]) / m[f"ls{k}"][1]) for k in range(1, 5)] + [0.0]
    # expected repair cost: chance of being in each damage state x its Hazus repair cost ratio
    loss = sum((p[k] - p[k + 1]) * t["loss_ratio_by_damage_state"][k] for k in range(4))
    return p[1], p[2], loss


# ---------------------------------------------------------------- flood


def flood_at(depths: list[float], b: dict, surge: bool = False, state: str | None = None) -> tuple[float, float, float, float]:
    """Averaged over the water depths (ft above the ground) and over the
    first-floor heights surveyed for this kind of foundation:
    (chance damaged, chance severe, expected repair cost / value, chance of
    water above the first floor). Damage is to walls, floors or foundation
    at that much water above the floor, as measured after Helene; surge in
    a V zone uses the V zone rates (waves), everything else the rest; with
    the calibration, also by when it was built.
    Repair cost is the FIA curve (V zone curve in a V zone), which includes
    repairs inside."""
    t = _table("flood_floors.json")
    cal = calibration()
    all_floors = cal["first_floor_ft"] if cal else t["first_floor_ft"]
    floors = all_floors.get(b.get("foundation") or "") or all_floors["any"]
    two = (b.get("stories") or 1) >= 2
    v = (b.get("flood_zone") or "").upper().startswith("V")
    xs, ps = t["fia_structure_loss"][("two_floors" if two else "one_floor") + ("_v" if v else "")]
    wd = t["water_damage"]["V" if (v and surge) else "other"]
    tops = [hi for _lo, hi in wd["bins_ft"]]
    y = b.get("year_built") or 0
    # unknown year built: the state's homes by build era (Census ACS)
    eras = [((y >= 2002, 1976 <= y < 2002), 1.0)] if y else [((yy >= 2002, 1976 <= yy < 2002), w) for yy, w in era_weights(state)]

    def rate(kind: str, above: float, depth: float) -> float:
        if cal:  # logistic in ft above the floor, ft above the ground (waves), V zone and build era, from three surveyed towns
            c = cal["water"][kind]
            z0 = c[0] + c[1] * min(max(above, -10.0), 20.0) + c[2] * min(max(depth, 0.0), 25.0) + c[3] * (v and surge)
            return sum(w / (1 + math.exp(-(z0 + sum(k * e for k, e in zip(c[4:], era))))) for era, w in eras)
        return wd[kind][min(bisect.bisect_right(tops, above), len(tops) - 1)]

    step = max(1, len(floors) // 60)  # every few surveyed floors is plenty for an average
    fs = floors[::step]
    n = len(depths) * len(fs)
    d = s = loss = wet = 0.0
    for x in depths:
        for f in fs:
            d += rate("damaged", x - f, x)
            s += rate("severe", x - f, x)
            loss += _interp(xs, ps, x - f)
            wet += x > f
    return d / n, s / n, loss / n, wet / n


def flood(depth_ft: float, b: dict, state: str | None = None) -> tuple[float, float, float, float]:
    return flood_at([depth_ft], b, state=state)


def modeled(t: str, intensity: float, b: dict, state: str | None, depth: float = 0) -> tuple:
    """(chance damaged, chance badly damaged, expected repair cost / value,
    chance of water above the first floor) for one building in one
    scenario; None where a number doesn't apply."""
    if t == "hurricane":
        return hurricane(intensity, depth, b, state)
    if t == "tornado":
        return (*tornado(intensity, b), None, None)
    if t == "earthquake":
        return (*quake(intensity, b, state), None)
    if t == "flood":
        return flood(intensity, b, state)
    raise ValueError(t)


def class_key(b: dict, state: str | None) -> tuple:
    """Buildings with the same key get the same odds in every scenario."""
    from .structures import code_era

    y = b.get("year_built")
    return (wind_key(b, state), tuple(b.get("surge") or ()), b.get("flood_zone"), "manufactured" in (b.get("material") or ""), quake_model(b, state), b.get("foundation"), (b.get("stories") or 1) >= 2,
            b.get("material"), b.get("green"), code_era(y, state, "hurricane"), code_era(y, state, "wildfire"),
            (y or 0) >= 2002, 1976 <= (y or 0) < 2002)  # the calibration's build eras


def odds_table(buildings: list[dict], state: str | None, place: dict | None = None) -> tuple[list[dict], list[int]]:
    """Every scenario's odds for each kind of building in the square.

    place is site(): hurricane gusts here after weakening inland. Each
    building's "surge" is its surge band in Category 1 to 5.

    Returns (classes, index): classes[i] = {"odds": {scenario: [damaged,
    severe, expected repair cost / value]}, ...}; index[n] is building n's class. Measured scenarios come
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
                        odds[sid] = [None, None, None, None]
                        continue
                    r = lookup(b, past, state, kind)
                    odds[sid] = [r["chance_damaged"], r["chance_badly_damaged_or_destroyed"], None, None]
                    extra[sid] = [*r["chance_damaged_90pct"], r["look_alikes"]]  # 90% range and how many look-alikes
                else:
                    depth = 0
                    if t == "hurricane":
                        x = (place or {}).get("gusts", {}).get(cid, x)
                        surge = b.get("surge") or ()
                        depth = surge[list(SURGE_LAYERS).index(cid)] if surge else 0
                    d, sv, loss, wet = modeled(t, x, b, state, depth)
                    odds[sid] = [round(d, 2), round(sv, 2), None if loss is None else round(loss, 3), None if wet is None else round(wet, 2)]
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
            "average_repair_cost_share_of_value": round(sum(v[2] for v in vals) / len(vals), 3) if vals[0][2] is not None else None,
            "average_chance_flooded": round(sum(v[3] for v in vals) / len(vals), 2) if vals[0][3] is not None else None,
            "expected_repair_dollars": round(sum(classes[i]["odds"][sid][2] * (b.get("value") or 0) for b, i in zip(buildings, index)))
            if vals[0][2] is not None else None,
        })
    return out
