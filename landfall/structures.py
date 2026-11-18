"""How and when each building was built, from public records.

Damage depends on how a building was built: what it is made of, what it
stands on, how many stories, and which building code it went up under. This
gathers those facts for every outline so the agent, the map card and the
research data can use them.

Sources, all free and with no account:

* USACE National Structure Inventory (NSI), nationwide:
  https://nsi.sec.usace.army.mil/nsiapi/structures . Construction material,
  foundation type and height, stories, floor area, occupancy, value, ground
  elevation and flood zone, one point per building. Checked against the
  assessors of the Cedar Key field survey (DesignSafe PRJ-6245, 713
  buildings): material right 85% of the time, stories 81%, foundation type
  only 41%. Its year built is the census block's median, not the building's.
  In Cedar Key the live API is far worse than that older copy: material
  right 16%, stories 73%, foundation 28% of 122 matched buildings (it calls
  most wood-frame houses masonry). So it is kept as a last resort only.
* The county tax roll, where Landfall has one (Florida's statewide
  cadastral layer, landfall/parcels.py): the building's actual and effective
  year built. Reliable.
* Street-level photos (landfall/street.py): the agent reads material,
  foundation and stories off them. In Cedar Key: material right 86%, stories
  84%, foundation 39% (57% as at grade / short piers or crawlspace / tall
  piles), against the survey's assessors. Preferred over NSI where present.

The code era is read off the year built. Outside the US there is no NSI and
this step does nothing.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

NSI = "https://nsi.sec.usace.army.mil/nsiapi/structures"
MATCH_M = 15.0  # an NSI point this close to an outline counts as that building
M_PER_DEG_LAT = 111_320.0

MATERIAL = {"W": "wood frame", "M": "masonry", "C": "concrete", "S": "steel", "H": "manufactured (mobile) home"}
FOUNDATION = {"C": "crawlspace", "B": "basement", "S": "slab", "P": "piers", "I": "piles", "F": "fill", "W": "solid wall"}


def code_era(year: int | None, state: str | None = "FL") -> str | None:
    """Which rules a building went up under, from its year built.

    Flood: communities joined the National Flood Insurance Program in the
    1970s; before about 1975 houses were built at grade ("pre-FIRM"). Wind:
    in Florida the statewide Florida Building Code, with its wind-borne
    debris and roof-attachment rules written after Hurricane Andrew (1992),
    took effect in March 2002.
    """
    if not year:
        return None
    if state == "FL":
        if year < 1975:
            return "before flood-elevation rules (pre-1975)"
        if year < 2002:
            return "after flood-elevation rules, before the Florida Building Code (1975-2001)"
        return "Florida Building Code (2002 or later)"
    return "before flood-elevation rules (pre-1975)" if year < 1975 else "after flood-elevation rules (1975 or later)"


def _query(bbox: tuple[float, float, float, float], timeout: float = 60) -> list[dict]:
    import requests

    w, s, e, n = bbox
    ring = f"{w},{s},{e},{s},{e},{n},{w},{n},{w},{s}"
    r = requests.get(NSI, params={"bbox": ring}, timeout=timeout)
    r.raise_for_status()
    return [f["properties"] for f in r.json().get("features", [])]


def _inside(lon: float, lat: float, ring: list[list[float]]) -> bool:
    hit = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def _meters(a: tuple[float, float], b: tuple[float, float]) -> float:
    k = math.cos(math.radians(a[1]))
    return math.hypot((a[0] - b[0]) * k, a[1] - b[1]) * M_PER_DEG_LAT


def match(feats: list[dict], points: list[dict]) -> dict[str, dict | None]:
    """The NSI point for each outline: one inside it (the largest), else the
    nearest within MATCH_M of its centroid."""
    out = {}
    for f in feats:
        p = f["properties"]
        geom = f["geometry"]
        ring = geom["coordinates"][0] if geom["type"] == "Polygon" else geom["coordinates"][0][0]
        c = p["centroid"]
        inside = [q for q in points if _inside(q["x"], q["y"], ring)]
        if inside:
            out[p["id"]] = max(inside, key=lambda q: q.get("sqft") or 0)
            continue
        near = min(points, key=lambda q: _meters(c, (q["x"], q["y"])), default=None)
        out[p["id"]] = near if near and _meters(c, (near["x"], near["y"])) <= MATCH_M else None
    return out


NSI_TO_STREET = {"wood frame": "wood", "manufactured (mobile) home": "manufactured"}
STREET_LABEL = {"wood": "wood frame", "manufactured": "manufactured (mobile) home"}


def merge(rec: dict | None, street: dict | None) -> dict | None:
    """The record shown and given to the agent: what the street photos show
    wins over the National Structure Inventory, field by field."""
    if not rec and not street:
        return None
    out = dict(rec or {})
    srcs = list(out.get("sources", []))
    for k in ("material", "foundation", "stories"):
        v = (street or {}).get(k)
        if v is not None:
            out[k] = STREET_LABEL.get(v, v) if k == "material" else v
            out[k + "_source"] = "street photos"
        elif out.get(k) is not None:
            out[k + "_source"] = "National Structure Inventory (often wrong here)"
    if street and "street photos" not in srcs:
        srcs.append("street photos")
    out["sources"] = srcs
    out.pop("note", None)
    return out


def describe(nsi: dict | None, parcel: dict | None, state: str | None = "FL") -> dict:
    """One building's record: what it is made of, what it stands on, when it
    was built and under which rules, and where each fact comes from."""
    p = (parcel or {}).get("parcel") or {}
    year = int(p["ACT_YR_BLT"]) if p.get("ACT_YR_BLT") else None
    rec: dict = {
        "year_built": year,
        "effective_year_built": int(p["EFF_YR_BLT"]) if p.get("EFF_YR_BLT") else None,
        "year_source": "county tax roll" if year else None,
    }
    if nsi:
        rec.update(
            material=MATERIAL.get(nsi.get("bldgtype"), nsi.get("bldgtype")),
            foundation=FOUNDATION.get(nsi.get("found_type"), nsi.get("found_type")),
            foundation_height_ft=nsi.get("found_ht"),
            stories=nsi.get("num_story"),
            floor_area_sqft=nsi.get("sqft"),
            occupancy=nsi.get("occtype"),
            nsi_id=nsi.get("fd_id"),
        )
        if not year and nsi.get("med_yr_blt"):
            rec.update(year_built=None, block_median_year_built=nsi["med_yr_blt"])
    rec["code_era"] = code_era(rec["year_built"], state)
    rec["sources"] = ["USACE National Structure Inventory"] * bool(nsi) + ["county tax roll"] * bool(year)
    return rec


def fetch(d: Path, state: str | None = "FL") -> dict:
    """Look up every building in an area and write structures.json."""
    path = d / "structures.json"
    feats = json.loads((d / "buildings.geojson").read_text())["features"]
    if not feats:
        return {}
    xs = [f["properties"]["centroid"][0] for f in feats]
    ys = [f["properties"]["centroid"][1] for f in feats]
    pad = 0.0005
    cache = d / "nsi.json"
    if cache.exists():
        points = json.loads(cache.read_text())
    else:
        points = _query((min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad))
        cache.write_text(json.dumps(points))
    parcels = json.loads((d / "parcels.json").read_text()) if (d / "parcels.json").exists() else {}
    matched = match(feats, points)
    out = {bid: describe(q, parcels.get(bid), state) for bid, q in matched.items()}
    path.write_text(json.dumps(out, indent=1))
    return out
