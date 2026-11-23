"""Two checks of the square estimate's assumptions, written to
eval/assumption_checks.md:

1. Forward speed. The inland wind decay (Kaplan and DeMaria 1995) needs how
   fast the storm moves over land; Landfall assumes 12 mph. This re-runs the
   Hazus wind odds for slow (8 mph, like Ian 2022), typical (12) and fast
   storms (18; 24, like Helene 2024) at several distances inland.

2. Helene at Cedar Key. What Landfall would have said for Helene's real
   hazard (84 mph peak gust at NOAA station CKYF1; water surface 10.9 ft
   NAVD88 from USGS high-water marks and NOAA gauge 8727520) for each
   surveyed building, from only what Landfall knows about it (material,
   foundation, stories, year built, flood zone; not its surveyed first
   floor), against what the DesignSafe PRJ-6245 survey found. The water
   damage rates come from the same survey, so they are refit with the
   building's own half left out (2-fold cross-validation).
   Also: how Helene's measured water compares with the National Hurricane
   Center's worst-case maps for each category (the maps Landfall uses).

Run: python scripts/check_assumptions.py
"""

from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from landfall import hazards  # noqa: E402

HELENE_GUST = 84
MATERIAL = {"wood": "wood frame", "masonry": "masonry", "concrete": "concrete", "steel": "steel", "manufactured": "manufactured (mobile) home"}


def num(x):
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def speed_check() -> tuple[str, dict]:
    old = {"year_built": 1960, "material": "wood frame", "stories": 1, "v_ult": 130}
    new = old | {"year_built": 2010}
    speeds = [8, 12, 18, 24]
    miles = [0, 10, 25, 50, 100]
    out, lines = {}, []
    lines.append("| Category | Miles inland | " + " | ".join(f"{s} mph" for s in speeds) + " |")
    lines.append("|---|---|" + "---|" * len(speeds))
    base = hazards.FORWARD_MPH
    try:
        for cid, label, g0 in hazards.SCENARIOS["hurricane"]["categories"]:
            for mi in miles:
                cells = []
                for sp in speeds:
                    hazards.FORWARD_MPH = sp
                    g = hazards.inland_gust(g0, mi)
                    d_old = hazards.wind(g, old, "FL")[0]
                    d_new = hazards.wind(g, new, "FL")[0]
                    out[f"{cid}|{mi}|{sp}"] = {"gust": g, "old": round(d_old, 3), "new": round(d_new, 3)}
                    cells.append(f"{g} mph: {d_old:.0%} / {d_new:.0%}")
                lines.append(f"| {label} | {mi} | " + " | ".join(cells) + " |")
    finally:
        hazards.FORWARD_MPH = base
    # largest swing between 8 and 24 mph
    swing = max((abs(out[f"{c}|{m}|24"]["old"] - out[f"{c}|{m}|8"]["old"]), c, m)
                for c, _l, _g in hazards.SCENARIOS["hurricane"]["categories"] for m in miles)
    return "\n".join(lines), {"table": out, "largest_swing_old_wood": swing}


