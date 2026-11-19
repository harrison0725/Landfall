"""One row per structure CAL FIRE inspected after a wildfire: how and when it
was built, and the damage the inspectors recorded. The wildfire counterpart
of scripts/building_table.py.

    python scripts/fire_table.py [--storm la_fires] [--out eval/field/la_fires_dins.csv]

Source: CAL FIRE Damage Inspection (DINS) data-share views for the 2025
Eaton and Palisades fires (landfall/structures.py DINS). Street numbers,
street names and parcel numbers are left out. Prints a first look at damage
by roof, eaves, vents, siding, windows, decks, year built and structure type.
"""

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from landfall.structures import DINS, code_era  # noqa: E402

FIELDS = "DAMAGE,STRUCTURET,STRUCTUREC,ROOFCONSTR,EAVES,VENTSCREEN,EXTERIORSI,WINDOWPANE,DECKPORCHO,DECKPORCHE,PATIOCOVER,FENCEATTAC,YEARBUILT,NUMBEROFUN,INCIDENTNA,COMMUNITY,LATITUDE,LONGITUDE"
SEVERE = {"Destroyed (>50%)"}
DAMAGED = {"Affected (1-9%)", "Minor (10-25%)", "Major (26-50%)", "Destroyed (>50%)"}


def download(storm: str) -> list[dict]:
    rows = []
    for url in DINS[storm]:
        offset = 0
        while True:
            r = requests.get(url, params=dict(where="1=1", outFields=FIELDS, returnGeometry="false",
                                              resultOffset=offset, resultRecordCount=2000, f="json"), timeout=120)
            r.raise_for_status()
            feats = [f["attributes"] for f in r.json().get("features", [])]
            rows += feats
            if len(feats) < 2000:
                break
            offset += len(feats)
    return rows


def first_look(rows: list[dict]) -> None:
    print(f"{len(rows)} structures inspected")
    print("damage:", dict(sorted(((d, sum(r['DAMAGE'] == d for r in rows)) for d in {r['DAMAGE'] for r in rows}), key=lambda kv: -kv[1])))

    def show(name, key, min_n=30):
        groups = defaultdict(list)
        for r in rows:
            groups[key(r) or "unknown"].append(r["DAMAGE"])
        print(f"\n== damage by {name} (groups of {min_n}+)")
        print(f"  {'group':48} {'n':>6} {'damaged':>8} {'destroyed':>10}")
        for g, v in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            if len(v) >= min_n:
                print(f"  {str(g)[:48]:48} {len(v):6} {sum(x in DAMAGED for x in v) / len(v):8.0%} {sum(x in SEVERE for x in v) / len(v):10.0%}")

    def era(r):
        y = r.get("YEARBUILT")
        return code_era(int(y), "CA", "wildfire") if y and 1700 < int(y) <= 2026 else None

    def decade(r):
        y = r.get("YEARBUILT")
        return f"{int(y) // 10 * 10}s" if y and 1700 < int(y) <= 2026 else None

    show("fire", lambda r: r["INCIDENTNA"])
    show("building-rules era (year built)", era)
    show("decade built", decade)
    show("roof", lambda r: r["ROOFCONSTR"])
    show("eaves", lambda r: r["EAVES"])
    show("vent screens", lambda r: r["VENTSCREEN"])
    show("exterior siding", lambda r: r["EXTERIORSI"])
    show("window panes", lambda r: r["WINDOWPANE"])
    show("deck or porch", lambda r: r["DECKPORCHO"])
    show("structure type", lambda r: r["STRUCTURET"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--storm", default="la_fires")
    ap.add_argument("--out", type=Path, default=Path("eval/field/la_fires_dins.csv"))
    args = ap.parse_args()
    rows = download(args.storm)
    for r in rows:
        y = r.get("YEARBUILT")
        r["code_era"] = code_era(int(y), "CA", "wildfire") if y and 1700 < int(y) <= 2026 else ""
    with args.out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    first_look(rows)
