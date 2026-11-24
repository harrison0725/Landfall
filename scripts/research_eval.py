"""Research checks of Landfall's damage odds against every public survey in
eval/research/buildings.csv (made by scripts/research_data.py). Writes
eval/research/results.json and predictions.csv, summarized by hand in
eval/research/README.md.

Two versions of Landfall are scored, each building judged only from what can
be known before a storm (walls, foundation, stories, year built, flood zone,
design wind) plus the storm's measured wind and water:

* v1, what Landfall does today: Hazus wind odds as they are, and water
  damage rates and first-floor heights measured at Cedar Key only.
* v2, learned from the other storms: Hazus wind odds recalibrated against
  roof damage found (logit(p) = a + b logit(Hazus) + build-era terms), and
  water damage rates and first-floor heights pooled from the other surveyed
  towns. Every place is scored with numbers fit WITHOUT that place (leave one
  place out), so each score is a test on an unseen storm or town.

Then:
* ranges: how wide a 90% "likely between" range must be, from how far off
  each held-out place was, the wind field's and the water's uncertainty, and
  chance; and whether those ranges held the true counts;
* baselines and drop-one-fact tests: does knowing how and when a building
  was built help, against same-rate-for-all, hazard-only, year-only and
  distance-to-coast-only models and a data-driven model;
* the build-year effect at equal wind, with a cluster-robust logistic model.

Run: python scripts/research_eval.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from landfall import hazards  # noqa: E402

hazards.USE_CALIBRATION = False  # v1 is Landfall without calibration.json; v2 is built here

OUT = ROOT / "eval" / "research"
RNG = np.random.default_rng(20261123)
FACTS = ["material", "foundation", "stories", "year_built", "flood_zone"]
FACT_NAMES = {"material": "walls", "foundation": "foundation", "stories": "stories", "year_built": "year built", "flood_zone": "flood zone"}
WATER_BINS = [(-99.0, 0.0), (0.0, 3.0), (3.0, 6.0), (6.0, 9.0), (9.0, 99.0)]  # ft of water above the first floor
DS_PLACES = ("Helene 2024, Cedar Key", "Helene 2024, Horseshoe Beach", "Ian 2022, Fort Myers Beach")


def load() -> pd.DataFrame:
    t = pd.read_csv(OUT / "buildings.csv", low_memory=False)
    t["place"] = np.where(t["event"].str.startswith(("Helene", "Ian")), t["event"] + ", " + t["town"].astype(str), t["event"])
    t["fold"] = t["id"].str[1:].str.extract(r"(\d+)")[0].astype(float).fillna(0) % 2
    # roof damage as the wind part of each survey: DS surveys rate the roof
    # (DS2+ damaged, DS4+ badly); WiSPD rates wind damage (2+ / 3+)
    r, w = t["obs_roof"], t["wind_rating"]
    t["obs_roof_damaged"] = np.where(r.notna(), r >= 2, np.where(w.notna(), w >= 2, np.nan))
    t["obs_roof_severe"] = np.where(r.notna(), r >= 4, np.where(w.notna(), w >= 3, np.nan))
    t["above_floor_ft"] = t["water_surface_ft"] - t["first_floor_navd_ft"]
    t["floor_ft"] = t["first_floor_navd_ft"] - t["ground_ft"]
    t["obs_water_d"] = t[["obs_walls", "obs_foundation"]].max(axis=1) >= 2
    t["obs_water_s"] = t[["obs_walls", "obs_foundation"]].max(axis=1) >= 4
    return t


def wind_only(t: pd.DataFrame) -> np.ndarray:
    """Buildings whose roof rating reflects wind alone: WiSPD houses that saw
    no surge, and surveyed buildings where the water stayed below the first
    floor (in a house the surge wrecked, the roof is rated wrecked too)."""
    wispd = t["wind_rating"].notna() & (t["hazards_present"] == "Wind")
    dry = t["obs_roof"].notna() & (t["obs_wet"] == 0)
    return (wispd | dry).to_numpy()


def building(r, drop: str | None = None) -> dict:
    def v(k):
        x = r[k]
        return None if (x is None or (isinstance(x, float) and math.isnan(x)) or k == drop) else x

    y = v("year_built")
    return {"material": v("material"), "foundation": v("foundation"), "stories": int(v("stories") or 1) if drop != "stories" else 1,
            "year_built": int(y) if y else None, "flood_zone": v("flood_zone"), "v_ult": r["v_ult"] if not math.isnan(r["v_ult"]) else None, "green": None}


def hazus_wind(t: pd.DataFrame, drop: str | None = None, k: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    xs = np.array(hazards._table("hazus_wind.json")["speeds_mph"], dtype=float)
    g = t["gust_mph"].to_numpy() * k
    rows = [hazards._wind_odds(hazards.wind_key(building(r, drop), r["state"])) for _, r in t.iterrows()]
    return (np.array([np.interp(g[i], xs, r_[0]) for i, r_ in enumerate(rows)]),
            np.array([np.interp(g[i], xs, r_[1]) for i, r_ in enumerate(rows)]))


# ------------------------------------------------------------------ wind recalibration

def _logit(p):
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def _era(year) -> tuple[np.ndarray, np.ndarray]:
    y = np.nan_to_num(np.asarray(year, float))
    return (y >= 2002).astype(float), ((y >= 1976) & (y < 2002)).astype(float)


def fit_recal(p_hz, year, y) -> list[float]:
    """logit(p) = a + b logit(Hazus) + c [built 2002+] + d [built 1976-2001]."""
    import statsmodels.api as sm

    new, mid = _era(year)
    X = sm.add_constant(np.c_[_logit(p_hz), new, mid], has_constant="add")
    return [float(v) for v in sm.Logit(np.asarray(y, float), X).fit(disp=0).params]


def apply_recal(params, p_hz, year) -> np.ndarray:
    a, b, c, d = params
    new, mid = _era(year)
    return 1 / (1 + np.exp(-(a + b * _logit(p_hz) + c * new + d * mid)))


# ------------------------------------------------------------------ water rates and first floors

def _pava(rates, ns):
    import build_hazards as bh

    return bh._increasing(rates, ns)


def _rates(rows: pd.DataFrame, bins) -> dict:
    d = rows[rows["above_floor_ft"].notna() & rows[["obs_walls", "obs_foundation"]].notna().any(axis=1)]
    out = {}
    for zone in ("V", "other"):
        z = d[d["flood_zone"].fillna("").str.upper().str.startswith("V") == (zone == "V")]
        ns, dm, sv = [], [], []
        for lo, hi in bins:
            m = (z["above_floor_ft"] >= lo) & (z["above_floor_ft"] < hi)
            ns.append(int(m.sum()))
            dm.append(float(z.loc[m, "obs_water_d"].mean()) if m.any() else 0.0)
            sv.append(float(z.loc[m, "obs_water_s"].mean()) if m.any() else 0.0)
        out[zone] = {"bins_ft": [list(b) for b in bins], "buildings": ns, "damaged": _pava(dm, ns), "severe": _pava(sv, ns)}
    return out


def fit_water(t: pd.DataFrame) -> dict:
    """Chance of walls/floors or foundation damage (DS2+; DS4+ for badly
    damaged) from the water: logistic in water above the first floor, water
    above the ground (deeper water carries bigger waves, which the survey
    towns show matters beyond the depth over the floor) and V zone."""
    import statsmodels.api as sm

    d = t[t["above_floor_ft"].notna() & t[["obs_walls", "obs_foundation"]].notna().any(axis=1) & t["water_ft"].notna()]
    X = sm.add_constant(np.c_[d["above_floor_ft"].clip(-10, 20), d["water_ft"].clip(0, 25),
                              d["flood_zone"].fillna("").str.upper().str.startswith("V").astype(float)], has_constant="add")
    out = {"model": "logistic: const, ft above first floor (clipped -10..20), ft above ground (0..25), V zone"}
    for key, col in (("damaged", "obs_water_d"), ("severe", "obs_water_s")):
        out[key] = [round(float(v), 4) for v in sm.Logit(d[col].astype(float), X).fit(disp=0).params]
    out["buildings"] = int(len(d))
    return out


def flood_logistic(depth: float, b: dict, model: dict, floors: dict) -> tuple[float, float, float]:
    """Like hazards.flood_at, averaging over first-floor heights for the
    building's foundation, but with the logistic water-damage model."""
    fl = np.array(floors.get(b.get("foundation") or "", floors["any"]) or floors["any"])
    v = float((b.get("flood_zone") or "").upper().startswith("V"))
    above = np.clip(depth - fl, -10, 20)
    agl = min(max(depth, 0.0), 25.0)

    def p(c):
        return float(np.mean(1 / (1 + np.exp(-(c[0] + c[1] * above + c[2] * agl + c[3] * v)))))

    return p(model["damaged"]), p(model["severe"]), float(np.mean(depth > fl))