def helene_check() -> tuple[str, dict]:
    import build_hazards as bh

    rows = [r for r in csv.DictReader((ROOT / "eval" / "field" / "cedar_key_buildings.csv").open())]
    keep = []
    for r in rows:
        ds = [num(r[k]) for k in ("DS_roof", "DS_walls/floors", "DS_foundation")]
        if num(r["grd_el_ft"]) is None or all(v is None for v in ds):
            continue
        keep.append(r)
    folds = [[r for r in keep if int(r["FID"]) % 2 == k] for k in (0, 1)]
    t = hazards._table("flood_floors.json")
    saved = t["water_damage"]
    preds = []
    try:
        for k in (0, 1):
            t["water_damage"] = bh.water_damage(folds[1 - k])  # rates from the other half only
            for r in folds[k]:
                b = {"material": MATERIAL.get(r["material"]), "foundation": r["foundation"] or None,
                     "stories": int(num(r["num_stories"]) or 1), "year_built": int(r["year_built"]) if r["year_built"] else None,
                     "flood_zone": None if r["firmzone"] == "NaN" else r["firmzone"], "v_ult": 131, "green": None}
                depth = max(0.0, bh.HELENE_SURFACE_FT - num(r["grd_el_ft"]))
                d, s, loss, wet = hazards.hurricane(HELENE_GUST, depth, b, "FL")
                ds = [v for v in (num(r[x]) for x in ("DS_roof", "DS_walls/floors", "DS_foundation")) if v is not None]
                ffe = num(r["FFE_NAVD88_ft"])
                preds.append({"zone": "V" if (b["flood_zone"] or "").startswith("V") else "A or other", "foundation": b["foundation"] or "unknown",
                              "era": r["code_era"] or "year built unknown (dropped from the tax roll)", "p_d": d, "p_s": s, "p_wet": wet, "obs_d": max(ds) >= 2, "obs_s": max(ds) >= 4,
                              "obs_wet": None if ffe is None else bh.HELENE_SURFACE_FT > ffe})
    finally:
        t["water_damage"] = saved

    def summary(ps):
        n = len(ps)
        base = sum(p["obs_d"] for p in ps) / n
        brier = sum((p["p_d"] - p["obs_d"]) ** 2 for p in ps) / n
        brier0 = sum((base - p["obs_d"]) ** 2 for p in ps) / n
        wet = [p for p in ps if p["obs_wet"] is not None]
        return {"buildings": n, "predicted_damaged": round(sum(p["p_d"] for p in ps)), "observed_damaged": sum(p["obs_d"] for p in ps),
                "predicted_severe": round(sum(p["p_s"] for p in ps)), "observed_severe": sum(p["obs_s"] for p in ps),
                "predicted_water_inside": round(sum(p["p_wet"] for p in wet)), "observed_water_inside": sum(p["obs_wet"] for p in wet),
                "brier": round(brier, 3), "brier_same_rate_for_all": round(brier0, 3)}

    res = {"all": summary(preds)}
    for key in ("zone", "foundation", "era"):
        for v in sorted({p[key] for p in preds}):
            ps = [p for p in preds if p[key] == v]
            if len(ps) >= 20:
                res[f"{key}: {v}"] = summary(ps)
    lines = ["| Buildings | Count | Damaged: predicted / found | Badly damaged: predicted / found | Water inside: predicted / found |", "|---|---|---|---|---|"]
    for k, v in res.items():
        lines.append(f"| {k} | {v['buildings']} | {v['predicted_damaged']} / {v['observed_damaged']} | {v['predicted_severe']} / {v['observed_severe']} | "
                     f"{v['predicted_water_inside']} / {v['observed_water_inside']} |")
    a = res["all"]
    lines.append("")
    lines.append(f"Brier score (lower is better; 0 is perfect): {a['brier']}, against {a['brier_same_rate_for_all']} for giving every building "
                 "the same average chance. Lower than that means the building facts help tell buildings apart.")

    # Helene's measured water against the NHC worst-case maps
    cmp = {}
    for cid, label, _g in hazards.SCENARIOS["hurricane"]["categories"]:
        diffs = []
        for r in keep:
            real = max(0.0, bh.HELENE_SURFACE_FT - num(r["grd_el_ft"]))
            mom = hazards.surge_depth(cid, float(r["x"]), float(r["y"]), None)
            diffs.append(mom - real)
        diffs.sort()
        cmp[label] = {"median_ft_worst_case_minus_helene": round(diffs[len(diffs) // 2], 1)}
    lines.append("")
    lines.append("Helene's water at the surveyed buildings against the National Hurricane Center's worst case for each category "
                 "(median, feet; positive means the map is deeper than Helene was):")
    lines.append("")
    lines.append("| Category | Worst-case map minus Helene |")
    lines.append("|---|---|")
    for k, v in cmp.items():
        lines.append(f"| {k} | {v['median_ft_worst_case_minus_helene']:+.1f} ft |")
    return "\n".join(lines), {"groups": res, "vs_nhc_maps": cmp}


if __name__ == "__main__":
    s_md, s = speed_check()
    h_md, h = helene_check()
    md = ("# Checks of the square estimate's assumptions\n\nMade by `scripts/check_assumptions.py`.\n\n"
          "## 1. Forward speed over land (inland wind decay)\n\nGust at that distance and chance of Hazus damage state 2+ for a one-story wood house, "
          "built 1960 / built 2010 (ASCE 7 design wind 130 mph). Landfall assumes 12 mph.\n\n" + s_md +
          f"\n\nLargest change for the 1960 house between 8 and 24 mph: {s['largest_swing_old_wood'][0]:.0%} "
          f"({s['largest_swing_old_wood'][1]}, {s['largest_swing_old_wood'][2]} miles inland).\n\n"
          "## 2. Helene at Cedar Key: predicted vs found\n\n" + h_md + "\n")
    (ROOT / "eval" / "assumption_checks.md").write_text(md)
    (ROOT / "eval" / "assumption_checks.json").write_text(json.dumps({"speed": s, "helene": h}, indent=1, default=str))
    print(md)
