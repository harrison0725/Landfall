"""Damage odds for the buildings in a 1-mile grid square, from past disasters
with buildings like them. No photos of a new disaster are needed.

For a square anywhere in the US it gathers each building's history:
- the year built: Florida's statewide parcel layer (the county tax roll) in
  Florida, else the USACE National Structure Inventory, whose year is the
  median for the census block, not the house;
- wall material, foundation and stories: the National Structure Inventory
  (often wrong: in Cedar Key it called most wood houses masonry);
- the green around it: mostly trees, some trees or lawn, or little green,
  read from Esri's satellite photo within about 12 m of the building;
and the building-rules era its year falls in (structures.code_era).

It groups the buildings by era, construction and green, then looks each
group up in the history: buildings that went through a disaster of the
same kind and were inspected afterwards (landfall/history/*.csv). The
share of look-alikes that were damaged is the group's odds, with a 90%
interval; when too few look-alikes match every factor, it drops the least
telling factor first (green, then foundation, then material) and says so.

Every square also carries each building's odds for every disaster type and
category in hazards.SCENARIOS (hurricane Category 1 to 5, tornado EF0 to EF5,
earthquake shaking, flood depth from damage curves; storm surge and wildfire
from the look-alikes above), so the map can switch between them.
"""

from __future__ import annotations

import csv
import io
import math
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

import numpy as np

from .structures import FOUNDATION, MATERIAL, STATE_FIPS, _inside, _query, code_era

HISTORY = Path(__file__).resolve().parent / "history"
ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
GREEN_ZOOM = 18
GREEN_RADIUS_M = 12.0
MIN_MATCH = 15
FACTORS = ["age", "construction", "green"]

# what each history counts as damaged, in the words shown to people
HISTORIES = {
    "hurricane": {
        "file": "helene_cedar_key.csv",
        "source": "Hurricane Helene (2024), Cedar Key, Florida: 771 buildings rated by the DesignSafe PRJ-6245 survey (checked against StEER field teams)",
        "damaged": "damaged (survey damage state 2 or worse: more than light damage)",
        "severe": "badly damaged or destroyed (damage state 4 or worse)",
    },
    "wildfire": {
        "file": "la_fires_dins.csv",
        "source": "2025 Eaton and Palisades fires, Los Angeles: about 30,000 buildings inspected by CAL FIRE",
        "damaged": "damaged (CAL FIRE minor or worse)",
        "severe": "destroyed (CAL FIRE: more than half gone)",
    },
}


# ---------------------------------------------------------------- the buildings in a square


def bbox_of(ring: list[list[float]]) -> tuple[float, float, float, float]:
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def parcels_fl(bbox: tuple[float, float, float, float]) -> list[tuple[list, int]]:
    """Florida parcels in a box with their year built: [(ring, year)]."""
    import requests

    from .cost import CADASTRAL

    out, offset = [], 0
    while True:
        r = requests.get(CADASTRAL, params=dict(
            geometry=",".join(map(str, bbox)), geometryType="esriGeometryEnvelope", inSR=4326, outSR=4326,
            spatialRel="esriSpatialRelIntersects", outFields="ACT_YR_BLT", where="ACT_YR_BLT>0", returnGeometry="true",
            geometryPrecision=6, resultOffset=offset, resultRecordCount=2000, f="geojson"), timeout=120)
        r.raise_for_status()
        feats = r.json().get("features", [])
        for f in feats:
            g, y = f.get("geometry") or {}, (f.get("properties") or {}).get("ACT_YR_BLT")
            rings = [g["coordinates"][0]] if g.get("type") == "Polygon" else [p[0] for p in g.get("coordinates", [])] if g.get("type") == "MultiPolygon" else []
            for ring in rings:
                out.append((ring, int(y)))
        if len(feats) < 2000 or offset > 20000:
            break
        offset += len(feats)
    return out