def fit_floors(t: pd.DataFrame) -> dict:
    d = t[t["floor_ft"].notna() & (t["floor_ft"] > -2) & (t["floor_ft"] < 30)]
    floors = {f: sorted(round(float(x), 1) for x in g["floor_ft"]) for f, g in d.groupby(d["foundation"].fillna("unknown"))}
    floors["any"] = sorted(round(float(x), 1) for x in d["floor_ft"])
    return floors


def water(t: pd.DataFrame, rates: dict | None, floors: dict | None, drop: str | None = None, shift: float = 0.0) -> tuple[np.ndarray, ...]:
    tab = hazards._table("flood_floors.json")
    saved = tab["water_damage"], tab["first_floor_ft"]
    fd, fs, wet = np.zeros(len(t)), np.zeros(len(t)), np.full(len(t), np.nan)
    try:
        if rates is not None:
            tab["water_damage"] = rates
        if floors is not None:
            tab["first_floor_ft"] = floors
        for i, (_, r) in enumerate(t.iterrows()):
            x = r["water_ft"]
            if isinstance(x, float) and math.isnan(x):
                continue
            x = max(0.0, x + shift)
            if x > 0:
                if rates is not None and "model" in rates:
                    fd[i], fs[i], wet[i] = flood_logistic(x, building(r, drop), rates, tab["first_floor_ft"])
                else:
                    fd[i], fs[i], _l, wet[i] = hazards.flood_at([x], building(r, drop), surge=True)
            else:
                wet[i] = 0.0
    finally:
        tab["water_damage"], tab["first_floor_ft"] = saved
    return fd, fs, wet


