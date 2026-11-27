"""First-floor heights measured on the coast (landfall/parcels.py OLD_COASTAL_FLOOR_FT, ABOVE_BFE_FT).

1. Homes built before 1975 in A and V flood zones, surveyed after Helene and Ian (PRJ-6245,
   PRJ-5700; eval/research/buildings.csv): first floor above the ground.
2. Homes built 1985 on with a FEMA elevation certificate in the Ian survey (Fort Myers Beach):
   first floor elevation minus the base flood elevation.

Run: python scripts/first_floors.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
Q = [0.1, 0.25, 0.5, 0.75, 0.9]


def main() -> None:
    t = pd.read_csv(ROOT / "eval" / "research" / "buildings.csv", low_memory=False)
    t = t[t.first_floor_navd_ft.notna() & t.ground_ft.notna()].copy()
    t["h"] = t.first_floor_navd_ft - t.ground_ft
    zone = t.flood_zone.fillna("").str.upper().str[:1]
    old = t[(t.year_built < 1975) & zone.isin(["A", "V"])]
    print(f"pre-1975, A/V zones ({len(old)}): floor above ground, ft", old.h.quantile(Q).round(1).to_dict())
    print("  share 1-3 ft:", round(float(((old.h >= 1) & (old.h <= 3)).mean()), 2))
    f = pd.read_csv(ROOT / "data" / "research_raw" / "ian_fmb.csv", low_memory=False, encoding="utf-8-sig")
    n = lambda c: pd.to_numeric(f[c], errors="coerce")
    d = pd.DataFrame({"y": n("TA_ActYearBuilt"), "diff": n("FEC_FFE") - n("FEC_BFE")}).dropna()
    for lo, hi in ((0, 1974), (1975, 1984), (1985, 2100)):
        s = d[(d.y >= lo) & (d.y <= hi)]["diff"]
        print(f"elevation certificates built {lo}-{hi} ({len(s)}): floor minus BFE, ft", s.quantile(Q).round(1).to_dict())


if __name__ == "__main__":
    main()
