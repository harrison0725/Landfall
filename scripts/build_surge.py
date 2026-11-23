"""Build landfall/history/surge/cat{1..5}.npz from the National Hurricane
Center's national Storm Surge Risk Maps data (SLOSH maximum of maximums,
high tide, water above the ground in whole feet; US Gulf and Atlantic):
https://www.nhc.noaa.gov/gis/hazardmaps/US_SLOSH_MOM_Inundation_v4.zip

The source rasters are about 10 m a pixel (1.6 GB zipped). Landfall stores
the 16x overview (about 160 m), cut into 1-degree tiles, so the app can read a
depth for any square without the full download. hazards.py uses the live
10 m map service for which band each building is in, and this file for how
deep the water is within that band.

Run: unzip the file above into DIR, then python scripts/build_surge.py DIR
"""

from __future__ import annotations

import glob
import sys
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parents[1] / "landfall" / "history" / "surge"
FACTOR = 16


def build(src: str, cat: int) -> None:
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds

    path = glob.glob(f"{src}/**/us_Category{cat}_MOM_Inundation_HIGH.tif", recursive=True)[0]
    tiles = {}
    with rasterio.open(path) as d:
        res = d.res[0] * FACTOR
        b = d.bounds
        for lat in range(int(np.floor(b.bottom)), int(np.ceil(b.top))):
            for lon in range(int(np.floor(b.left)), int(np.ceil(b.right))):
                win = from_bounds(lon, lat, lon + 1, lat + 1, d.transform)
                n = int(round(1 / res))
                a = d.read(1, window=win, out_shape=(n, n), boundless=True, fill_value=255, resampling=Resampling.nearest)
                a = np.where(a == 255, 0, a).astype(np.uint8)  # 0: dry; 99: leveed area
                if a.any():
                    tiles[f"t{lon}_{lat}"] = a
    meta = np.array([res], dtype=np.float64)
    np.savez_compressed(OUT / f"cat{cat}.npz", res=meta, **tiles)
    print(cat, len(tiles), "tiles", (OUT / f"cat{cat}.npz").stat().st_size // 1024, "KB")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for c in range(1, 6):
        build(sys.argv[1], c)