# ------------------------------------------------------------------ the two versions

class Fits:
    """v2's numbers for one held-out place, fit on every other place."""

    def __init__(self, t: pd.DataFrame, held: str | None, hz: tuple[np.ndarray, np.ndarray]):
        tr = (t["place"] != held).to_numpy() if held else np.ones(len(t), bool)
        wo = wind_only(t) & tr & t["obs_roof_damaged"].notna().to_numpy()
        self.recal_d = fit_recal(hz[0][wo], t["year_built"].to_numpy()[wo], t["obs_roof_damaged"].to_numpy()[wo])
        self.recal_s = fit_recal(hz[1][wo], t["year_built"].to_numpy()[wo], t["obs_roof_severe"].to_numpy()[wo])
        ds = t[tr & t["place"].isin(DS_PLACES).to_numpy()]
        self.rates, self.floors = fit_water(ds), fit_floors(ds)


def run(t: pd.DataFrame, version: str, fits: dict | None = None, drop: str | None = None, k: float = 1.0, shift: float = 0.0,
        hz: tuple | None = None) -> pd.DataFrame:
    hz = hz if hz is not None else hazus_wind(t, drop, k)
    out = pd.DataFrame(index=t.index, columns=["p_wind_d", "p_wind_s", "p_d", "p_s", "p_wet"], dtype=float)
    for place, g in t.groupby("place", sort=False):
        m = (t["place"] == place).to_numpy()
        wd, ws = hz[0][m], hz[1][m]
        if version == "v1":
            if place.endswith("Cedar Key"):  # its own rates come from its own survey: use the other half (2-fold)
                import build_hazards as bh

                fd, fs, wet = np.zeros(m.sum()), np.zeros(m.sum()), np.full(m.sum(), np.nan)
                for f in (0, 1):
                    h = (g["fold"] == f).to_numpy()
                    other = g[g["fold"] != f]
                    fd[h], fs[h], wet[h] = water(g[h], _rates(other, bh.WATER_BINS), None, drop, shift)
            else:
                fd, fs, wet = water(g, None, None, drop, shift)
        else:
            F = fits[place]
            yb = g["year_built"].to_numpy() if drop != "year_built" else np.full(m.sum(), np.nan)
            wd, ws = apply_recal(F.recal_d, wd, yb), apply_recal(F.recal_s, ws, yb)
            fd, fs, wet = water(g, F.rates, F.floors, drop, shift)
        out.loc[m, "p_wind_d"], out.loc[m, "p_wind_s"] = wd, ws
        out.loc[m, "p_d"], out.loc[m, "p_s"], out.loc[m, "p_wet"] = 1 - (1 - wd) * (1 - fd), 1 - (1 - ws) * (1 - fs), wet
    return out


