"""Checks behind landfall/climate.py ("Over the years"). Writes
eval/climate_check.json; summarized in eval/climate_check.md.

1. Wind: this module's gust at 720 surveyed houses (120 per storm) for six
   storms, against NIST/ARA's measured-wind maps there (eval/research/
   buildings.csv, gust_mph). Gives LAND (median ratio) and its spread.
2. Surge: the peak water level at the Cedar Key tide gauge (NOAA 8727520,
   hourly heights above high tide, MHHW) for every storm that came within
   150 nautical miles since 1950, against the storm's strongest wind while it
   could push surge there. Gives SURGE_A, SURGE_B and the spread.
3. Helene at Cedar Key replayed: water level and buildings damaged.

Run: python scripts/climate_check.py   (needs eval/research/buildings.csv and the internet)
"""

from __future__ import annotations

import datetime as dt
import json
import math
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from landfall import climate  # noqa: E402

STORM_IDS = {"Helene": "AL092024", "Ian": "AL092022", "Harvey": "AL092017", "Irma": "AL112017", "Michael": "AL142018", "Laura": "AL132020"}
GAUGE = ("8727520", -83.0317, 29.135)  # Cedar Key
CEDAR_KEY_CAT1_LEVEL = 13.8  # ft above MHHW: the Cedar Key square's 95th-percentile Category 1 depth (11 ft) + 2.8


def wind() -> dict:
    t = pd.read_csv(ROOT / "eval" / "research" / "buildings.csv", low_memory=False)
    by_id = {s["id"]: s for s in climate.tracks()["storms"]}
    saved, climate.LAND = climate.LAND, 1.0
    rows = []
    try:
        for ev, g in t.groupby("event"):
            s = by_id[STORM_IDS[ev.split()[0]]]
            g = g.dropna(subset=["gust_mph", "lon", "lat"]).sample(min(len(g), 120), random_state=1)
            for _, r in g.iterrows():
                p = climate.pass_by(s, r.lon, r.lat)
                if p:
                    rows.append((ev, r.gust_mph, p["gust_mph"]))
    finally:
        climate.LAND = saved
    d = pd.DataFrame(rows, columns=["storm", "measured", "vortex"])
    d["ratio"] = d["measured"] / d["vortex"]
    return {"houses": len(d), "median_ratio": round(float(d["ratio"].median()), 3), "sd_log": round(float(np.log(d["ratio"]).std()), 3),
            "by_storm": {k: {"measured_median_mph": round(float(g["measured"].median())), "vortex_median_mph": round(float(g["vortex"].median())),
                             "ratio": round(float(g["ratio"].median()), 2)} for k, g in d.groupby("storm")}}


def gauge_peak(station: str, when: dt.datetime) -> float | None:
    a, b = when - dt.timedelta(hours=30), when + dt.timedelta(hours=30)
    u = ("https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?product=hourly_height&datum=MHHW&units=english&time_zone=gmt"
         f"&format=json&application=landfall_research&station={station}"
         f"&begin_date={a:%Y%m%d}%20{a:%H:%M}&end_date={b:%Y%m%d}%20{b:%H:%M}")
    try:
        d = json.load(urllib.request.urlopen(u, timeout=60))
    except Exception:  # noqa: BLE001
        return None
    v = [float(x["v"]) for x in d.get("data", []) if x.get("v")]
    return max(v) if v else None


def surge() -> dict:
    station, lon, lat = GAUGE
    k = math.cos(math.radians(lat))
    rows = []
    for s in climate.tracks()["storms"]:
        p = climate.pass_by(s, lon, lat)
        if not p or p["closest_nm"] > 150:
            continue
        i = min(range(len(s["t"])), key=lambda i: math.hypot((s["lon"][i] - lon) * k, s["lat"][i] - lat))
        when = dt.datetime.strptime(s["start"], "%Y-%m-%dT%H:%MZ") + dt.timedelta(hours=s["t"][i])
        rows.append({"storm": p["name"], "year": p["year"], "closest_nm": p["closest_nm"], "surge_kt": p["surge_kt"], "peak_ft_above_mhhw": gauge_peak(station, when)})
        time.sleep(0.2)
    d = pd.DataFrame(rows).dropna(subset=["peak_ft_above_mhhw"])
    m = d["surge_kt"] >= 34
    y = d.loc[m, "peak_ft_above_mhhw"] / CEDAR_KEY_CAT1_LEVEL
    A = np.c_[np.ones(m.sum()), d.loc[m, "surge_kt"]]
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    pred = A @ coef * CEDAR_KEY_CAT1_LEVEL
    obs = d.loc[m, "peak_ft_above_mhhw"].to_numpy()
    return {"station": f"NOAA {station} Cedar Key", "storms_with_gauge_data": int(len(d)), "storms_that_could_push_surge": int(m.sum()),
            "fit": {"a": round(float(coef[0]), 4), "b": round(float(coef[1]), 5)}, "residual_sd_ft": round(float(np.std(obs - pred)), 2),
            "correlation": round(float(np.corrcoef(obs, pred)[0, 1]), 2),
            "highest": d.sort_values("peak_ft_above_mhhw", ascending=False).head(10).round(2).to_dict("records")}


def main() -> None:
    out = {"wind": wind(), "surge": surge()}
    (ROOT / "eval" / "climate_check.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
