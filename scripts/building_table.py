"""One row per surveyed building: how and when it was built, and the damage
the field-validated survey found. The data for predicting damage from
construction.

    python scripts/building_table.py [--out eval/field/cedar_key_buildings.csv]

Reads eval/field/HxHelene_VDA_CedarKey.csv (DesignSafe PRJ-6245, assessor-
checked material, foundation, stories and first-floor height, and component
damage states) and adds, for each building's point, the county tax roll's
actual and effective year built, living area, land use and construction class
(Florida's statewide cadastral layer), and the building-rules era. No owner
names or addresses are fetched. Prints a first look at damage by era,
material, foundation, elevation and stories.
"""

import argparse
import csv
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from landfall.cost import CADASTRAL  # noqa: E402
from landfall.parcels import _point_query  # noqa: E402
from landfall.structures import code_era  # noqa: E402

SURVEY = Path("eval/field/HxHelene_VDA_CedarKey.csv")
ROLL_FIELDS = "ACT_YR_BLT,EFF_YR_BLT,TOT_LVG_AR,DOR_UC,CONST_CLAS,IMP_QUAL,NO_RES_UNT"
MATERIAL = {"W": "wood", "M": "masonry", "C": "concrete", "S": "steel", "H": "manufactured"}
FOUNDATION = {"S": "slab", "C": "crawlspace", "P": "piers", "I": "piles", "F": "fill", "W": "solid wall", "B": "basement"}
KEEP = ["FID", "x", "y", "firmzone", "grd_el_ft", "num_stories", "eff_yr_built", "occ_type", "occ_subtype",
        "building_type", "foundation_type", "mitigation_strategy", "FFE_NAVD88_ft", "FFE_elevation_description",
        "DS_roof", "DS_walls/floors", "DS_foundation", "DS_openings", "DS_attached/detached_structures", "DS_overall"]


def num(v):
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def build(out: Path) -> list[dict]:
    rows = list(csv.DictReader(SURVEY.open(encoding="utf-8-sig")))

    def roll(r):
        return _point_query(CADASTRAL, float(r["x"]), float(r["y"]), ROLL_FIELDS) or {}

    with ThreadPoolExecutor(12) as pool:
        rolls = list(pool.map(roll, rows))
    table = []
    for r, p in zip(rows, rolls):
        t = {k: r.get(k, "").strip() for k in KEEP}
        year = int(p["ACT_YR_BLT"]) if p.get("ACT_YR_BLT") else None
        ffe, grade = num(r["FFE_NAVD88_ft"]), num(r["grd_el_ft"])
        t.update(
            material=MATERIAL.get(t["building_type"], t["building_type"]),
            foundation=FOUNDATION.get(t["foundation_type"], t["foundation_type"]),
            first_floor_ft=round(max(0.0, ffe - grade), 1) if ffe is not None and grade is not None else "",
            year_built=year or "",
            roll_effective_year=p.get("EFF_YR_BLT") or "",
            roll_living_sqft=p.get("TOT_LVG_AR") or "",
            roll_land_use=p.get("DOR_UC") or "",
            roll_construction_class=p.get("CONST_CLAS") or "",
            code_era=code_era(year) or "",
        )
        table.append(t)
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(table[0]))
        w.writeheader()
        w.writerows(table)
    return table


def first_look(table: list[dict]) -> None:
    def ds(t):
        return int(num(t["DS_overall"]) or 0)

    def show(name, key):
        groups = defaultdict(list)
        for t in table:
            groups[key(t) or "unknown"].append(ds(t))
        print(f"\n== damage by {name} (survey overall DS; DS2+ = damaged, DS4+ = severe)")
        print(f"  {'group':58} {'n':>4} {'DS2+':>6} {'DS4+':>6} {'mean DS':>8}")
        for g, v in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            print(f"  {str(g)[:58]:58} {len(v):4} {sum(x >= 2 for x in v) / len(v):6.0%} {sum(x >= 4 for x in v) / len(v):6.0%} {sum(v) / len(v):8.2f}")

    def elevation(t):
        ff = num(t["first_floor_ft"])
        return None if ff is None else "at grade (<2 ft)" if ff < 2 else "2-6 ft up" if ff < 6 else "6 ft or more up"

    def decade(t):
        return f"{int(t['year_built']) // 10 * 10}s" if t["year_built"] else None

    print(f"{len(table)} buildings; tax-roll year built for {sum(bool(t['year_built']) for t in table)}")
    show("building-rules era (tax-roll year built)", lambda t: t["code_era"])
    show("decade built", decade)
    show("material (survey assessors)", lambda t: t["material"])
    show("foundation (survey assessors)", lambda t: t["foundation"])
    show("first-floor height (survey)", elevation)
    show("stories (survey assessors)", lambda t: t["num_stories"])
    show("FEMA flood zone", lambda t: t["firmzone"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("eval/field/cedar_key_buildings.csv"))
    args = ap.parse_args()
    first_look(build(args.out))
