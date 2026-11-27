"""Roof shapes and garages of surveyed houses (landfall/hazards.py ROOF_GABLE_SHARE, GARAGE_SHARE).

From eval/research/buildings.csv: the WiSPD field surveys (DesignSafe PRJ-4186; Harvey, Irma,
Michael, Laura) recorded roof shape for 2,551 houses in Florida, Texas and Louisiana. Prints
the share of gable roofs by build era (a hip-and-gable roof counts half), and the odds of roof
damage for hip and mixed roofs against gable at the same gust, era and storm.

Run: python scripts/roof_shapes.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    import statsmodels.formula.api as smf

    t = pd.read_csv(ROOT / "eval" / "research" / "buildings.csv", low_memory=False)
    r = t.roof_shape.str.strip().str.lower()
    hip, gab = r.str.contains("hip", na=False), r.str.contains("gable", na=False)
    t["gable_share"] = np.where(hip & gab, 0.5, np.where(gab, 1.0, np.where(hip, 0.0, np.nan)))
    t["shape"] = np.where(hip & gab, "mixed", np.where(gab, "gable", np.where(hip, "hip", None)))
    w = pd.read_csv(ROOT / "data" / "research_raw" / "wispd.csv", low_memory=False)
    w = w[w["_project"].str.startswith("Hurricane") & (w["wind_damage_rating"] >= 0)]
    wy = pd.to_numeric(w["year_built"], errors="coerce").where(lambda s: s > 1800)
    g = (w["large_door_present"] == "yes").astype(float)
    print(f"{int(wy.notna().sum())} houses with year built: share with a garage (large door)")
    print(g.groupby(pd.cut(wy, [0, 1976, 2000, 2100]), observed=True).agg(["mean", "count"]).round(3).to_string())
    import statsmodels.formula.api as smf0
    w = w.assign(y=wy, gar=g, dm=(w["wind_damage_rating"] >= 2).astype(int),
                 ws=pd.to_numeric(w["wind_speed_ms"], errors="coerce") * 2.23694).dropna(subset=["y", "ws"])
    w = w.assign(new=(w.y >= 2002).astype(int), mid=((w.y >= 1976) & (w.y < 2002)).astype(int))
    mg = smf0.logit("dm ~ np.log(ws) + gar + new + mid + C(_project)", w).fit(disp=0)
    cg = np.exp(mg.conf_int().loc["gar"])
    print(f"odds of wind damage with a garage vs without, same gust, era, storm: {np.exp(mg.params['gar']):.2f} ({cg[0]:.2f}-{cg[1]:.2f})")
    k = t.dropna(subset=["gable_share"]).copy()
    k["era"] = pd.cut(k.year_built, [0, 1975, 2001, 2100], labels=["before 1976", "1976-2001", "2002 on"])
    print(f"{len(k)} houses with a hip or gable roof recorded")
    print(k.groupby("era", observed=True).gable_share.agg(["mean", "count"]).round(3).to_string())
    print(k.groupby("state").gable_share.agg(["mean", "count"]).round(3).to_string())
    k = k.dropna(subset=["gust_mph", "obs_roof_damaged"])
    k["new"], k["mid"] = (k.year_built >= 2002).astype(int), ((k.year_built >= 1976) & (k.year_built < 2002)).astype(int)
    m = smf.logit('obs_roof_damaged ~ np.log(gust_mph) + C(shape, Treatment("gable")) + new + mid + C(event)', k).fit(disp=0)
    ci = np.exp(m.conf_int())
    for s in ("hip", "mixed"):
        n = f'C(shape, Treatment("gable"))[T.{s}]'
        print(f"odds of roof damage, {s} vs gable: {np.exp(m.params[n]):.2f} ({ci.loc[n, 0]:.2f}-{ci.loc[n, 1]:.2f})")


if __name__ == "__main__":
    main()