# ------------------------------------------------------------------ metrics

def _clean(y, p):
    y, p = np.asarray(y, float), np.asarray(p, float)
    ok = ~np.isnan(y) & ~np.isnan(p)
    return y[ok], p[ok]


def auc(y, p) -> float:
    from sklearn.metrics import roc_auc_score

    y, p = _clean(y, p)
    return float(roc_auc_score(y, p)) if 0 < y.sum() < len(y) else float("nan")


def logloss(y, p) -> float:
    y, p = _clean(y, p)
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def score(y, p) -> dict:
    y, p = _clean(y, p)
    idx = RNG.integers(0, len(y), (1000, len(y)))
    r = p[idx].sum(1) / np.maximum(y[idx].sum(1), 1)
    return {"buildings": int(len(y)), "found": int(y.sum()), "predicted": round(float(p.sum()), 1), "ratio": round(float(p.sum() / max(y.sum(), 1)), 2),
            "ratio_90": [round(float(np.percentile(r, 5)), 2), round(float(np.percentile(r, 95)), 2)],
            "auc": round(auc(y, p), 3), "logloss": round(logloss(y, p), 4), "brier": round(float(np.mean((p - y) ** 2)), 4)}


def by_place(t: pd.DataFrame, p: pd.DataFrame) -> dict:
    out = {}
    wo = wind_only(t)
    for place, g in t.groupby("place", sort=False):
        q = p.loc[g.index]
        m = wo[g.index]
        res = {"gust_mph": round(float(g["gust_mph"].median())), "water_ft": None if g["water_ft"].isna().all() else round(float(g["water_ft"].median()), 1),
               "damaged": score(g["obs_damaged"], q["p_d"]), "badly_damaged": score(g["obs_severe"], q["p_s"])}
        if m.sum() >= 20:
            res["roof_damaged_wind_only"] = score(g.loc[m, "obs_roof_damaged"], q.loc[m, "p_wind_d"])
        w = g["obs_wet"].notna() & q["p_wet"].notna()
        if w.any():
            res["water_inside"] = score(g.loc[w, "obs_wet"], q.loc[w, "p_wet"])
        out[place] = res
    return out


# ------------------------------------------------------------------ ranges

