"""Sea level by 2050 at US tide gauges, for "Over the years" in a 2050 climate
(landfall/climate.py). From the 2022 Interagency Sea Level Rise Technical
Report (Sweet et al. 2022, NOAA Technical Report NOS 01), its dataset
"SLR_TF U.S. Sea Level Projections" (Zenodo record 17247198).

For each tide gauge: the Intermediate scenario (1.0 m of global rise by
2100), its 17th, 50th and 83rd percentiles of relative sea level in 2050,
measured from 1992 (the middle of the 1983-2001 tidal epoch that high-tide
datums, and so Landfall's surge levels, refer to): offset 1992-2000 + offset
2000-2005 + RSL2050. Writes landfall/history/sea_level_2050.json.

Run: python scripts/build_sea_level.py "path/to/SLR_TF U.S. Sea Level Projections.csv"
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CM_PER_FT = 30.48


def main(path: str) -> None:
    rows = list(csv.reader(open(path, encoding="latin-1")))
    head = next(i for i, r in enumerate(rows) if r and r[0] == "PSMSL Site")
    cols = rows[head]
    ix = {c: cols.index(c) for c in ("PSMSL Site", "NOAA ID", "NOAA Name", "Lat", "Long", "Scenario", "Offset 1992 to 2000 (cm)", "Offset 2000 to 2005 (cm)", "RSL2050 (cm)")}
    gauges: dict[str, dict] = {}
    gmsl = {}
    for r in rows[head + 1:]:
        if len(r) <= ix["RSL2050 (cm)"] or not r[ix["Scenario"]].startswith("1.0 - "):
            continue
        pct = r[ix["Scenario"]].split(" - ")[1].lower()
        rise = (float(r[ix["Offset 1992 to 2000 (cm)"]]) + float(r[ix["Offset 2000 to 2005 (cm)"]]) + float(r[ix["RSL2050 (cm)"]])) / CM_PER_FT
        if r[ix["PSMSL Site"]] == "GMSL":
            gmsl[pct] = round(rise, 2)
            continue
        noaa = r[ix["NOAA ID"]]
        if not noaa or noaa == "0":
            continue
        g = gauges.setdefault(noaa, {"id": noaa, "name": r[ix["NOAA Name"]], "lat": float(r[ix["Lat"]]), "lon": float(r[ix["Long"]])})
        g[pct] = round(rise, 2)
    out = {"source": "2022 Interagency Sea Level Rise Technical Report (Sweet et al. 2022, NOAA NOS 01), Intermediate scenario, "
                     "relative sea level in 2050 above 1992 at NOAA tide gauges; 17th, 50th and 83rd percentiles",
           "global_ft": gmsl, "gauges": [g for g in gauges.values() if {"low", "med", "high"} <= g.keys()]}
    p = ROOT / "landfall" / "history" / "sea_level_2050.json"
    p.write_text(json.dumps(out, separators=(",", ":")))
    print(len(out["gauges"]), "gauges; global", gmsl)


if __name__ == "__main__":
    main(sys.argv[1])