def _tile_xy(lon: float, lat: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    x = (lon + 180) / 360 * n
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    return x, y


@lru_cache(maxsize=4096)
def _tile(z: int, x: int, y: int) -> np.ndarray | None:
    import requests
    from PIL import Image

    try:
        r = requests.get(ESRI.format(z=z, x=x, y=y), timeout=30)
        r.raise_for_status()
        return np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB"))
    except Exception:  # noqa: BLE001 - a missing tile only leaves green unknown
        return None


def green_masks(img: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Trees and lawn in an Esri satellite tile. Trees are green and rough;
    lawn is green, smooth and bright. Smooth dark olive (muddy water) is
    neither."""
    import cv2

    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV).astype(np.float32)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY).astype(np.float32)
    std = np.sqrt(np.maximum(cv2.blur(g * g, (7, 7)) - cv2.blur(g, (7, 7)) ** 2, 0))
    greenish = (h >= 30) & (h <= 90) & (s >= 40)
    return greenish & (std >= 9) & (v >= 35), greenish & (std < 9) & (v >= 95)


def green_label(trees: float, lawn: float) -> str:
    if trees >= 0.35:
        return "mostly trees"
    if trees + lawn < 0.15:
        return "little green (pavement, sand, water or bare ground)"
    return "some trees or lawn"


def green_at(points: list[tuple[float, float]], z: int = GREEN_ZOOM) -> list[str | None]:
    """The green around each point (lon, lat) from Esri's satellite photo."""

    if not points:
        return []
    tiles = {(int(x), int(y)) for x, y in (_tile_xy(lon, lat, z) for lon, lat in points)}
    with ThreadPoolExecutor(8) as pool:
        got = dict(zip(tiles, pool.map(lambda t: _tile(z, *t), tiles)))
    out = []
    for lon, lat in points:
        fx, fy = _tile_xy(lon, lat, z)
        img = got.get((int(fx), int(fy)))
        if img is None:
            out.append(None)
            continue
        m_px = 156543.03392 * math.cos(math.radians(lat)) / 2 ** z
        rad = max(3, int(GREEN_RADIUS_M / m_px))
        cx, cy = int((fx % 1) * img.shape[1]), int((fy % 1) * img.shape[0])
        y0, y1, x0, x1 = max(0, cy - rad), min(img.shape[0], cy + rad + 1), max(0, cx - rad), min(img.shape[1], cx + rad + 1)
        crop = img[y0:y1, x0:x1]
        yy, xx = np.mgrid[y0:y1, x0:x1]
        d2 = (yy - cy) ** 2 + (xx - cx) ** 2
        ring = (d2 <= rad**2) & (d2 >= (rad * 0.4) ** 2)  # around the building, not its roof
        if ring.sum() < 10:
            out.append(None)
            continue
        trees, lawn = green_masks(crop)
        n = ring.sum()
        out.append(green_label(float((trees & ring).sum()) / n, float((lawn & ring).sum()) / n))
    return out


def buildings_in(ring: list[list[float]], kind: str = "hurricane") -> dict:
    """Every building in a square with its history: year, era, material,
    foundation, stories, green, and where each fact came from."""
    bbox = bbox_of(ring)
    nsi = [p for p in _query(bbox, timeout=90) if _inside(p["x"], p["y"], ring)]
    states = [STATE_FIPS.get(str(p.get("cbfips", ""))[:2]) for p in nsi]
    state = max(set(states), key=states.count) if states else None
    parcels = parcels_fl(bbox) if state == "FL" else []
    rows = []
    for p in nsi:
        year, year_src = None, None
        for pring, y in parcels:
            if _inside(p["x"], p["y"], pring):
                year, year_src = y, "county tax roll"
                break
        if year is None and p.get("med_yr_blt"):
            year, year_src = int(p["med_yr_blt"]), "structure inventory (median year for the block)"
        rows.append({
            "lon": round(p["x"], 6), "lat": round(p["y"], 6),
            "year_built": year, "year_source": year_src,
            "code_era": code_era(year, state, kind),
            "material": MATERIAL.get(p.get("bldgtype")),
            "foundation": FOUNDATION.get(p.get("found_type")),
            "stories": p.get("num_story"),
            "occupancy": p.get("occtype"),
            "flood_zone": p.get("firmzone"),
            # what it would cost to rebuild the structure (not the land or contents), national structure inventory
            "value": round(p["val_struct"]) if p.get("val_struct") else None,
        })
    for r, g in zip(rows, green_at([(r["lon"], r["lat"]) for r in rows])):
        r["green"] = g
    from . import hazards

    v = hazards.design_wind(round((bbox[0] + bbox[2]) / 2, 3), round((bbox[1] + bbox[3]) / 2, 3)) if rows else None
    for r in rows:
        r["v_ult"] = v  # ASCE 7 design wind speed at the square's center
    place = hazards.site(bbox)
    if rows and place["surge_checked"]:
        bands = hazards.surge_bands(bbox, [(r["lon"], r["lat"]) for r in rows])
        place["surge_map_read"] = bands is not None  # without the live map, the 160 m depth data alone
        cats = list(hazards.SURGE_LAYERS)
        for k, r in enumerate(rows):
            # water above the ground in Category 1..5, whole feet (0: dry)
            r["surge"] = [hazards.surge_depth(c, r["lon"], r["lat"], bands[k][i] if bands else None) for i, c in enumerate(cats)]
    return {"state": state, "buildings": rows, "site": place}


# ---------------------------------------------------------------- the history


def age(r: dict) -> str:
    return r.get("code_era") or "year built unknown"


def construction(r: dict) -> str:
    m, f = r.get("material"), r.get("foundation")
    if not m and not f:
        return "construction unknown"
    return f"{m or 'unknown material'} on {f or 'unknown foundation'}"


def green(r: dict) -> str:
    return r.get("green") or "green unknown"


KEY = {"age": age, "construction": construction, "green": green}


@lru_cache(maxsize=8)
def history(kind: str) -> list[dict]:
    """Past buildings of this disaster kind: year, material, foundation,
    green, damaged (0/1), severe (0/1)."""
    h = HISTORIES.get(kind)
    if not h or not (HISTORY / h["file"]).exists():
        return []
    rows = []
    with (HISTORY / h["file"]).open() as fh:
        for r in csv.DictReader(fh):
            rows.append({
                "year_built": int(r["year_built"]) if r.get("year_built") else None,
                "material": r.get("material") or None,
                "foundation": r.get("foundation") or None,
                "green": r.get("green") or None,
                "damaged": int(r["damaged"]),
                "severe": int(r["severe"]),
            })
    return rows


def wilson(k: int, n: int, z: float = 1.645) -> tuple[float, float]:
    """90% interval for a share k of n."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


MATCH = {  # the factors a group is matched on, least telling dropped first
    "green": lambda r, state, kind: r.get("green"),
    "foundation": lambda r, state, kind: r.get("foundation"),
    "material": lambda r, state, kind: r.get("material"),
    "age": lambda r, state, kind: code_era(r.get("year_built"), state, kind),
}


def lookup(member: dict, past: list[dict], state: str | None, kind: str) -> dict:
    """Odds for a group (given one of its buildings) from the past buildings
    most like it."""
    have = [f for f in ("age", "material", "foundation", "green")
            if MATCH[f](member, state, kind) and any(p.get(f if f != "age" else "year_built") for p in past)]
    use = list(have)
    while True:
        want = {f: MATCH[f](member, state, kind) for f in use}
        like = [p for p in past if all(MATCH[f](p, state, kind) == v for f, v in want.items())]
        if len(like) >= MIN_MATCH or not use:
            break
        use.pop()  # have is ordered age, material, foundation, green: drop from the end
    n = len(like)
    k = sum(p["damaged"] for p in like)
    s = sum(p["severe"] for p in like)
    lo, hi = wilson(k, n)
    return {
        "matched_on": use or ["nothing (all past buildings)"],
        "look_alikes": n,
        "chance_damaged": round(k / n, 2) if n else None,
        "chance_damaged_90pct": [round(lo, 2), round(hi, 2)],
        "chance_badly_damaged_or_destroyed": round(s / n, 2) if n else None,
    }


def range_sd() -> dict:
    """Model spread (log scale) for the 'likely between' ranges, by disaster type."""
    from . import hazards

    cal = hazards.calibration()
    hurricane = (cal or {}).get("range_sd_log", 0.3)
    return {"hurricane": hurricane, "other": max(0.5, hurricane)}


def estimate(ring: list[list[float]], kind: str = "hurricane", label: str = "", scenario: str | None = None) -> dict:
    """Group the buildings in a square and give each group's odds in one
    disaster scenario (a type and category, hazards.SCENARIOS), plus every
    scenario's odds for each kind of building so the map can switch."""
    from . import hazards

    got = buildings_in(ring, kind)
    rows, state = got["buildings"], got["state"]
    sid = scenario if scenario in hazards.scenario_ids() else hazards.default_scenario(kind)
    t, cid = sid.split(":")
    place = got.get("site") or {}
    classes, index = hazards.odds_table(rows, state, place)
    for r, i in zip(rows, index):
        r["cls"] = i
        r["chance_damaged"] = classes[i]["odds"][sid][0]
    groups: dict[str, dict] = {}
    for r in rows:
        f = {k: KEY[k](r) for k in FACTORS}
        name = " · ".join(f[k] for k in FACTORS)
        g = groups.setdefault(name, {"group": name, **f, "buildings": 0, "_members": []})
        g["buildings"] += 1
        g["_members"].append(r)
        r["group"] = name
    measured = t in hazards.MEASURED
    hkind = hazards.MEASURED.get(t)
    past = history(hkind) if measured else []
    h = HISTORIES.get(hkind) if measured else None
    for g in groups.values():
        ms = g.pop("_members")
        if measured:
            g.update(lookup(ms[0], past, state, hkind) if past else {})
        else:
            sev = [classes[m["cls"]]["odds"][sid][1] for m in ms]
            g["chance_damaged"] = round(sum(m["chance_damaged"] for m in ms) / len(ms), 2)
            g["chance_badly_damaged_or_destroyed"] = round(sum(sev) / len(sev), 2)
            loss = [classes[m["cls"]]["odds"][sid][2] for m in ms]
            if loss and loss[0] is not None:
                g["expected_repair_cost_share_of_value"] = round(sum(loss) / len(loss), 3)
                g["expected_repair_dollars"] = round(sum(x * (m.get("value") or 0) for x, m in zip(loss, ms)))
            wet = [classes[m["cls"]]["odds"][sid][3] for m in ms]
            if wet and wet[0] is not None:
                g["chance_water_above_first_floor"] = round(sum(wet) / len(wet), 2)
            g["from"] = "damage curves"
    rows_sorted = sorted(groups.values(), key=lambda g: -g["buildings"])
    known = [r["chance_damaged"] for r in rows if r["chance_damaged"] is not None]
    sc = hazards.SCENARIOS[t]
    cat_label = next(c[1] for c in sc["categories"] if c[0] == cid)
    if measured:
        source = h["source"] if h and past else f"no past {hkind} with inspected buildings in Landfall yet"
        notes = ("These are odds from look-alike buildings in one past disaster, not a forecast of this place: how hard it hits "
                 "(surge depth, distance from the fire) matters more than how a house was built. ")
    else:
        source = hazards.SOURCES[t]
        notes = (f"These are odds for a {sc['name'].lower()} at {cat_label}, {sc['note']}. ")
        if t == "hurricane" and place:
            gust = place["gusts"][cid]
            sb = sorted(r["surge"][list(hazards.SURGE_LAYERS).index(cid)] for r in rows if r.get("surge"))
            sb = [b for b in sb if b]
            wet = len(sb)
            inside = sum(classes[i]["odds"][sid][3] or 0 for i in index)
            notes += (f"The square is {place['coast_miles']} miles from the nearest coast, so a {cat_label} storm's winds are down to about "
                      f"{gust} mph gusts here (Kaplan and DeMaria inland decay, at {place['forward_mph']:.0f} mph forward speed"
                      + (f"; {place['gusts_slow'][cid]} to {place['gusts_fast'][cid]} mph if it moves at {place['slow_mph']:.0f} to {place['fast_mph']:.0f} mph, "
                         "which inland can change the odds a lot" if place['gusts_slow'][cid] != place['gusts_fast'][cid] else "") + "). ")
            if not place.get("surge_checked"):
                notes += "It is too far inland for storm surge. "
            else:
                notes += (f"In the National Hurricane Center's worst case for a {cat_label} storm at high tide, {wet} of {len(rows)} "
                          f"buildings have surge water" + (f" (typically {sb[len(sb) // 2]} ft deep), and about {round(inside)} would have water "
                          "above the first floor. Water inside is flooding, which isn't counted as damage. " if sb else ". "))
                if place.get("surge_map_read") is False:
                    notes += "The live 10 m surge map couldn't be read, so the 160 m depth data alone says which buildings get wet. "
    return {
        "square": label or "the picked square",
        "disaster_kind": kind,
        "scenario": sid,
        "scenario_label": f"{sc['name']}: {cat_label}",
        "state": state,
        "buildings": len(rows),
        "expected_damaged": round(sum(known)) if known else None,
        "groups": rows_sorted,
        "by_category": hazards.by_category(rows, classes, index, t),
        "site": place,
        "expected_repair_dollars": (round(sum((classes[i]["odds"][sid][2] or 0) * (r.get("value") or 0) for r, i in zip(rows, index)))
                                    if index and classes[index[0]]["odds"][sid][2] is not None else None),
        "rebuild_value_dollars": round(sum(r.get("value") or 0 for r in rows)),
        "range_sd_log": range_sd(),
        "range_means": "90% 'likely between' range for counts: chance from building to building plus how far off Landfall was at 7 surveyed "
                       "hurricane places (eval/research); other disasters are untested, so their range is wider",
        "expected_repair_cost_share_of_value": (round(sum(classes[i]["odds"][sid][2] for i in index) / len(index), 3)
                                                if index and classes[index[0]]["odds"][sid][2] is not None else None),
        "history": source,
        "damaged_means": h["damaged"] if h else "more than light damage to the building itself (flooding inside is given separately)",
        "severe_means": h["severe"] if h else "badly damaged or destroyed",
        "points": rows,
        "classes": classes,
        "notes": notes + "Repair dollars are the expected repair share times each building's rebuild cost from the national structure inventory "
        "(the structure only, not land or belongings). Year built is from the county tax roll in Florida, elsewhere the block's median year. Material and foundation come from "
        "the national structure inventory, which is often wrong (unknown material is treated as wood frame). Green is from today's satellite photo. "
        "The map can switch to any disaster type and category without asking again.",
    }
