"""Hurricane-season sea surface temperature for "Over the years" (landfall/climate.py).

Writes landfall/history/sst.json on NOAA ERSST's 2-degree grid over the Atlantic
(0-46N, 100W-2W):

1. Measured: NOAA Extended Reconstructed Sea Surface Temperature v5 (ERSST v5,
   Huang et al. 2017), August to October average for each year from 1950, read
   from NOAA CoastWatch ERDDAP (dataset nceiErsstv5).
2. 2050: CMIP6 climate models (the ones IPCC AR6 uses), SSP2-4.5 (intermediate
   emissions), one run per model. For each model, its August-October average in
   2041-2060 minus its own 1995-2014 average (IPCC AR6's reference period),
   moved to the ERSST grid. The 2050 value at a cell is ERSST's measured
   1995-2014 average plus the models' change (median, and the 17th and 83rd
   percentiles across models, as IPCC reports "likely" ranges). Model output is
   read from the Pangeo CMIP6 copy on Google Cloud (pangeo-cmip6.csv).

Run: python scripts/build_sst.py            (needs xarray, zarr, fsspec, aiohttp, cftime, scipy)
     python scripts/build_sst.py --model X  (one model's change, saved under eval/sst_models/)
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "landfall" / "history" / "sst.json"
MODELS = ROOT / "eval" / "sst_models"
CATALOG = os.environ.get("CMIP6_CATALOG", "https://storage.googleapis.com/cmip6/pangeo-cmip6.csv")
ERDDAP = ("https://coastwatch.pfeg.noaa.gov/erddap/griddap/nceiErsstv5.csv?"
          "sst%5B(1950-{m}-01):12:(2025-{m}-01)%5D%5B(0.0)%5D%5B(0):(46)%5D%5B(260):(358)%5D")
LAT = np.arange(0, 47, 2.0)
LON = np.arange(260, 359, 2.0)
FIRST, LAST = 1950, 2025
BASE = (1995, 2014)
FUTURE = (2041, 2060)
SEASON = (8, 9, 10)


def measured() -> np.ndarray:
    """ERSST v5 August-October average, [year, lat, lon] in degrees C (NaN on land)."""
    frames = []
    for m in ("08", "09", "10"):
        text = urllib.request.urlopen(ERDDAP.format(m=m), timeout=600).read().decode()
        frames.append(pd.read_csv(io.StringIO(text), skiprows=[1]))
    d = pd.concat(frames)
    d["year"] = d.time.str[:4].astype(int)
    a = d.groupby(["year", "latitude", "longitude"]).sst.mean()
    years = np.arange(FIRST, LAST + 1)
    idx = pd.MultiIndex.from_product([years, LAT, LON], names=["year", "latitude", "longitude"])
    return a.reindex(idx).to_numpy().reshape(len(years), len(LAT), len(LON))


def _xyz(lat, lon):
    la, lo = np.radians(lat), np.radians(lon)
    return np.c_[np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)]


def model_change(src: str, catalog: pd.DataFrame) -> dict:
    """One CMIP6 model's August-October change, 2041-2060 (SSP2-4.5) minus 1995-2014, on the ERSST grid."""
    import fsspec
    import xarray as xr
    from scipy.spatial import cKDTree

    glon, glat = np.meshgrid(LON, LAT)
    grid = _xyz(glat.ravel(), glon.ravel())

    def clim(exp, y0, y1, member=None):
        m = catalog[(catalog.variable_id == "tos") & (catalog.table_id == "Omon") & (catalog.experiment_id == exp) & (catalog.source_id == src)]
        m = m.assign(g=m.grid_label.map({"gr": 0, "gr1": 1, "gn": 2}), mine=(m.member_id != member)).sort_values(["mine", "member_id", "g"])
        for _, r in m.iterrows():  # the same run as the other period where it has the years (some stores stop early)
            ds = xr.open_zarr(fsspec.get_mapper(r.zstore.replace("gs://", "https://storage.googleapis.com/")), consolidated=True)
            t = ds.time.dt
            sel = ((t.year >= y0) & (t.year <= y1) & t.month.isin(list(SEASON))).values
            if sel.sum() == (y1 - y0 + 1) * len(SEASON):
                break
        else:
            raise ValueError(f"no {exp} run covers {y0}-{y1}")
        v = ds.tos.isel(time=np.where(sel)[0]).mean("time").load().values
        la = next(ds[k] for k in ("lat", "latitude", "nav_lat") if k in ds).values
        lo = next(ds[k] for k in ("lon", "longitude", "nav_lon") if k in ds).values
        if la.ndim == 1 and v.ndim == 2:  # regular grid; an unstructured mesh keeps one value per point
            lo, la = np.meshgrid(lo, la)
        lo = lo % 360
        ok = np.isfinite(v) & (la > -10) & (la < 56) & ((lo > 250) | (lo < 10))
        d, i = cKDTree(_xyz(la[ok], lo[ok])).query(grid)
        out = v[ok][i]
        out[d > np.radians(1.6)] = np.nan  # no model ocean within about 1.6 degrees
        if np.nanmean(out) > 100:
            out = out - 273.15
        return out.reshape(glat.shape), r.member_id

    f, member = clim("ssp245", *FUTURE)
    h, member_h = clim("historical", *BASE, member)
    if member_h != member:
        raise ValueError(f"{src}: no historical run {member}")
    return {"model": src, "member": member, "delta": np.round(f - h, 3).tolist()}


