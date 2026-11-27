"""Tornado chances over the next 10, 30 and 50 years for a square.

From NOAA's tornado record (scripts/build_tornadoes.py):

1. How often tornadoes come: every tornado since 1995 whose path came within
   50 miles of the square (1995 on: Doppler radar finds the weak ones, and
   widths are the largest width, as used here).
2. Each simulated tornado is one of those, with its own rating, length, width
   and direction, dropped at a random spot within the 50 miles. Most miss the
   square entirely.
3. Wind in the path: NWS damage surveys' EF contour maps of 876 tornadoes
   (scripts/build_tornado_wind.py) give, for each rating, the share of the
   recorded length x largest width at or above each EF level. An EF3's own
   EF3 wind covers about 3% of that box, and EF1 or more about 41%. A
   building's level is set by how far it is from the center line (stronger
   levels in narrower strips around it); its wind is anywhere within that
   level's Enhanced Fujita range.
4. Damage at each building from the Enhanced Fujita scale's damage indicators
   (hazards.tornado). A building counts once.

The likely-between range is the middle 90% of the simulated periods.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

HISTORY = Path(__file__).resolve().parent / "history"
REACH_MI = 50.0
FROM_YEAR = 1995
MI_PER_DEG = 69.17
# Enhanced Fujita 3-second gust ranges, mph (EF5 capped at 230); F-scale ratings before 2007 taken as the same number
EF_MPH = [(65, 85), (86, 110), (111, 135), (136, 165), (166, 200), (201, 230)]
PERIODS = (10, 30, 50)


@lru_cache(maxsize=1)
def wind_shares() -> dict:
    """Rating -> share of the length x width box at or above EF0, EF1, ... (from NWS damage surveys)."""
    import json

    d = json.loads((HISTORY / "tornado_wind.json").read_text())
    return {int(k): v for k, v in d["shares"].items()}


@lru_cache(maxsize=1)
def record() -> dict:
    import numpy as np

    z = np.load(HISTORY / "tornadoes.npz")
    return {k: z[k] for k in z.files}


def _nearby(lon: float, lat: float, reach: float, from_year: int):
    """Tornadoes since from_year whose path came within reach miles: index, closest miles."""
    import numpy as np

    r = record()
    k = math.cos(math.radians(lat))
    keep = r["year"] >= from_year
    # quick box first
    lo_min, lo_max = np.minimum(r["slon"], r["elon"]), np.maximum(r["slon"], r["elon"])
    la_min, la_max = np.minimum(r["slat"], r["elat"]), np.maximum(r["slat"], r["elat"])
    dl = reach / MI_PER_DEG
    keep &= (lo_max >= lon - dl / k) & (lo_min <= lon + dl / k) & (la_max >= lat - dl) & (la_min <= lat + dl)
    i = np.where(keep)[0]
    ax, ay = (r["slon"][i] - lon) * k * MI_PER_DEG, (r["slat"][i] - lat) * MI_PER_DEG
    bx, by = (r["elon"][i] - lon) * k * MI_PER_DEG, (r["elat"][i] - lat) * MI_PER_DEG
    dx, dy = bx - ax, by - ay
    t = np.clip(-(ax * dx + ay * dy) / np.maximum(dx * dx + dy * dy, 1e-9), 0, 1)
    closest = np.hypot(ax + t * dx, ay + t * dy)
    near = closest <= reach
    return i[near], closest[near]


def _damage_tables():
    """Chance of damage and of bad damage by gust (1-mph steps, 0-240), houses and manufactured homes."""
    import numpy as np

    from . import hazards

    g = np.arange(241, dtype=float)
    out = {}
    for kind, mat in (("house", "wood frame"), ("manufactured", "manufactured home")):
        pairs = [hazards.tornado(max(v, 1.0), {"material": mat}) for v in g]
        out[kind] = (np.array([p[0] for p in pairs]), np.array([p[1] for p in pairs]))
    return out


def outlook(buildings: list[dict], lon: float, lat: float, sims: int = 4000, seed: int = 11) -> dict | None:
    import numpy as np

    if not buildings:
        return None
    r = record()
    last = int(r["year"].max())
    years = last - FROM_YEAR + 1
    idx, _ = _nearby(lon, lat, REACH_MI, FROM_YEAR)
    n = len(idx)
    k = math.cos(math.radians(lat))
    bx = np.array([(b["lon"] - lon) * k * MI_PER_DEG for b in buildings])
    by = np.array([(b["lat"] - lat) * MI_PER_DEG for b in buildings])
    manu = np.array(["manufactured" in (b.get("material") or "") for b in buildings])
    total = len(buildings)
    reach_sq = float(np.hypot(bx, by).max()) + 0.01
    tab = _damage_tables()
    shares = wind_shares()
    pdmg = np.where(manu[None, :], tab["manufactured"][0][:, None], tab["house"][0][:, None])  # [gust, building]
    pbad = np.where(manu[None, :], tab["manufactured"][1][:, None], tab["house"][1][:, None])

    mag = r["mag"][idx].astype(int)
    length = r["length_mi"][idx].astype(float)
    half = r["width_yd"][idx].astype(float) / 1760 / 2
    ang = np.arctan2((r["elat"][idx] - r["slat"][idx]) * MI_PER_DEG, (r["elon"][idx] - r["slon"][idx]) * k * MI_PER_DEG)

    # share of the 50-mile circle's area that tornado paths covered each year (any rating, and EF2 or stronger)
    area = math.pi * REACH_MI ** 2
    box = length * half * 2  # most of these paths lie inside the circle
    sh = wind_shares()
    point_any = float(sum(box[q] * sh[min(int(mag[q]), 5)][0] for q in range(n))) / area / years if n else 0.0
    point_strong = float(sum(box[q] * sh[min(int(mag[q]), 5)][2] for q in range(n) if mag[q] >= 2)) / area / years if n else 0.0

    rng = np.random.default_rng(seed)
    out = {}
    for T in PERIODS:
        lam = rng.gamma(n + 0.5, 1.0 / years, sims)
        kk = rng.poisson(lam * T)
        total_k = int(kk.sum())
        dmg, bad = np.zeros(sims), np.zeros(sims)
        hit_any, hit_strong = np.zeros(sims, bool), np.zeros(sims, bool)
        if n and total_k:
            sim_of = np.repeat(np.arange(sims), kk)
            pick = rng.integers(0, n, total_k)
            rad = REACH_MI * np.sqrt(rng.random(total_k))
            th = 2 * math.pi * rng.random(total_k)
            cx, cy = rad * np.cos(th), rad * np.sin(th)
            L, h, a = length[pick], half[pick], ang[pick]
            close = np.hypot(cx, cy) <= L / 2 + h + reach_sq
            surv_d = {}
            surv_b = {}
            for j in np.where(close)[0]:
                ux, uy = math.cos(a[j]), math.sin(a[j])
                px, py = bx - cx[j], by - cy[j]
                along = px * ux + py * uy
                cross = np.abs(px * uy - py * ux)
                inside = (np.abs(along) <= L[j] / 2) & (cross <= h[j])
                if not inside.any():
                    continue
                s = sim_of[j]
                u = cross / h[j]  # 0 on the center line, 1 at the recorded edge
                lvl = np.full(total, -1)
                for e, share in enumerate(shares[min(mag[pick[j]], 5)]):
                    lvl[u <= share] = e
                inside &= lvl >= 0
                if not inside.any():
                    continue
                lo_v = np.array([EF_MPH[e][0] for e in range(6)])[np.maximum(lvl, 0)]
                hi_v = np.array([EF_MPH[e][1] for e in range(6)])[np.maximum(lvl, 0)]
                v = lo_v + (hi_v - lo_v) * rng.random(total)
                gi = np.clip(np.round(v).astype(int), 0, 240)
                cols = np.arange(total)
                pd_ = np.where(inside, pdmg[gi, cols], 0.0)
                pb_ = np.where(inside, pbad[gi, cols], 0.0)
                surv_d[s] = surv_d.get(s, np.ones(total)) * (1 - pd_)
                surv_b[s] = surv_b.get(s, np.ones(total)) * (1 - pb_)
                hit_any[s] = True
                hit_strong[s] |= bool((inside & (lvl >= 2)).any())  # EF2 or stronger wind at a building
            for s, sv in surv_d.items():
                dmg[s] = (1 - sv).sum()
                bad[s] = (1 - surv_b[s]).sum()
        out[str(T)] = {
            "years": T,
            "chance_path_crosses": round(float(hit_any.mean()), 3),
            "chance_strong_path_crosses": round(float(hit_strong.mean()), 3),
            "expected_damaged": round(float(dmg.mean()), 1),
            "damaged_range_90": [int(np.percentile(dmg, 5)), int(math.ceil(np.percentile(dmg, 95)))],
            "expected_badly_damaged": round(float(bad.mean()), 1),
            "badly_range_90": [int(np.percentile(bad, 5)), int(math.ceil(np.percentile(bad, 95)))],
        }

    # strongest tornadoes within 10 miles since 1950
    near, dist = _nearby(lon, lat, 10.0, int(r["year"].min()))
    order = sorted(range(len(near)), key=lambda q: (-int(r["mag"][near[q]]), -float(r["width_yd"][near[q]])))[:3]
    strongest = [{"year": int(r["year"][near[q]]), "rating": int(r["mag"][near[q]]),
                  "miles": round(float(dist[q]), 1), "length_mi": round(float(r["length_mi"][near[q]]), 1),
                  "width_yd": int(r["width_yd"][near[q]])} for q in order]
    return {
        "periods": out,
        "tornadoes_per_year": round(n / years, 1),
        "tornadoes_counted": n,
        "record": f"{FROM_YEAR}-{last}",
        "reach_miles": REACH_MI,
        "point_chance_per_year": {"any": point_any, "ef2_plus": point_strong},
        "buildings": total,
        "strongest_within_10mi": strongest,
        "source": str(r["source"]) + "; how much of each path gets each EF level from NWS damage survey maps of 876 tornadoes; "
        "damage from the Enhanced Fujita scale's damage indicators",
        "means": "Chances come from how often tornadoes came within 50 miles since 1995, each dropped at random spots nearby. "
        "A tornado's top wind covers only a small part of its path, as NWS damage surveys show.",
    }
