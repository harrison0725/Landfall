"""Cut Maxar after-storm imagery into map tiles, so a satellite area looks
like a NOAA one: photos across the whole map, not one square on a street map.

The browser can't read Maxar's GeoTIFFs, and the free host is too slow to
warp them on request, so the tiles are made once here and shipped as files:

    python scripts/maxar_tiles.py montego_bay_melissa melissa \
        -77.935 18.452 -77.895 18.478 --areas montego_bay montego_bay_hills

writes demo/tiles/<name>/{z}/{x}/{y}.jpg (zooms 12 to 18, about Maxar's own
0.5 m) and points each area at it with demo/<area>/tiles.json.

A wider, coarser layer fills the map around it with every after-storm pass
(clearest first), read from the COGs' overviews so it stays cheap:

    python scripts/maxar_tiles.py montego_bay_wide melissa \
        -78.1 18.35 -77.7 18.55 --top 15 --low 10 --overview 2 --all --under \
        --areas montego_bay montego_bay_hills
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

from landfall import maxar
from landfall.geo import TILE_SIZE, Window, lonlat_to_pixel
from landfall.storms import STORMS



def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("storm")
    ap.add_argument("bbox", nargs=4, type=float, metavar=("W", "S", "E", "N"))
    ap.add_argument("--areas", nargs="*", default=[])
    ap.add_argument("--out", default="demo")
    ap.add_argument("--cache", default="data/cache")
    ap.add_argument("--top", type=int, default=18)
    ap.add_argument("--low", type=int, default=12)
    ap.add_argument("--overview", type=int, default=None, help="COG overview level to read (0 = half resolution)")
    ap.add_argument("--all", action="store_true", help="every after-storm pass, clearest first, even cloudy ones")
    ap.add_argument("--under", action="store_true", help="add as a layer under the areas' existing tiles")
    a = ap.parse_args()
    TOP, LOW = a.top, a.low

    storm = STORMS[a.storm]
    w, s, e, n = a.bbox
    found = maxar.passes(storm.maxar_event, (w, s, e, n), Path(a.cache))
    if a.all:
        best = sorted((p for p in found if p.date > storm.landfall), key=lambda p: (p.clouds, p.date))
    else:
        best = maxar.pick(found, storm.landfall, after=True)
    if not best:
        raise SystemExit("no after-storm pass over that box")
    hrefs = [h for p in best for h in p.hrefs]
    print("passes:", ", ".join(p.label for p in best))

    # whole tiles at the top zoom
    x0, y1 = (math.floor(v / TILE_SIZE) for v in lonlat_to_pixel(w, s, TOP))
    x1, y0 = (math.floor(v / TILE_SIZE) for v in lonlat_to_pixel(e, n, TOP))
    win = Window(x0 * TILE_SIZE, y0 * TILE_SIZE, (x1 + 1) * TILE_SIZE, (y1 + 1) * TILE_SIZE)
    print(f"mosaic {win.width}x{win.height} px at z{TOP}")
    rgb, ok = maxar.mosaic(hrefs, win, TOP, a.overview)

    root = Path(a.out) / "tiles" / a.name
    img = np.dstack([rgb, (ok * 255).astype(np.uint8)])
    tx0, ty0 = x0, y0
    count = 0
    for z in range(TOP, LOW - 1, -1):
        h, wd = img.shape[:2]
        for j in range(h // TILE_SIZE):
            for i in range(wd // TILE_SIZE):
                t = img[j * TILE_SIZE : (j + 1) * TILE_SIZE, i * TILE_SIZE : (i + 1) * TILE_SIZE]
                if t[..., 3].mean() < 8:  # nothing here
                    continue
                p = root / str(z) / str(tx0 + i) / f"{ty0 + j}.jpg"
                p.parent.mkdir(parents=True, exist_ok=True)
                if t[..., 3].min() == 255:
                    Image.fromarray(t[..., :3]).save(p, quality=78, optimize=True)
                else:  # the region's edge: keep no-data see-through (served under the same name)
                    Image.fromarray(t, "RGBA").save(p.with_suffix(".png"), optimize=True)
                count += 1
        # next zoom out: pad to an even tile grid, then halve
        if tx0 % 2:
            img = np.pad(img, ((0, 0), (TILE_SIZE, 0), (0, 0)))
            tx0 -= 1
        if ty0 % 2:
            img = np.pad(img, ((TILE_SIZE, 0), (0, 0), (0, 0)))
            ty0 -= 1
        h, wd = img.shape[:2]
        img = np.pad(img, ((0, (-h) % (2 * TILE_SIZE)), (0, (-wd) % (2 * TILE_SIZE)), (0, 0)))
        h, wd = img.shape[:2]
        img = np.asarray(Image.fromarray(img, "RGBA").resize((wd // 2, h // 2), Image.LANCZOS))
        tx0, ty0 = tx0 // 2, ty0 // 2

    meta = {
        "url": f"/api/tiles/{a.name}/{{z}}/{{x}}/{{y}}.jpg",
        "min_zoom": LOW,
        "max_native_zoom": TOP,
        "bounds": [[s, w], [n, e]],
        "attribution": f"Maxar Open Data (CC BY-NC 4.0), {min(p.date for p in best)} to {max(p.date for p in best)}"
        if a.all
        else f"Maxar Open Data (CC BY-NC 4.0), {best[0].date}",
    }
    # the disaster's own layer: the map shows it when the disaster is picked
    (root / "layer.json").write_text(json.dumps({**meta, "storm": a.storm}))
    for area in a.areas:
        f = Path(a.out) / area / "tiles.json"
        layers = json.loads(f.read_text()) if f.exists() else []
        layers = [layers] if isinstance(layers, dict) else [x for x in layers if x["url"] != meta["url"]]
        layers = [meta, *layers] if a.under else [*layers, meta]  # drawn in order: first is lowest
        f.write_text(json.dumps(layers))
    print(f"{count} tiles in {root}")


if __name__ == "__main__":
    main()