def models_with_both(catalog: pd.DataFrame) -> list[str]:
    m = catalog[(catalog.variable_id == "tos") & (catalog.table_id == "Omon") & catalog.experiment_id.isin(["historical", "ssp245"])]
    g = m.groupby(["source_id", "experiment_id"]).member_id.nunique().unstack().dropna()
    return sorted(g.index)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    a = ap.parse_args()
    MODELS.mkdir(parents=True, exist_ok=True)
    if a.model:
        catalog = pd.read_csv(CATALOG)
        (MODELS / f"{a.model}.json").write_text(json.dumps(model_change(a.model, catalog)))
        return
    catalog = pd.read_csv(CATALOG)
    for src in models_with_both(catalog):
        if not (MODELS / f"{src}.json").exists():
            subprocess.run([sys.executable, __file__, "--model", src], timeout=1800, check=False)
    deltas = {p.stem: np.array(json.loads(p.read_text())["delta"], float) for p in sorted(MODELS.glob("*.json"))}
    stack = np.stack(list(deltas.values()))
    obs = measured()
    years = np.arange(FIRST, LAST + 1)
    base = np.nanmean(obs[(years >= BASE[0]) & (years <= BASE[1])], axis=0)
    with np.errstate(all="ignore"):
        lo, med, hi = (np.nanpercentile(stack, q, axis=0) for q in (17, 50, 83))
    ocean = np.isfinite(base)

    def pack(x):  # hundredths of a degree C, null on land
        return [[None if not ocean[i, j] or not np.isfinite(x[i, j]) else int(round(100 * x[i, j])) for j in range(len(LON))] for i in range(len(LAT))]

    out = {
        "source": "NOAA ERSST v5 (Huang et al. 2017), August-October average; 2050: CMIP6 models, SSP2-4.5, "
                  f"2041-2060 minus 1995-2014, {len(deltas)} models (median, 17th-83rd percentile), added to ERSST's 1995-2014 average",
        "units": "hundredths of a degree C",
        "lat": LAT.tolist(), "lon": LON.tolist(), "first_year": FIRST, "last_year": LAST,
        "base_years": list(BASE), "future_years": list(FUTURE),
        "models": sorted(deltas),
        "measured": [pack(obs[k]) for k in range(len(years))],
        "change_2050": {"low": pack(lo), "med": pack(med), "high": pack(hi)},
    }
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    print(len(deltas), "models;", round(OUT.stat().st_size / 1e6, 2), "MB; mean change",
          round(float(np.nanmean(med[ocean])), 2), "C")


if __name__ == "__main__":
    main()
