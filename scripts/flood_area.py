"""Flood depth above ground for every building in an area.

    python scripts/flood_area.py --area cedar_key

Writes data/<area>/flood.json with the water surface used (and every
observation behind it) and each building's ground elevation and depth.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from landfall.flood import depth_above_ground, gauge_peak, ground_ft, high_water_marks, water_surface  # noqa: E402

AREAS = {
    "cedar_key": dict(
        stn_event="2024 Helene",
        center=(-83.0355, 29.1375),
        hwm_radius_km=5.0,
        # NOAA CO-OPS 8727520 Cedar Key, on the Dock Street waterfront
        gauges=[dict(station="8727520", lon=-83.0317, lat=29.1350, begin="20240925", end="20240929")],
    ),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", default="cedar_key", choices=AREAS)
    ap.add_argument("--out", type=Path, default=Path("data"))
    args = ap.parse_args()
    cfg = AREAS[args.area]
    d = args.out / args.area

    obs = high_water_marks(cfg["stn_event"], cfg["center"], cfg["hwm_radius_km"])
    obs += [gauge_peak(**g) for g in cfg["gauges"]]
    surface = water_surface(obs)
    for o in obs:
        print(f"{o.source}: {o.elev_ft:.2f} ft NAVD88 {o.note[:60]}")
    print(f"water surface used: {surface:.2f} ft NAVD88")

    feats = json.loads((d / "buildings.geojson").read_text())["features"]
    cache = args.out / "cache" / "epqs.json"
    store = json.loads(cache.read_text()) if cache.exists() else {}

    def one(f):
        lon, lat = f["properties"]["centroid"]
        key = f"{lon:.6f},{lat:.6f}"
        return key, store[key] if key in store else ground_ft(lon, lat)

    with ThreadPoolExecutor(8) as pool:
        by_key = dict(pool.map(one, feats))
    store.update(by_key)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(store))
    grounds = {f["properties"]["id"]: by_key[f"{f['properties']['centroid'][0]:.6f},{f['properties']['centroid'][1]:.6f}"] for f in feats}

    buildings = []
    for f in feats:
        bid = f["properties"]["id"]
        g = grounds[bid]
        dep = depth_above_ground(surface, g)
        buildings.append(dict(id=bid, ground_ft=None if g is None else round(g, 2), flood_ft=None if dep is None else round(dep, 2)))
    (d / "flood.json").write_text(
        json.dumps(dict(surface_ft_navd88=round(surface, 2), observations=[o.__dict__ for o in obs], buildings=buildings), indent=1)
    )
    deps = sorted(b["flood_ft"] for b in buildings if b["flood_ft"] is not None)
    if deps:
        print(f"depth above ground: min {deps[0]:.1f} ft, median {deps[len(deps) // 2]:.1f} ft, max {deps[-1]:.1f} ft")


if __name__ == "__main__":
    main()
