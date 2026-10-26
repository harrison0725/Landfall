"""Download imagery and outlines for an area and line up every building.

    python scripts/prepare_area.py --area cedar_key

Writes data/<area>/: buildings.geojson, pre.png / post.png mosaics,
crops/<id>_{pre,post}.png, alignment.json and a contact sheet to eyeball.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from landfall.align import match_building, place_buildings  # noqa: E402
from landfall.geo import Window, building_window, lonlat_to_pixel, meters_per_pixel  # noqa: E402
from landfall.sources import ms_buildings, naip_mosaic, naip_scenes, noaa_mosaic  # noqa: E402

AREAS = {
    # Hurricane Helene landfall 2024-09-26. Downtown Cedar Key and the Dock
    # Street waterfront, which took the surge.
    "cedar_key": dict(
        center=(-83.0355, 29.1375),
        search_bbox=(-83.06, 29.12, -83.01, 29.16),
        storm_date="2024-09-26",
        noaa_flight="20240928b-rgb",
        n_buildings=150,
        min_area_m2=40.0,
    ),
}
ZOOM = 19
MARGIN_M = 8.0
MAX_SHIFT = 28  # ~7 m: roofs on pilings lean differently in each flight
TEMPLATE_PAD = 8  # px of ground kept around the outline's box for matching


def area_m2(outline: list[tuple[float, float]]) -> float:
    lat0 = outline[0][1]
    kx = 111320 * math.cos(math.radians(lat0))
    ky = 110540
    pts = [(lon * kx, lat * ky) for lon, lat in outline]
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]))) / 2


def pick_buildings(feats: list[dict], center, n: int, min_area: float) -> list[dict]:
    cx, cy = center
    kx = math.cos(math.radians(cy))
    for f in feats:
        ring = [tuple(p) for p in f["geometry"]["coordinates"][0]]
        f["properties"]["area_m2"] = round(area_m2(ring[:-1]), 1)
    big = [f for f in feats if f["properties"]["area_m2"] >= min_area]
    big.sort(key=lambda f: ((f["properties"]["centroid"][0] - cx) * kx) ** 2 + (f["properties"]["centroid"][1] - cy) ** 2)
    chosen = big[:n]
    for i, f in enumerate(chosen):
        f["properties"]["id"] = f"b{i:03d}"
    return chosen


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", default="cedar_key", choices=AREAS)
    ap.add_argument("--out", type=Path, default=Path("data"))
    args = ap.parse_args()
    cfg = AREAS[args.area]
    out = args.out / args.area
    cache = args.out / "cache"
    (out / "crops").mkdir(parents=True, exist_ok=True)

    feats = ms_buildings(cfg["search_bbox"], cache)
    chosen = pick_buildings(feats, cfg["center"], cfg["n_buildings"], cfg["min_area_m2"])
    print(f"{len(feats)} footprints in search box, kept {len(chosen)}")
    (out / "buildings.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": chosen}))

    wins = {f["properties"]["id"]: building_window([tuple(p) for p in f["geometry"]["coordinates"][0][:-1]], ZOOM, MARGIN_M) for f in chosen}
    pad = 64 + MAX_SHIFT
    area = Window(
        min(w.x0 for w in wins.values()) - pad,
        min(w.y0 for w in wins.values()) - pad,
        max(w.x1 for w in wins.values()) + pad,
        max(w.y1 for w in wins.values()) + pad,
    )
    print(f"mosaic {area.width}x{area.height} px at z{ZOOM}")

    post, post_ok = noaa_mosaic(cfg["noaa_flight"], area, ZOOM, cache)
    scene = naip_scenes(*cfg["center"], before=cfg["storm_date"])[0]
    print(f"pre-storm NAIP scene {scene.id} ({scene.date}, {scene.gsd} m)")
    pre, pre_ok = naip_mosaic(scene, area, ZOOM)
    Image.fromarray(pre).save(out / "pre.png")
    Image.fromarray(post).save(out / "post.png")

    matches, centers, coverage = [], [], []
    for f in chosen:
        bid = f["properties"]["id"]
        ring = f["geometry"]["coordinates"][0][:-1]
        px = np.array([lonlat_to_pixel(lon, lat, ZOOM) for lon, lat in ring]) - [area.x0, area.y0]
        bx0, by0 = np.floor(px.min(0)).astype(int) - TEMPLATE_PAD
        bx1, by1 = np.ceil(px.max(0)).astype(int) + TEMPLATE_PAD
        m = MAX_SHIFT
        ctx = (slice(by0 - m, by1 + m), slice(bx0 - m, bx1 + m))
        coverage.append(float(min(pre_ok[ctx].mean(), post_ok[ctx].mean())))
        matches.append(match_building(pre[ctx], post[ctx], (m, m, m + bx1 - bx0, m + by1 - by0), m))
        centers.append(tuple(px.mean(0)))
    places = place_buildings(matches, centers)

    results = []
    for f, pl, cover in zip(chosen, places, coverage):
        bid = f["properties"]["id"]
        w = wins[bid]
        status = pl.status if cover >= 0.99 else "unaligned"
        rec = dict(
            id=bid,
            window=[w.x0, w.y0, w.x1, w.y1],
            area_m2=f["properties"]["area_m2"],
            coverage=round(cover, 3),
            dx=pl.dx,
            dy=pl.dy,
            status=status,
            aligned=status == "matched",
            score=round(pl.own.score, 3),
            margin=round(pl.own.margin, 3),
            own_dx=pl.own.dx,
            own_dy=pl.own.dy,
            neighbor_dx=None if pl.neighbor_dx is None else round(pl.neighbor_dx, 1),
            neighbor_dy=None if pl.neighbor_dy is None else round(pl.neighbor_dy, 1),
        )
        a = pre[w.y0 - area.y0 : w.y1 - area.y0, w.x0 - area.x0 : w.x1 - area.x0]
        b = post[w.y0 - area.y0 + pl.dy : w.y1 - area.y0 + pl.dy, w.x0 - area.x0 + pl.dx : w.x1 - area.x0 + pl.dx]
        Image.fromarray(a).save(out / "crops" / f"{bid}_pre.png")
        Image.fromarray(b).save(out / "crops" / f"{bid}_post.png")
        results.append(rec)

    ok = sum(r["aligned"] for r in results)
    summary = dict(
        area=args.area,
        zoom=ZOOM,
        m_per_px=round(meters_per_pixel(cfg["center"][1], ZOOM), 3),
        noaa_flight=cfg["noaa_flight"],
        naip_scene=scene.id,
        naip_date=scene.date,
        buildings=len(results),
        aligned=ok,
        placed_from_neighbors=sum(r["status"] == "neighbors" for r in results),
        unaligned=sum(r["status"] == "unaligned" for r in results),
    )
    (out / "alignment.json").write_text(json.dumps(dict(summary=summary, buildings=results), indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
