"""US tornado paths for "Over the years" (landfall/tornadoes.py).

Reads NOAA Storm Prediction Center's tornado database (SPC WCM, one row per
tornado, https://www.spc.noaa.gov/wcm/) and writes
landfall/history/tornadoes.npz with, for every tornado since 1950: year,
rating (F scale to January 2007, EF scale after; unknown counted as 0), start
and end point, path length (miles) and width (yards; the mean width to 1994,
the largest width from 1995).

Run: python scripts/build_tornadoes.py [path to a downloaded 1950-YYYY_actual_tornadoes.csv]
"""

from __future__ import annotations

import io
import re
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INDEX = "https://www.spc.noaa.gov/wcm/"


def download() -> tuple[str, str]:
    html = urllib.request.urlopen(INDEX, timeout=60).read().decode()
    name = sorted(set(re.findall(r"data/1950-\d{4}_actual_tornadoes\.csv", html)))[-1]
    return name.split("/")[-1], urllib.request.urlopen(INDEX + name, timeout=300).read().decode()


def main() -> None:
    if len(sys.argv) > 1:
        name, text = Path(sys.argv[1]).name, Path(sys.argv[1]).read_text()
    else:
        name, text = download()
    d = pd.read_csv(io.StringIO(text), low_memory=False)
    d = d[d.slat > 0].copy()
    no_end = (d.elat <= 0) | (d.elon >= 0)
    d.loc[no_end, "elat"] = d.loc[no_end, "slat"]
    d.loc[no_end, "elon"] = d.loc[no_end, "slon"]
    out = ROOT / "landfall" / "history" / "tornadoes.npz"
    np.savez_compressed(
        out,
        year=d.yr.to_numpy(np.int16), mag=d.mag.clip(lower=0).to_numpy(np.int8),
        slat=d.slat.to_numpy(np.float32), slon=d.slon.to_numpy(np.float32),
        elat=d.elat.to_numpy(np.float32), elon=d.elon.to_numpy(np.float32),
        length_mi=d.len.clip(lower=0.05).to_numpy(np.float32), width_yd=d.wid.clip(lower=10).to_numpy(np.float32),
        source=np.array(f"NOAA Storm Prediction Center tornado database ({name})"),
    )
    print(len(d), "tornadoes", int(d.yr.min()), "-", int(d.yr.max()), round(out.stat().st_size / 1e6, 2), "MB")


if __name__ == "__main__":
    main()
