"""What public records say about each building: the parcel it sits on and its
flood zone. Used to estimate how high the first floor is.

Parcel: Florida's statewide tax roll (Department of Revenue cadastral layer)
at the building's centroid: land use, year built, living area, units. No
owner names or addresses are kept.

Flood zone: FEMA's National Flood Hazard Layer at the centroid: zone and
base flood elevation (BFE, ft NAVD88).

First floor: houses built after a community joined the National Flood
Insurance Program had to put the lowest floor at or above the BFE. Florida's
coastal communities joined in the 1970s and early 1980s, so a house built in
1985 or later in a zone with a BFE is taken to have its floor at the BFE; one
built before 1975 is taken to be at grade (about 1 ft above the ground),
which is how FEMA treats "pre-FIRM" houses. Between those years, or when the
record has no year (the current roll drops buildings that were destroyed),
the floor is unknown. Older houses can have been raised since, so this is
an estimate an assessor confirms, not a measurement.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .cost import CADASTRAL

NFHL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
POST_FIRM_YEAR = 1985
PRE_FIRM_YEAR = 1975
LOW_FLOOR_FT = (1.0, 3.0)  # slab to crawlspace, Hazus default first-floor heights
PARCEL_FIELDS = "DOR_UC,ACT_YR_BLT,EFF_YR_BLT,TOT_LVG_AR,NO_RES_UNT,NO_BULDNG,JV,LND_VAL,ASMNT_YR"


def _point_query(url: str, lon: float, lat: float, fields: str) -> dict | None:
    import requests

    for attempt in range(3):
        try:
            r = requests.get(
                url,
                params=dict(
                    geometry=f"{lon},{lat}",
                    geometryType="esriGeometryPoint",
                    inSR=4326,
                    spatialRel="esriSpatialRelIntersects",
                    outFields=fields,
                    returnGeometry="false",
                    f="json",
                ),
                timeout=60,
            )
            r.raise_for_status()
            feats = r.json().get("features", [])
            return feats[0]["attributes"] if feats else None
        except Exception:
            if attempt == 2:
                return None
    return None


def first_floor(year: int | None, zone: str | None, bfe: float | None, ground_ft: float | None) -> dict:
    """Estimated first-floor height above the ground as a (low, high) range in
    feet, with the reason. A None high end means it could be raised well
    above the water."""
    if ground_ft is None:
        return {"floor_low_ft": None, "floor_high_ft": None, "basis": "ground elevation unknown"}
    if year and year >= POST_FIRM_YEAR and bfe is not None and bfe > ground_ft:
        h = round(bfe - ground_ft, 1)
        return {"floor_low_ft": h, "floor_high_ft": h, "basis": f"built {year}, after flood rules: floor at the BFE ({bfe:g} ft NAVD88, zone {zone})"}
    if year and year < PRE_FIRM_YEAR:
        return {"floor_low_ft": LOW_FLOOR_FT[0], "floor_high_ft": LOW_FLOOR_FT[1], "basis": f"built {year}, before flood rules: likely 1-3 ft above ground"}
    if zone and zone.upper().startswith("X"):
        return {"floor_low_ft": LOW_FLOOR_FT[0], "floor_high_ft": LOW_FLOOR_FT[1], "basis": f"zone {zone}, no elevation required: likely 1-3 ft above ground"}
    why = f"built {year}, in the years communities were joining the flood program" if year else "no year built on record (the current roll drops destroyed buildings)"
    return {"floor_low_ft": LOW_FLOOR_FT[0], "floor_high_ft": None, "basis": f"{why}: unknown"}


def lookup(d: Path, workers: int = 16) -> dict:
    """Parcel, flood zone and first-floor estimate for every building."""
    path = d / "parcels.json"
    feats = json.loads((d / "buildings.geojson").read_text())["features"]
    grounds = {}
    if (d / "flood.json").exists():
        grounds = {b["id"]: b.get("ground_ft") for b in json.loads((d / "flood.json").read_text()).get("buildings", [])}
    cached = json.loads(path.read_text()) if path.exists() else {}

    def one(f) -> tuple[str, dict]:
        bid = f["properties"]["id"]
        if bid in cached and "parcel" in cached[bid]:
            rec = cached[bid]
        else:
            lon, lat = f["properties"]["centroid"]
            rec = {"parcel": _point_query(CADASTRAL, lon, lat, PARCEL_FIELDS), "zone": _point_query(NFHL, lon, lat, "FLD_ZONE,STATIC_BFE,V_DATUM")}
        p = rec["parcel"] or {}
        z = rec["zone"] or {}
        bfe = z.get("STATIC_BFE")
        bfe = bfe if bfe is not None and bfe > -9000 and z.get("V_DATUM") in (None, "NAVD88") else None
        year = int(p["ACT_YR_BLT"]) if p.get("ACT_YR_BLT") else None
        rec.update(first_floor(year, z.get("FLD_ZONE"), bfe, grounds.get(bid)))
        rec.update(year_built=year, flood_zone=z.get("FLD_ZONE"), bfe_ft=bfe, units=int(p.get("NO_RES_UNT") or 0), land_use=p.get("DOR_UC"))
        return bid, rec

    with ThreadPoolExecutor(workers) as pool:
        out = dict(pool.map(one, feats))
    path.write_text(json.dumps(out, indent=1))
    return out
