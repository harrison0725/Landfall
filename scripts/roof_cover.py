"""Does roof cover (shingle, metal, tile) change wind damage? (landfall/history/roof_cover.json)

Hazus's house models have no roof-cover setting. The WiSPD field surveys (DesignSafe PRJ-4186;
Harvey, Irma, Michael, Laura) recorded roof cover on 2,804 houses with year built and wind.
1. Odds of wind damage (StEER rating 2+) and bad damage (3+) by cover against laminated
   shingles, at the same gust, build era and storm (logistic regression).
2. Held-out check: for each storm, fit the odds on the other three and apply them on top of
   Landfall's own held-out predictions (eval/research/predictions.csv, v2), relative to that
   era's mix of covers, so an unknown cover leaves the prediction unchanged. Log loss with and
   without.

Run: python scripts/roof_cover.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
COVERS = ("laminated shingles", "3-tab shingles", "metal", "tile")
ERAS = ((1976, "to 1976"), (2000, "1977-2000"), (9999, "2001 on"))


def load() -> pd.DataFrame:
    w = pd.read_csv(ROOT / "data" / "research_raw" / "wispd.csv", low_memory=False)
    w = w[w["_project"].str.startswith("Hurricane") & (w["wind_damage_rating"] >= 0)].copy()
    rc = w["roof_cover"].str.lower().fillna("")
    w["cover"] = np.select([rc.str.contains("laminated"), rc.str.contains("3-tab"), rc.str.contains("metal"), rc.str.contains("tile")], COVERS, "")
    w["y"] = pd.to_numeric(w["year_built"], errors="coerce").where(lambda s: s > 1800)
    w["ws"] = pd.to_numeric(w["wind_speed_ms"], errors="coerce") * 2.23694
    w = w[(w["cover"] != "")].dropna(subset=["y", "ws"])
    w["era"] = [next(n for top, n in ERAS if y <= top) for y in w["y"]]
    w["new"], w["mid"] = (w.y >= 2002).astype(int), ((w.y >= 1976) & (w.y < 2002)).astype(int)
    w["d"], w["s"] = (w["wind_damage_rating"] >= 2).astype(int), (w["wind_damage_rating"] >= 3).astype(int)
    w["id"] = "W" + w["_record_id"].astype(str)
    return w


def fit(w: pd.DataFrame, y: str) -> dict:
    import statsmodels.formula.api as smf

    m = smf.logit(f'{y} ~ np.log(ws) + C(cover, Treatment("laminated shingles")) + new + mid + C(_project)', w).fit(disp=0)
    out = {"laminated shingles": [1.0, 1.0, 1.0]}
    ci = np.exp(m.conf_int())
    for c in COVERS[1:]:
        n = f'C(cover, Treatment("laminated shingles"))[T.{c}]'
        out[c] = [round(float(np.exp(m.params[n])), 3), round(float(ci.loc[n, 0]), 3), round(float(ci.loc[n, 1]), 3)]
    return out


def mix(w: pd.DataFrame) -> dict:
    return {n: {c: round(float((g["cover"] == c).mean()), 3) for c in COVERS} for n, g in w.groupby("era")}


def factor(odds: dict, mixes: dict, era: str, cover: str) -> float:
    """Odds multiplier for this cover relative to the era's average cover (geometric mean of odds)."""
    avg = sum(mixes[era][c] * np.log(odds[c][0]) for c in COVERS)
    return float(np.exp(np.log(odds[cover][0]) - avg))


def held_out(w: pd.DataFrame) -> dict:
    pred = pd.read_csv(ROOT / "eval" / "research" / "predictions.csv").set_index("id")
    w = w[w["id"].isin(pred.index)].copy()
    w["p_d"], w["p_s"] = pred.loc[w["id"], "v2_p_wind_d"].to_numpy(), pred.loc[w["id"], "v2_p_wind_s"].to_numpy()
    res = {}
    for kind, y, p in (("damaged", "d", "p_d"), ("badly damaged", "s", "p_s")):
        base = adj = 0.0
        for ev in w["_project"].unique():
            tr, te = w[w["_project"] != ev], w[w["_project"] == ev]
            odds, mixes = fit(tr, y), mix(tr)
            q = te[p].clip(1e-4, 1 - 1e-4).to_numpy()
            f = np.array([factor(odds, mixes, e, c) for e, c in zip(te["era"], te["cover"])])
            qa = q * f / (1 - q + q * f)
            o = te[y].to_numpy()
            ll = lambda r: -np.mean(o * np.log(r) + (1 - o) * np.log(1 - r))
            base += ll(q) * len(te)
            adj += ll(qa) * len(te)
        res[kind] = {"logloss_without": round(base / len(w), 4), "logloss_with": round(adj / len(w), 4), "houses": len(w)}
    return res


def main() -> None:
    w = load()
    out = {
        "source": "WiSPD v1.0 field surveys (DesignSafe PRJ-4186; Harvey, Irma, Michael, Laura): odds of StEER wind damage "
                  "rating 2+ (damaged) and 3+ (badly damaged) by roof cover against laminated shingles, at the same gust, "
                  "build era and storm; [odds ratio, 95% low, 95% high]",
        "houses": len(w),
        "damaged": fit(w, "d"), "severe": fit(w, "s"),
        "era_mix": mix(w),
        "held_out": held_out(w),
    }
    (ROOT / "landfall" / "history" / "roof_cover.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("houses", "damaged", "severe", "era_mix", "held_out")}, indent=1))


if __name__ == "__main__":
    main()
