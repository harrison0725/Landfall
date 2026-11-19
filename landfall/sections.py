"""The square-mile sections of the US Public Land Survey System: the 1-mile
grid printed on USGS topo maps and county maps, and used in property deeds
("Section 29, Township 15 South, Range 13 East").

Lines come from the Bureau of Land Management's national PLSS layer
(CadNSDI) in fixed 0.1-degree cells, cached on disk, so the map draws the
same lines at every zoom. Places outside the survey (Texas, the original
colonies, other countries) return no sections.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import requests

PLSS = "https://gis.blm.gov/arcgis/rest/services/Cadastral/BLM_Natl_PLSS_CadNSDI/MapServer/2/query"
CELL = 0.1  # degrees


def cell_bbox(i: int, j: int) -> tuple[float, float, float, float]:
    """(west, south, east, north) of grid cell i (longitude) j (latitude)."""
    return (round(i * CELL, 6), round(j * CELL, 6), round((i + 1) * CELL, 6), round((j + 1) * CELL, 6))


def cells(west: float, south: float, east: float, north: float) -> list[tuple[int, int]]:
    return [(i, j) for i in range(math.floor(west / CELL), math.ceil(east / CELL))
            for j in range(math.floor(south / CELL), math.ceil(north / CELL))]


def label(props: dict) -> str:
    """'Sec 29 · T15S R13E' from the section's PLSS id (FL290150S0130E0)."""
    sec = (props.get("FRSTDIVLAB") or props.get("FRSTDIVNO") or "").lstrip("0")
    pid = props.get("PLSSID") or ""
    town = ""
    if len(pid) >= 14:
        try:
            town = f"T{int(pid[4:7])}{pid[8]} R{int(pid[9:12])}{pid[13]}"
        except ValueError:
            town = ""
    return " · ".join(x for x in (f"Sec {sec}" if sec else "", town) if x)


def fetch_cell(i: int, j: int, cache: Path | None = None) -> dict:
    """The sections touching one cell, as GeoJSON with a 'label' on each."""
    path = cache / "plss" / f"{i}_{j}.json" if cache else None
    if path and path.exists():
        return json.loads(path.read_text())
    w, s, e, n = cell_bbox(i, j)
    r = requests.get(PLSS, params=dict(
        geometry=f"{w},{s},{e},{n}", geometryType="esriGeometryEnvelope", inSR=4326, outSR=4326,
        spatialRel="esriSpatialRelIntersects", outFields="PLSSID,FRSTDIVNO,FRSTDIVLAB",
        returnGeometry="true", geometryPrecision=6, resultRecordCount=2000, f="geojson"), timeout=60)
    r.raise_for_status()
    data = r.json()
    if "features" not in data:
        raise ValueError(f"PLSS service answered without features: {str(data)[:200]}")
    feats = [{"type": "Feature", "geometry": f["geometry"], "properties": {"id": f["properties"].get("PLSSID", "") + "-" + str(f["properties"].get("FRSTDIVNO", "")), "label": label(f["properties"])}}
             for f in data["features"] if f.get("geometry")]
    out = {"type": "FeatureCollection", "features": feats}
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out))
    return out
