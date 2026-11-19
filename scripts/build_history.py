"""The past buildings Landfall's damage odds look up (landfall/odds.py):
one row per inspected building with its year built, wall material,
foundation, the green around it, and whether it was damaged.

    python scripts/build_history.py

- landfall/history/helene_cedar_key.csv from eval/field/cedar_key_buildings.csv
  (Helene, DesignSafe PRJ-6245 survey; damaged = damage state 2+, severe = 4+).
  Green is read from Esri's satellite photo around each point (odds.green_at).
- landfall/history/la_fires_dins.csv from eval/field/la_fires_dins.csv (CAL
  FIRE; damaged = minor or worse, severe = destroyed). Siding isn't wall
  material (stucco is mostly on wood frames there), so material is left out;
  matching there is on age.
No addresses or parcel numbers are kept.
"""

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from landfall.odds import HISTORY, green_at  # noqa: E402

MAT = {"wood": "wood frame", "manufactured": "manufactured (mobile) home"}
COLS = ["year_built", "material", "foundation", "green", "damaged", "severe"]


def helene() -> None:
    rows = list(csv.DictReader(open("eval/field/cedar_key_buildings.csv")))
    greens = green_at([(float(r["x"]), float(r["y"])) for r in rows])
    out = []
    for r, g in zip(rows, greens):
        ds = int(float(r["DS_overall"] or 0))
        out.append({"year_built": r["year_built"], "material": MAT.get(r["material"], r["material"]), "foundation": r["foundation"],
                    "green": g or "", "damaged": int(ds >= 2), "severe": int(ds >= 4)})
    write("helene_cedar_key.csv", out)


def la_fires() -> None:
    out = []
    for r in csv.DictReader(open("eval/field/la_fires_dins.csv")):
        d = r["DAMAGE"]
        if d == "Inaccessible":
            continue
        y = r["YEARBUILT"]
        out.append({"year_built": y if y and 1700 < int(float(y)) <= 2026 else "", "material": "", "foundation": "", "green": "",
                    "damaged": int(d in ("Minor (10-25%)", "Major (26-50%)", "Destroyed (>50%)")), "severe": int(d == "Destroyed (>50%)")})
    for r in out:
        if r["year_built"]:
            r["year_built"] = int(float(r["year_built"]))
    write("la_fires_dins.csv", out)


def write(name: str, rows: list[dict]) -> None:
    HISTORY.mkdir(exist_ok=True)
    with (HISTORY / name).open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    print(name, len(rows), "rows,", sum(r["damaged"] for r in rows), "damaged")


if __name__ == "__main__":
    helene()
    la_fires()