def ranges(t: pd.DataFrame, p: pd.DataFrame, fits: dict, hz) -> dict:
    """90% ranges for the count of damaged buildings in each place: the spread
    of held-out predicted/found ratios at the OTHER places (log scale), the
    wind field's uncertainty (gust x e^N(0, 0.10); NIST/ARA fits explain ~70%
    of station variance), the water's (each survey's own +-), and chance."""
    places = list(dict.fromkeys(t["place"]))
    logr = {pl: math.log(max(p.loc[t["place"] == pl, "p_d"].sum(), 1) / max(t.loc[t["place"] == pl, "obs_damaged"].sum(), 1)) for pl in places}
    out = {}
    for pl in places:
        sd = float(np.sqrt(np.mean([v * v for q, v in logr.items() if q != pl])))
        m = (t["place"] == pl).to_numpy()
        g = t[m]
        sims = []
        for _ in range(40):
            k = math.exp(RNG.normal(0, 0.10))
            sh = RNG.normal(0, float(g["water_sd_ft"].mean())) if g["water_sd_ft"].notna().any() else 0.0
            q = run(g, "v2", fits, k=k, shift=sh)["p_d"].to_numpy(float)
            for _ in range(10):
                pp = np.clip(q * math.exp(RNG.normal(0, sd)), 0, 1)
                sims.append(int((RNG.random(len(pp)) < pp).sum()))
        lo, hi = int(np.percentile(sims, 5)), int(np.percentile(sims, 95))
        found = int(g["obs_damaged"].sum())
        out[pl] = {"predicted": round(float(p.loc[m, "p_d"].sum())), "range_90": [lo, hi], "found": found, "inside": bool(lo <= found <= hi),
                   "model_sd_log_from_other_places": round(sd, 3), "buildings": int(m.sum())}
    return {"places": out, "log_ratio_held_out": {k: round(v, 3) for k, v in logr.items()},
            "model_sd_log_all": round(float(np.sqrt(np.mean([v * v for v in logr.values()]))), 3)}


# ------------------------------------------------------------------ baselines, drop-one, build year

def baselines(t: pd.DataFrame, preds: dict) -> dict:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression

    y = t["obs_damaged"].to_numpy(float)
    ev = t["place"].to_numpy()
    X = pd.DataFrame({"gust": t["gust_mph"], "water": t["water_ft"].fillna(0), "year": t["year_built"], "stories": t["stories"],
                      "coast": t["coast_miles"], "v_ult": t["v_ult"], "material": t["material"].fillna("unknown"),
                      "foundation": t["foundation"].fillna("unknown"), "zone": t["flood_zone"].fillna("unknown")})
    Xd = pd.get_dummies(X, columns=["material", "foundation", "zone"], dtype=float)
    hazard = ["gust", "water"]
    bldg = [c for c in Xd.columns if c not in ("gust", "water", "coast")]

    def loeo(cols, model="logit"):
        out = np.zeros(len(t))
        for e in pd.unique(ev):
            tr, te = ev != e, ev == e
            Z = Xd[cols].copy()
            if model == "logit":
                Z = Z.fillna(Z[tr].median())
                Z = (Z - Z[tr].mean()) / Z[tr].std().replace(0, 1)
                m = LogisticRegression(max_iter=3000).fit(Z[tr], y[tr])
            else:
                m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, random_state=0).fit(Z[tr], y[tr])
            out[te] = m.predict_proba(Z[te])[:, 1]
        return out

    preds = dict(preds)
    preds["Same rate for all (other places' rate)"] = np.array([y[ev != e].mean() for e in ev])
    preds["Year built only (fit on other places)"] = loeo(["year"])
    preds["Distance to coast only (fit on other places)"] = loeo(["coast"])
    preds["Wind and water only (fit on other places)"] = loeo(hazard)
    preds["Wind, water + building facts, logistic (fit on other places)"] = loeo(hazard + bldg)
    preds["Boosted trees, everything (fit on other places)"] = loeo(hazard + bldg + ["coast"], "gbm")
    places = list(pd.unique(ev))
    w = np.array([(ev == e).sum() for e in places])
    table = {}
    for name, q in preds.items():
        per = {e: {"auc": round(auc(y[ev == e], q[ev == e]), 3), "logloss": round(logloss(y[ev == e], q[ev == e]), 4),
                   "ratio": round(float(np.nansum(q[ev == e]) / max(y[ev == e].sum(), 1)), 2)} for e in places}
        a = np.array([per[e]["auc"] for e in places])
        ll = np.array([per[e]["logloss"] for e in places])
        table[name] = {"auc_within_place": round(float(np.nansum(a * w) / w[~np.isnan(a)].sum()), 3),
                       "logloss_mean_of_places": round(float(np.mean(ll)), 4),
                       "count_error_median": round(float(np.median([abs(math.log(max(per[e]["ratio"], 1e-3))) for e in places])), 3),
                       "by_place": per}
    imp = importance(Xd, y, ev, hazard + bldg + ["coast"])
    return {"models": table, "permutation_importance_auc_drop": imp}


