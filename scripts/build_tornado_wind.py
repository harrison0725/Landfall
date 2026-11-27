"""How much of a tornado's recorded path gets each Enhanced Fujita level (landfall/tornadoes.py).

The SPC record gives each tornado a length, its largest width and its peak
rating, but the peak wind covers only a small part of that box. NWS damage
surveys map EF contours (the Damage Assessment Toolkit's damage polygons,
2008 on). This matches each mapped tornado to its SPC record (same day within
one, within 2 miles, same rating) and, for each rating, sums the area at or
above each EF level over the length x largest width of the matched records.

Writes landfall/history/tornado_wind.json:
  {"shares": {"1": [share >= EF0, share >= EF1], "2": [...], ...}, "tornadoes": {"1": n, ...}, "source": ...}

Run: python scripts/build_tornado_wind.py [SPC 1950-YYYY_actual_tornadoes.csv]   (needs shapely)
"""

from __future__ import annotations

import collections
import datetime as dt
import json
import math
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DAT = "https://services.dat.noaa.gov/arcgis/rest/services/nws_damageassessmenttoolkit/DamageViewer/FeatureServer/2/query"
SPC = "https://www.spc.noaa.gov/wcm/data/1950-2025_actual_tornadoes.csv"


def dat_polygons() -> list[dict]:
    feats, off = [], 0
    while True:
        q = urllib.parse.urlencode({"where": "1=1", "outFields": "efscale,event_id,stormdate,path_guid", "outSR": 4326,
                                    "f": "geojson", "resultRecordCount": 2000, "resultOffset": off})
        page = json.load(urllib.request.urlopen(f"{DAT}?{q}", timeout=300)).get("features", [])
        feats += page
        if len(page) < 2000:
            return feats
        off += 2000


def mapped_tornadoes(feats: list[dict]):
    """Per mapped tornado: its EF contour areas (sq mi at or above each level), centroid and day."""
    from shapely.geometry import shape
    from shapely.ops import unary_union

    groups = collections.defaultdict(list)
    for f in feats:
        p = f["properties"]
        if not f.get("geometry"):
            continue
        guid = p.get("path_guid") or ""
        groups[guid if guid and not guid.startswith("{0000") else (p.get("event_id"), p.get("stormdate"))].append(f)
    for v in groups.values():
        levels = collections.defaultdict(list)
        for f in v:
            e = (f["properties"].get("efscale") or "").upper().replace("EF", "")
            if e.isdigit():
                levels[int(e)].append(shape(f["geometry"]).buffer(0))
        if 0 not in levels or max(levels) < 1 or not v[0]["properties"].get("stormdate"):
            continue
        u = {e: unary_union(p) for e, p in levels.items()}
        c = u[0].centroid
        k = math.cos(math.radians(c.y)) * 69.17 * 69.17
        area = {e: u[e].area * k for e in u}
        if area[0] < 0.02:
            continue
        top = max(e for e in u if area[e] > 0)
        day = dt.datetime.fromtimestamp(v[0]["properties"]["stormdate"] / 1000, dt.timezone.utc).date()
        yield {"area": area, "top": top, "lon": c.x, "lat": c.y, "day": day}


def main() -> None:
    spc = pd.read_csv(sys.argv[1] if len(sys.argv) > 1 else SPC, low_memory=False)
    spc = spc[spc.yr >= 2008].copy()
    spc["day"] = pd.to_datetime(spc.date).dt.date
    spc.loc[spc.elat <= 0, "elat"] = spc.slat
    spc.loc[spc.elon >= 0, "elon"] = spc.slon
    rows = []
    for m in mapped_tornadoes(dat_polygons()):
        c = spc[spc.day.map(lambda d: abs((d - m["day"]).days) <= 1)]
        if c.empty:
            continue
        kx = math.cos(math.radians(m["lat"])) * 69.17
        ax, ay = (c.slon - m["lon"]) * kx, (c.slat - m["lat"]) * 69.17
        dx, dy = (c.elon - m["lon"]) * kx - ax, (c.elat - m["lat"]) * 69.17 - ay
        t = np.clip(-(ax * dx + ay * dy) / np.maximum(dx * dx + dy * dy, 1e-9), 0, 1)
        dist = np.hypot(ax + t * dx, ay + t * dy)
        j = dist.idxmin()
        s = c.loc[j]
        if dist[j] > 2 or int(s.mag) != m["top"]:
            continue
        rows.append({"rating": int(s.mag), "box": max(float(s.len), 0.05) * float(s.wid) / 1760,
                     **{f"a{e}": m["area"].get(e, 0.0) for e in range(6)}})
    r = pd.DataFrame(rows).drop_duplicates()
    shares, counts = {}, {}
    for R in (1, 2, 3):
        x = r[r.rating == R]
        shares[str(R)] = [round(min(1.0, x[f"a{e}"].sum() / x.box.sum()), 4) for e in range(R + 1)]
        counts[str(R)] = int(len(x))
    # EF4 and EF5 pooled (few mapped): the share at or above each level over both
    x = r[r.rating >= 4]
    pooled = [round(min(1.0, x[f"a{e}"].sum() / x.box.sum()), 4) for e in range(6)]
    shares["4"], shares["5"] = pooled[:5], pooled
    counts["4"] = counts["5"] = int(len(x))
    shares["0"] = [shares["1"][0]]
    out = {"shares": shares, "tornadoes": counts, "matched": int(len(r)),
           "source": "NWS Damage Assessment Toolkit EF contour polygons (2008 on) matched to the SPC tornado record; share of "
                     "length x largest width at or above each EF level, pooled by rating (EF4 and EF5 together)"}
    (ROOT / "landfall" / "history" / "tornado_wind.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
