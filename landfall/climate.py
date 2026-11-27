"""Chances over time: how often hurricanes have hit a square, and what that
means for its buildings over the next 10, 30 or 50 years.

Every Atlantic storm from 1950 on (NOAA HURDAT2 best tracks, built by
scripts/build_tracks.py) is replayed past the square. Checks are in
eval/climate_check.md (scripts/climate_check.py).

* Wind here: a modified Rankine vortex around each hourly position (wind
  rises linearly to the radius of maximum wind, then falls as (R/r)^0.5),
  with half the storm's forward speed added on its right side and taken off
  on its left. The radius of maximum wind is the best track's own (2021 on)
  or Willoughby and Rahn (2004)'s fit to aircraft data. The peak 1-minute
  wind becomes a 3-second gust with the 1.22 factor of the hurricane
  scenarios, times 0.81: over land, NIST/ARA's measured-wind maps of six
  storms (Harvey, Irma, Michael, Laura, Ian, Helene; 720 surveyed houses)
  had 0.81 of this vortex's gust, give or take 17%.
* Storm surge: a storm brings surge when it passes with the square on its
  right (onshore winds) within 100 nautical miles, or within its radius of
  maximum wind on either side. Its water level above high tide is a share
  of the National Hurricane Center's worst-case Category 1 water level here,
  growing with the storm's strongest wind nearby, fit to 35 storms at the
  Cedar Key tide gauge (NOAA 8727520, 1950-2025; give or take 1.4 ft). Each
  building gets that water level minus its height above the Category 1 worst
  case. This uses one gauge; elsewhere it leans on the worst-case maps to
  carry the local sea floor's effect.

Each past storm's damage to the square comes from the same damage curves as
the hurricane scenarios (hazards.hurricane). Future years are drawn from the
past: the number of storms in a period is Poisson, with a rate that itself is
uncertain (76 years of record); each storm is a past storm picked at random,
with its wind and water a bit lower or higher than estimated; and the model
spread from the multi-storm test (eval/research) is added. A building counts
once however many times it is damaged.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

HISTORY = Path(__file__).resolve().parent / "history"
NM_PER_DEG = 60.0
MPH_PER_KT = 1.15078
GUST = 1.22  # 3-second gust / 1-minute sustained, as in hazards._gust
REACH_NM = 250  # storms that never come closer than this are skipped
LAND = 0.81  # NIST/ARA measured gust / this vortex's gust over land, six storms (scripts/climate_check.py)
LAND_SD_LOG = 0.17
SURGE_REACH_NM = 100
# water level above high tide = Category 1 worst-case level x (A + B x strongest wind nearby, kt); Cedar Key gauge
SURGE_A, SURGE_B, SURGE_SD_FT = -0.1216, 0.005, 1.39
CAT1_ABOVE_LOWEST_FT = 2.8  # Category 1 worst-case level here = deepest building's Category 1 depth + this (fit to Helene at Cedar Key)
PERIODS = (10, 30, 50)
THRESHOLDS = {"hurricane_gusts": 74 * GUST, "cat3_gusts": 111 * GUST}  # 1-minute 74 / 111 mph as gusts
# A 2050 climate. Knutson et al. (2020, BAMS) give, for 2 C of warming over 1986-2005 (the middle
# of this storm record), a median +5% (1-10%) in hurricanes' peak winds, +13% in the share reaching
# Category 4-5, and fewer storms overall with low confidence. IPCC AR6 puts 2041-2060 about 1.3 C
# (0.9-1.8 C) above 1986-2005 under intermediate emissions (SSP2-4.5), so 0.65 (0.45-0.9) of that:
# winds x1.033 (x1.005 to x1.09). The number of storms is kept as in the record.
WIND_2050 = (1 + 0.01 * 0.45, 1 + 0.05 * 0.65, 1 + 0.10 * 0.9)
SEA_LEVEL_REACH_MI = 200
# Saffir-Simpson categories by 1-minute wind, kt
CATS = [(64, 1), (83, 2), (96, 3), (113, 4), (137, 5)]


@lru_cache(maxsize=1)
def tracks() -> dict:
    return json.loads((HISTORY / "hurricane_tracks.json").read_text())


def category(kt: float) -> int:
    c = 0
    for lo, n in CATS:
        if kt >= lo:
            c = n
    return c


def rmw_nm(kt: float, lat: float) -> float:
    """Willoughby and Rahn (2004): radius of maximum wind from intensity and
    latitude, km = 46.4 exp(-0.0155 Vmax[m/s] + 0.0169 lat)."""
    return 46.4 * math.exp(-0.0155 * kt * 0.5144 + 0.0169 * abs(lat)) / 1.852


def _hourly(s: dict):
    t, la, lo, kt, rm = s["t"], s["lat"], s["lon"], s["kt"], s["rmw"]
    for i in range(len(t) - 1):
        n = max(1, round(t[i + 1] - t[i]))
        # forward motion over this leg (kt), and its heading
        k = math.cos(math.radians((la[i] + la[i + 1]) / 2))
        dx, dy = (lo[i + 1] - lo[i]) * k * NM_PER_DEG, (la[i + 1] - la[i]) * NM_PER_DEG
        vt = math.hypot(dx, dy) / max(t[i + 1] - t[i], 1e-6)
        for j in range(n):
            f = j / n
            r = rm[i] + f * (rm[i + 1] - rm[i]) if rm[i] and rm[i + 1] else 0
            yield (la[i] + f * (la[i + 1] - la[i]), lo[i] + f * (lo[i + 1] - lo[i]), kt[i] + f * (kt[i + 1] - kt[i]), r, dx, dy, vt)
    yield (la[-1], lo[-1], kt[-1], rm[-1], 0.0, 0.0, 0.0)


def pass_by(s: dict, lon: float, lat: float) -> dict | None:
    """One storm going past a point: its peak gust there (mph), how close it
    came (nm), and its strongest wind (kt) while it could push surge here
    (0 = none)."""
    k = math.cos(math.radians(lat))
    if min(math.hypot((x - lon) * k, y - lat) for x, y in zip(s["lon"], s["lat"])) * NM_PER_DEG > REACH_NM + 60:
        return None
    best, closest, surge = 0.0, 1e9, 0
    for cla, clo, kt, rm, dx, dy, vt in _hourly(s):
        ex, ey = (lon - clo) * k * NM_PER_DEG, (lat - cla) * NM_PER_DEG
        r = math.hypot(ex, ey)
        closest = min(closest, r)
        if r > REACH_NM or kt < 34:
            continue
        rmax = rm or rmw_nm(kt, cla)
        # right of the motion (northern hemisphere): the point is to the right when the cross product is negative
        right = (dx * ey - dy * ex) < 0 if (dx or dy) else True
        cos_side = 0.0
        if (dx or dy) and r > 0:
            # unit vector 90 degrees clockwise of the heading, against the direction to the point
            hx, hy = dx / math.hypot(dx, dy), dy / math.hypot(dx, dy)
            cos_side = (hy * ex - hx * ey) / r
        vmax = max(kt - 0.5 * vt, 0.0)
        v = vmax * (r / rmax) if r < rmax else vmax * math.sqrt(rmax / r)
        best = max(best, v + 0.5 * vt * cos_side)
        if r <= rmax or (right and r <= SURGE_REACH_NM):
            surge = max(surge, kt)
    if best * LAND < 34 * 0.6:
        return None
    return {"id": s["id"], "name": s["name"].title(), "year": s["year"], "gust_mph": round(best * MPH_PER_KT * GUST * LAND),
            "closest_nm": round(closest), "surge_kt": round(surge)}


def storms_here(lon: float, lat: float) -> tuple[list[dict], int]:
    """Every past storm that brought at least strong-breeze gusts here
    (1950 on), and the number of years of record."""
    d = tracks()
    out = [p for s in d["storms"] if (p := pass_by(s, lon, lat))]
    out = [p for p in out if p["gust_mph"] >= 40]
    return sorted(out, key=lambda p: -p["gust_mph"]), d["last_year"] - d["first_year"] + 1


@lru_cache(maxsize=1)
def _sea_level() -> dict:
    return json.loads((HISTORY / "sea_level_2050.json").read_text())


def sea_level_2050(lon: float, lat: float) -> dict:
    """Sea level rise by 2050 (ft above 1992; low, med, high) at the nearest
    NOAA tide gauge within 200 miles, else the global average."""
    d = _sea_level()
    k = math.cos(math.radians(lat))
    best = min(d["gauges"], key=lambda g: math.hypot((g["lon"] - lon) * k, g["lat"] - lat))
    miles = math.hypot((best["lon"] - lon) * k, best["lat"] - lat) * 69.17
    if miles <= SEA_LEVEL_REACH_MI:
        return {"low": best["low"], "med": best["med"], "high": best["high"], "where": f"{best['name']} tide gauge", "miles": round(miles)}
    return {**d["global_ft"], "where": "global average", "miles": None}


SST_REACH_MI = 250
HURRICANE_WATER_F = 80.0  # about 26.5 C: water warm enough to feed a hurricane (Gray 1968)


@lru_cache(maxsize=1)
def _sst() -> dict:
    return json.loads((HISTORY / "sst.json").read_text())


def _c_to_f(c: float) -> float:
    return c * 9 / 5 + 32


def water_near(lon: float, lat: float) -> dict | None:
    """Hurricane-season (August-October) sea surface temperature of the water near a
    place: ERSST v5 cells within 250 miles (else the 4 nearest), each year since 1950,
    and CMIP6's 2041-2060 value (low, med, high), in degrees F."""
    d = _sst()
    k = math.cos(math.radians(lat))
    lon360 = lon % 360
    cells = []
    for i, la in enumerate(d["lat"]):
        for j, lo in enumerate(d["lon"]):
            if d["change_2050"]["med"][i][j] is None:
                continue
            miles = math.hypot((lo - lon360) * k, la - lat) * 69.17
            cells.append((miles, i, j, math.cos(math.radians(la))))
    if not cells:
        return None
    cells.sort()
    near = [c for c in cells if c[0] <= SST_REACH_MI] or cells[:4]
    if near[0][0] > 600:  # nowhere near the Atlantic
        return None

    def avg(grid) -> float | None:
        v = [(grid[i][j], w) for _, i, j, w in near if grid[i][j] is not None]
        return sum(x * w for x, w in v) / sum(w for _, w in v) / 100 if v else None

    years = list(range(d["first_year"], d["last_year"] + 1))
    temps = [avg(g) for g in d["measured"]]
    by_year = {y: t for y, t in zip(years, temps) if t is not None}
    b0, b1 = d["base_years"]
    base = sum(by_year[y] for y in range(b0, b1 + 1)) / (b1 - b0 + 1)
    f = lambda c: round(_c_to_f(c), 2)  # noqa: E731
    first10 = [by_year[y] for y in years[:10]]
    last10 = [by_year[y] for y in years[-10:]]
    import numpy as np

    slope = float(np.polyfit(list(by_year), list(by_year.values()), 1)[0])
    warmest = max(by_year, key=by_year.get)
    return {
        "years": list(by_year),
        "temp_f": [f(t) for t in by_year.values()],
        "first10_f": f(sum(first10) / 10), "last10_f": f(sum(last10) / 10),
        "first10": f"{years[0]}-{years[9]}", "last10": f"{years[-10]}-{years[-1]}",
        "trend_f_per_decade": round(slope * 9 / 5 * 10, 2),
        "warmest_year": warmest, "warmest_f": f(by_year[warmest]),
        "f_2050": {q: f(base + avg(d["change_2050"][q])) for q in ("low", "med", "high")},
        "future_years": d["future_years"], "models": len(d["models"]),
        "hurricane_water_f": HURRICANE_WATER_F,
        "cells": len(near), "miles": SST_REACH_MI if near[0][0] <= SST_REACH_MI else round(near[-1][0]),
        "source": d["source"],
    }