def importance(Xd, y, ev, cols) -> dict:
    from sklearn.ensemble import HistGradientBoostingClassifier

    groups = {"wind": ["gust"], "water": ["water"], "year built": ["year"], "stories": ["stories"], "distance to coast": ["coast"],
              "design wind": ["v_ult"], "walls": [c for c in cols if c.startswith("material_")],
              "foundation": [c for c in cols if c.startswith("foundation_")], "flood zone": [c for c in cols if c.startswith("zone_")]}
    acc = {g: [] for g in groups}
    for e in pd.unique(ev):
        tr, te = ev != e, ev == e
        m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, random_state=0).fit(Xd.loc[tr, cols], y[tr])
        base = auc(y[te], m.predict_proba(Xd.loc[te, cols])[:, 1])
        for g, cs in groups.items():
            drops = []
            for _ in range(5):
                Z = Xd.loc[te, cols].copy()
                perm = RNG.permutation(len(Z))
                for c in cs:
                    Z[c] = Z[c].to_numpy()[perm]
                drops.append(base - auc(y[te], m.predict_proba(Z)[:, 1]))
            acc[g].append((te.sum(), float(np.mean(drops))))
    return {g: round(sum(n * d for n, d in v if not math.isnan(d)) / sum(n for n, d in v if not math.isnan(d)), 3) for g, v in acc.items()}


def year_effect(t: pd.DataFrame, hz) -> dict:
    """Roof damage found by build era within narrow wind bands, wind-only
    buildings, beside Hazus for the same buildings; and a logistic model of
    roof damage on log gust + era + place (standard errors clustered by place)."""
    import statsmodels.api as sm

    use = t["obs_roof_damaged"].notna().to_numpy() & wind_only(t) & t["year_built"].notna().to_numpy()
    g, yb, y = t["gust_mph"].to_numpy(), t["year_built"].to_numpy(), t["obs_roof_damaged"].to_numpy(float)
    bands, eras = [(70, 100), (100, 120), (120, 140), (140, 175)], [("before 1976", 0, 1975), ("1976-2001", 1976, 2001), ("2002 or later", 2002, 3000)]
    table = {}
    for lo, hi in bands:
        row = {}
        for name, a, b in eras:
            m = use & (g >= lo) & (g < hi) & (yb >= a) & (yb <= b)
            if m.sum() >= 25:
                r = y[m].mean()
                se = math.sqrt(r * (1 - r) / m.sum())
                row[name] = {"buildings": int(m.sum()), "found": round(float(r), 3), "found_90": [round(max(0.0, r - 1.645 * se), 3), round(min(1.0, r + 1.645 * se), 3)],
                             "hazus": round(float(hz[0][m].mean()), 3)}
        table[f"{lo}-{hi} mph"] = row
    d = t[use].copy()
    new, mid = _era(d["year_built"])
    groups = pd.factorize(d["place"])[0]
    X = pd.concat([pd.DataFrame({"log_gust": np.log(d["gust_mph"]), "built_2002_or_later": new, "built_1976_2001": mid}, index=d.index),
                   pd.get_dummies(d["place"], drop_first=True, dtype=float)], axis=1)
    fit = sm.Logit(d["obs_roof_damaged"].astype(float), sm.add_constant(X)).fit(disp=0, cov_type="cluster", cov_kwds={"groups": groups})
    off = sm.Logit(d["obs_roof_damaged"].astype(float), sm.add_constant(pd.DataFrame({"built_2002_or_later": new, "built_1976_2001": mid}, index=d.index)),
                   offset=_logit(hz[0][use])).fit(disp=0, cov_type="cluster", cov_kwds={"groups": groups})

    def ors(f):
        ci = f.conf_int()
        return {c: {"odds_ratio": round(math.exp(f.params[c]), 2), "ci95": [round(math.exp(ci.loc[c, 0]), 2), round(math.exp(ci.loc[c, 1]), 2)],
                    "p": round(float(f.pvalues[c]), 4)} for c in ("built_2002_or_later", "built_1976_2001")}

    return {"by_wind_band": table, "odds_ratios_vs_before_1976": ors(fit), "beyond_hazus_odds_ratios": ors(off), "buildings": int(use.sum()),
            "by_place": {p: int(n) for p, n in d["place"].value_counts().items()}}


