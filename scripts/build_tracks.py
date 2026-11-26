"""Atlantic hurricane tracks for "chances over time" (landfall/climate.py).

Reads NOAA's National Hurricane Center best-track record (HURDAT2, Atlantic,
https://www.nhc.noaa.gov/data/hurdat/) and keeps every storm from 1950 on:
the years reconnaissance aircraft flew and the record is reliable near the US
coast (Landsea and Franklin 2013). Writes landfall/history/hurricane_tracks.json:

  {"source": ..., "first_year": 1950, "last_year": 2025,
   "storms": [{"id": "AL092024", "name": "HELENE", "year": 2024, "start": "2024-09-24T06:00Z",
               "t": [hours since the first fix], "lat": [...], "lon": [...],
               "kt": [1-minute wind, kt], "rmw": [radius of maximum wind, nm, or 0],
               "landfall": [indexes of fixes marked as landfall]}]}

Run: python scripts/build_tracks.py [path to a downloaded hurdat2 file]
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = "https://www.nhc.noaa.gov/data/hurdat/"
FIRST_YEAR = 1950


def download() -> tuple[str, str]:
    html = urllib.request.urlopen(INDEX, timeout=60).read().decode()
    names = sorted(set(re.findall(r"hurdat2-1851-\d{4}-\d+\.txt", html)), key=lambda n: (n.split("-")[2], n))
    name = names[-1]
    return name, urllib.request.urlopen(INDEX + name, timeout=120).read().decode()


def parse(text: str) -> list[dict]:
    storms, cur, t0 = [], None, None
    for line in text.splitlines():
        f = [x.strip() for x in line.split(",")]
        if line.startswith("AL"):
            cur = {"id": f[0], "name": f[1], "year": int(f[0][4:8]), "t": [], "lat": [], "lon": [], "kt": [], "rmw": [], "landfall": []}
            storms.append(cur)
            t0 = None
            continue
        if cur is None or len(f) < 8:
            continue
        when = dt.datetime.strptime(f[0] + f[1].zfill(4), "%Y%m%d%H%M")
        if t0 is None:
            t0 = when
            cur["start"] = when.strftime("%Y-%m-%dT%H:%MZ")
        lat = float(f[4][:-1]) * (1 if f[4].endswith("N") else -1)
        lon = float(f[5][:-1]) * (-1 if f[5].endswith("W") else 1)
        kt = int(f[6])
        rmw = int(f[20]) if len(f) > 20 and f[20] not in ("", "-999") else 0
        if f[2] == "L":
            cur["landfall"].append(len(cur["t"]))
        cur["t"].append(round((when - t0).total_seconds() / 3600, 2))
        cur["lat"].append(lat)
        cur["lon"].append(lon)
        cur["kt"].append(max(kt, 0))
        cur["rmw"].append(max(rmw, 0))
    return storms


def main() -> None:
    if len(sys.argv) > 1:
        name, text = Path(sys.argv[1]).name, Path(sys.argv[1]).read_text()
    else:
        name, text = download()
    storms = [s for s in parse(text) if s["year"] >= FIRST_YEAR and max(s["kt"]) >= 34]
    out = {
        "source": f"NOAA National Hurricane Center best tracks, HURDAT2 Atlantic ({name}); {FIRST_YEAR} on, when aircraft "
                  "reconnaissance makes the record reliable near the US coast",
        "first_year": FIRST_YEAR,
        "last_year": max(s["year"] for s in storms),
        "storms": storms,
    }
    p = ROOT / "landfall" / "history" / "hurricane_tracks.json"
    p.write_text(json.dumps(out, separators=(",", ":")))
    print(len(storms), "storms", out["first_year"], "-", out["last_year"], round(p.stat().st_size / 1e6, 2), "MB")


if __name__ == "__main__":
    main()
