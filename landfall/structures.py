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

* CAL FIRE Damage Inspection (DINS), for California wildfires: every
  structure inside the fire's perimeter, inspected after the fire, with its
  year built (from the assessor, kept even when the house burned, unlike
  today's tax rolls) and how it was built where it matters for fire: roof,
  eaves, vent screens, siding, window panes, decks. Preferred over everything
  else for those fields. DINS also records the damage, which is used only to
  score Landfall, never shown as Landfall's own call.

The code era is read off the year built and depends on the disaster: flood
elevation rules (about 1975), Florida's statewide building code (2002) for
wind, California's wildfire building rules (Chapter 7A, 2008), and modern
seismic codes for earthquakes. Outside the US only the street photos (where
Mapillary has any) say how a building is built.
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


def code_era(year: int | None, state: str | None = "FL", kind: str = "hurricane") -> str | None:
    """Which rules a building went up under, from its year built.

    Flood: communities joined the National Flood Insurance Program in the
    1970s; before about 1975 houses were built at grade ("pre-FIRM"). Wind:
    in Florida the statewide Florida Building Code, with its wind-borne
    debris and roof-attachment rules written after Hurricane Andrew (1992),
    took effect in March 2002. Wildfire: California's wildland-urban
    interface rules (Building Code Chapter 7A: ignition-resistant roofs,
    vents, eaves and siding) apply to homes permitted from 2008. Earthquake:
    in the US, the 1976 Uniform Building Code brought modern seismic design
    and the post-Northridge changes followed in 1997; Myanmar's first national
    building code with seismic provisions dates from 2012 (revised 2020).
    """
    if not year:
        return None
    if kind == "wildfire":
        if state == "CA":
            return "before California's wildfire building rules (pre-2008)" if year < 2008 else "California wildfire building rules (2008 or later)"
        return None
    if kind == "earthquake":
        if state is None:  # abroad: Myanmar is the one earthquake area Landfall covers
            return "before Myanmar's national building code (pre-2012)" if year < 2012 else "Myanmar national building code (2012 or later)"
        if year < 1976:
            return "before modern seismic codes (pre-1976)"
        return "modern seismic code (1976-1996)" if year < 1997 else "post-Northridge seismic code (1997 or later)"
    if state == "FL" and kind == "hurricane":
        if year < 1975:
            return "before flood-elevation rules (pre-1975)"
        if year < 2002:
            return "after flood-elevation rules, before the Florida Building Code (1975-2001)"
        return "Florida Building Code (2002 or later)"
    return "before flood-elevation rules (pre-1975)" if year < 1975 else "after flood-elevation rules (1975 or later)"


# CAL FIRE damage inspections, per Landfall disaster (storms.py key)
DINS = {
    "la_fires": [
        "https://services3.arcgis.com/Oy2JTCD10wkoelxS/arcgis/rest/services/DINS_2025_Eaton_Data_Share_View/FeatureServer/0/query",
        "https://services3.arcgis.com/Oy2JTCD10wkoelxS/arcgis/rest/services/DINS_2025_Palisades_Data_Share_View/FeatureServer/0/query",
    ],
}
DINS_FIELDS = "DAMAGE,STRUCTURET,ROOFCONSTR,EAVES,VENTSCREEN,EXTERIORSI,WINDOWPANE,DECKPORCHO,YEARBUILT,NUMBEROFUN,LATITUDE,LONGITUDE"


def dins_points(storm: str, bbox: tuple[float, float, float, float], timeout: float = 60) -> list[dict]:
    """CAL FIRE inspection records in a box, as points with x, y."""
    import requests

    out = []
    for url in DINS.get(storm, []):
        offset = 0
        while True:
            r = requests.get(url, params=dict(
                geometry=",".join(map(str, bbox)), geometryType="esriGeometryEnvelope", inSR=4326,
                spatialRel="esriSpatialRelIntersects", outFields=DINS_FIELDS, returnGeometry="false",
                resultOffset=offset, resultRecordCount=2000, f="json"), timeout=timeout)
            r.raise_for_status()
            feats = r.json().get("features", [])
            for f in feats:
                a = f["attributes"]
                if a.get("LONGITUDE") is not None and a.get("LATITUDE") is not None:
                    out.append({**a, "x": a["LONGITUDE"], "y": a["LATITUDE"]})
            if len(feats) < 2000:
                break
            offset += len(feats)
    return out


def _dins_value(v) -> str | None:
    v = (v or "").strip()
    return None if not v or v.lower() in ("unknown", "none", "null") else v


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
        if v is not None and "inspection" not in str(out.get(k + "_source", "")):
            out[k] = STREET_LABEL.get(v, v) if k == "material" else v
            out[k + "_source"] = "street photos"
    if street and "street photos" not in srcs:
        srcs.append("street photos")
    out["sources"] = srcs
    out.pop("note", None)
    return out


def describe(nsi: dict | None, parcel: dict | None, state: str | None = "FL", kind: str = "hurricane",
             dins: dict | None = None) -> dict:
    """One building's record: what it is made of, what it stands on, when it
    was built and under which rules, and where each fact comes from."""
    p = (parcel or {}).get("parcel") or {}
    year = int(p["ACT_YR_BLT"]) if p.get("ACT_YR_BLT") else None
    rec: dict = {
        "year_built": year,
        "effective_year_built": int(p["EFF_YR_BLT"]) if p.get("EFF_YR_BLT") else None,
        "year_source": "county tax roll" if year else None,
    }
    sources = []
    if nsi:
        sources.append("USACE National Structure Inventory")
        rec.update(
            material=MATERIAL.get(nsi.get("bldgtype"), nsi.get("bldgtype")),
            foundation=FOUNDATION.get(nsi.get("found_type"), nsi.get("found_type")),
            foundation_height_ft=nsi.get("found_ht"),
            stories=nsi.get("num_story"),
            floor_area_sqft=nsi.get("sqft"),
            occupancy=nsi.get("occtype"),
            nsi_id=nsi.get("fd_id"),
        )
        for k in ("material", "foundation", "stories"):
            if rec.get(k) is not None:
                rec[k + "_source"] = "National Structure Inventory (often wrong here)"
        if not year and nsi.get("med_yr_blt"):
            rec.update(block_median_year_built=nsi["med_yr_blt"])
    if year:
        sources.append("county tax roll")
    if dins:
        sources.append("CAL FIRE damage inspection")
        y = dins.get("YEARBUILT")
        if y and 1700 < int(y) <= 2026 and not year:
            rec.update(year_built=int(y), year_source="CAL FIRE damage inspection (from the assessor)")
        rec["fire"] = {k: v for k, v in {
            "roof": _dins_value(dins.get("ROOFCONSTR")),
            "eaves": _dins_value(dins.get("EAVES")),
            "vent_screens": _dins_value(dins.get("VENTSCREEN")),
            "siding": _dins_value(dins.get("EXTERIORSI")),
            "window_panes": _dins_value(dins.get("WINDOWPANE")),
            "deck_or_porch": _dins_value(dins.get("DECKPORCHO")),
        }.items() if v}
        rec["structure_type"] = _dins_value(dins.get("STRUCTURET"))
        if rec["fire"].get("siding"):
            rec.update(material=rec["fire"]["siding"].lower() + " siding", material_source="CAL FIRE damage inspection")
    rec["code_era"] = code_era(rec["year_built"], state, kind)
    rec["sources"] = sources
    return rec


STATE_FIPS = {"01": "AL", "06": "CA", "12": "FL", "13": "GA", "22": "LA", "28": "MS", "37": "NC", "45": "SC", "47": "TN", "48": "TX", "51": "VA", "21": "KY", "72": "PR"}


def fetch(d: Path, state: str | None = None, kind: str = "hurricane", storm: str = "") -> dict:
    """Look up every building in an area and write structures.json. The
    state, when not given, comes from the inventory's census block ids."""
    path = d / "structures.json"
    feats = json.loads((d / "buildings.geojson").read_text())["features"]
    if not feats:
        return {}
    xs = [f["properties"]["centroid"][0] for f in feats]
    ys = [f["properties"]["centroid"][1] for f in feats]
    pad = 0.0005
    box = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
    cache = d / "nsi.json"
    if cache.exists():
        points = json.loads(cache.read_text())
    else:
        points = _query(box)
        cache.write_text(json.dumps(points))
    if state is None and points:
        state = STATE_FIPS.get(str(points[0].get("cbfips", ""))[:2])
    inspections = {}
    if storm in DINS:
        inspections = match(feats, dins_points(storm, box))
    parcels = json.loads((d / "parcels.json").read_text()) if (d / "parcels.json").exists() else {}
    matched = match(feats, points)
    out = {bid: describe(q, parcels.get(bid), state, kind, inspections.get(bid)) for bid, q in matched.items()}
    path.write_text(json.dumps(out, indent=1))
    return out