# ------------------------------------------------------------------ main

def write_calibration(full: "Fits", res: dict) -> None:
    """What Landfall itself uses (landfall/history/calibration.json): v2 fit
    on every surveyed place, and the spread used for its "likely between" ranges."""
    cal = {
        "wind": {"damaged": [round(v, 4) for v in full.recal_d], "severe": [round(v, 4) for v in full.recal_s],
                 "form": "logit(p) = a + b logit(Hazus p) + c [built 2002 or later] + d [built 1976-2001]",
                 "source": "fit to roof damage found on wind-only buildings: WiSPD v1.0 (Harvey, Irma, Michael, Laura; DesignSafe PRJ-4186) "
                           "and the Helene and Ian virtual surveys where water stayed below the first floor (PRJ-6245, PRJ-5700); see eval/research"},
        "water": full.rates | {"source": "Helene 2024 (Cedar Key, Horseshoe Beach; PRJ-6245) and Ian 2022 (Fort Myers Beach; PRJ-5700): "
                                         "walls/floors or foundation damage state 2+ (damaged) and 4+ (badly damaged)"},
        "first_floor_ft": full.floors,
        "range_sd_log": res["ranges"]["model_sd_log_all"],
        "range_source": "root-mean-square of log(predicted / found) damaged counts over 7 surveyed places, each predicted "
                        "with numbers fit without it (eval/research/results.json)",
    }
    (ROOT / "landfall" / "history" / "calibration.json").write_text(json.dumps(cal, separators=(",", ":")))


def main() -> None:
    t = load()
    hz = hazus_wind(t)
    places = list(dict.fromkeys(t["place"]))
    fits = {pl: Fits(t, pl, hz) for pl in places}
    full = Fits(t, None, hz)
    v1 = run(t, "v1", hz=hz)
    v2 = run(t, "v2", fits, hz=hz)
    res = {"buildings": int(len(t)), "places": {pl: int((t["place"] == pl).sum()) for pl in places},
           "v1_today": by_place(t, v1), "v2_learned": by_place(t, v2),
           "v2_fit_on_all": {"wind_damaged": full.recal_d, "wind_badly": full.recal_s, "water": full.rates,
                             "floors_n": {k: len(v) for k, v in full.floors.items()}}}
    res["ranges"] = ranges(t, v2, fits, hz)
    drops = {"Landfall v1 (today)": v1["p_d"].to_numpy(float), "Landfall v2 (learned from other places)": v2["p_d"].to_numpy(float)}
    for f in FACTS:
        drops[f"v2 without {FACT_NAMES[f]}"] = run(t, "v2", fits, drop=f)["p_d"].to_numpy(float)
    t0 = t.copy()
    for f in FACTS:
        t0[f] = 1 if f == "stories" else None
    drops["v2, nothing known about the building"] = run(t0, "v2", fits)["p_d"].to_numpy(float)
    res["baselines"] = baselines(t, drops)
    res["year_effect"] = year_effect(t, hz)
    (OUT / "results.json").write_text(json.dumps(res, indent=1, default=float))
    write_calibration(full, res)
    pd.concat([t[["id", "place"]], v1.add_prefix("v1_"), v2.add_prefix("v2_")], axis=1).round(4).to_csv(OUT / "predictions.csv", index=False)
    print("written", OUT / "results.json")


if __name__ == "__main__":
    main()