def _era(y: int | None) -> int:
    y = y or 0
    return 2 if y >= 2002 else 1 if y >= 1976 else 0


def surge_level(kt: float, cat1_level: float) -> float:
    """A storm's water level above high tide here (ft), from its strongest
    wind while it could push surge here and the Category 1 worst-case level."""
    if kt < 34 or cat1_level <= 0:
        return 0.0
    return max(0.0, cat1_level * (SURGE_A + SURGE_B * kt))


def cat1_level(buildings: list[dict]) -> float:
    """The Category 1 worst-case water level above high tide in the square:
    the deepest buildings' Category 1 depth plus a little (the lowest
    buildings stand a few feet above high tide)."""
    import numpy as np

    d = [(b.get("surge") or (0,))[0] for b in buildings]
    d = [x for x in d if x]
    return float(np.percentile(d, 95)) + CAT1_ABOVE_LOWEST_FT if d else 0.0


def outlook(buildings: list[dict], classes: list[dict], index: list[int], state: str | None, place: dict,
            lon: float, lat: float, sd_log: float, sims: int = 4000, seed: int = 7) -> dict:
    """Chances over the next 10, 30 and 50 years for a square, from past storms."""
    import numpy as np

    from . import hazards

    storms, years = storms_here(lon, lat)
    level1 = cat1_level(buildings) if place.get("surge_checked", True) else 0.0
    members: dict[int, dict] = {}
    for b, i in zip(buildings, index):
        members.setdefault(i, b)
    counts = np.bincount(np.asarray(index, int), minlength=len(classes)).astype(float) if index else np.zeros(0)
    # chance each kind of building is damaged (and badly) by each past storm, with its wind and water
    # estimated low, as is, and high (a 3-point stand-in for their uncertainty)
    z = 1.2
    shifts = [(math.exp(-z * LAND_SD_LOG), -z * SURGE_SD_FT), (1.0, 0.0), (math.exp(z * LAND_SD_LOG), z * SURGE_SD_FT)]
    n = len(storms)
    cache: dict[tuple, tuple] = {}
    # what hazards.hurricane looks at, so look-alike buildings share one calculation
    kinds = {ci: (hazards.wind_key(b, state), _era(b.get("year_built")), b.get("foundation"), (b.get("stories") or 1) >= 2,
                  (b.get("flood_zone") or "")[:1].upper()) for ci, b in members.items()}
    for st in storms:
        st["surge_ft"] = round(surge_level(st["surge_kt"], level1), 1)

    def odds_for(wind_mult: float, rise_ft: float):
        """Chance each kind of building is damaged (and badly) by each past
        storm, with its wind and water estimated low, as is, and high."""
        pd_ = np.zeros((3, n, len(classes)))
        ps_ = np.zeros((3, n, len(classes)))
        wet = np.zeros(n)
        for si, st in enumerate(storms):
            h0 = st["surge_ft"]
            for k, (gf, dh) in enumerate(shifts):
                g = round(st["gust_mph"] * gf * wind_mult / 2) * 2
                h = max(0.0, h0 + dh) + rise_ft if h0 > 0 else 0.0
                for ci, b in members.items():
                    d1 = (b.get("surge") or (0,))[0]
                    depth = round(max(0.0, d1 - (level1 - h)), 1) if d1 and h > 0 else 0.0
                    key = (kinds[ci], g, depth)
                    if key not in cache:
                        d, sv, _l, _w = hazards.hurricane(g, depth, b, state)
                        cache[key] = (d, sv)
                    pd_[k, si, ci], ps_[k, si, ci] = cache[key]
                    if k == 1 and depth > 0:
                        wet[si] += counts[ci]
        return pd_, ps_, wet

    total = float(counts.sum())
    gusts = np.array([s["gust_mph"] for s in storms]) if storms else np.zeros(0)

    def periods(levels: list[tuple], wind_mult: float) -> dict:
        """levels: one (pd, ps, wet) per climate level, each equally likely."""
        rng = np.random.default_rng(seed)
        out = {}
        wet = levels[len(levels) // 2][2]
        for T in PERIODS:
            lam = rng.gamma(n + 0.5, 1.0 / years, sims)  # storms a year here: Jeffreys posterior from the record
            k = rng.poisson(lam * T)
            lev = rng.integers(0, len(levels), sims)
            dmg = np.zeros(sims)
            bad = np.zeros(sims)
            for j in range(sims):
                if k[j] == 0 or n == 0:
                    continue
                pd_, ps_, _w = levels[lev[j]]
                pick = rng.integers(0, n, k[j])
                lvl = rng.integers(0, 3, k[j])
                dmg[j] = (counts * (1 - np.prod(1 - pd_[lvl, pick], axis=0))).sum()
                bad[j] = (counts * (1 - np.prod(1 - ps_[lvl, pick], axis=0))).sum()
            spread = np.exp(rng.normal(0, sd_log, sims))
            dmg, bad = np.minimum(dmg * spread, total), np.minimum(bad * spread, total)
            chance = {key: round(1 - math.exp(-float((gusts * wind_mult >= g).sum()) / years * T), 3) for key, g in THRESHOLDS.items()}
            chance["surge"] = round(1 - math.exp(-float((wet >= max(1.0, 0.1 * total)).sum()) / years * T), 3)  # water at a tenth of the buildings
            out[str(T)] = {
                "years": T,
                "chance": chance,
                "expected_damaged": round(float(dmg.mean())),
                "damaged_range_90": [int(np.percentile(dmg, 5)), int(math.ceil(np.percentile(dmg, 95)))],
                "expected_badly_damaged": round(float(bad.mean())),
                "badly_range_90": [int(np.percentile(bad, 5)), int(math.ceil(np.percentile(bad, 95)))],
            }
        return out

    now = odds_for(1.0, 0.0)
    per = periods([now], 1.0)
    rise = sea_level_2050(lon, lat)
    future = [odds_for(w, r) for w, r in zip(WIND_2050, (rise["low"], rise["med"], rise["high"]))]
    per_2050 = periods(future, WIND_2050[1])
    exp_d = (now[0][1] * counts).sum(1) if n else np.zeros(0)
    wet = now[2]
    top = [{**s, "expected_damaged": round(float(exp_d[i])), "buildings_wet": int(wet[i])} for i, s in enumerate(storms[:5])]
    t = tracks()
    try:
        water = water_near(lon, lat)
    except (OSError, ValueError, KeyError, ZeroDivisionError):
        water = None
    return {
        "periods": per,
        "water": water,
        "periods_2050": per_2050,
        "climate_2050": {"sea_level_rise_ft": rise, "wind_change_pct": [round(100 * (w - 1), 1) for w in WIND_2050],
                         "means": "2050's higher seas (NOAA 2022 Intermediate scenario, the nearest tide gauge) and stronger hurricanes "
                                  "(Knutson et al. 2020, scaled to IPCC AR6 warming by 2041-2060); the number of storms stays as in the record"},
        "buildings": int(total),
        "storms_counted": n,
        "years_of_record": years,
        "record": f"{t['first_year']}-{t['last_year']}",
        "strongest_here": top,
        "source": t["source"] + "; wind at the square from a modified Rankine vortex (Willoughby and Rahn 2004 radii), scaled to "
        "NIST/ARA measured winds of six storms; storm surge as a share of the National Hurricane Center's Category 1 worst case, fit "
        "to the Cedar Key tide gauge (NOAA 8727520); damage from the same curves as the hurricane scenarios; 2050: "
        + _sea_level()["source"] + ", and Knutson et al. (2020) hurricane intensity changes scaled to IPCC AR6 warming",
        "means": "Chances come from how often past storms (1950 on) reached here. The 2050 numbers add higher seas and stronger "
        "storms; both keep the number of storms as in the record.",
    }
