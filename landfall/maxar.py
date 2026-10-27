"""Before and after photos anywhere in the world: Maxar's Open Data program.

After a major disaster Maxar publishes satellite imagery of the affected area
(about 0.3 to 0.6 m per pixel), often with older images of the same places,
as a static STAC catalog of Cloud-Optimized GeoTIFFs on S3
(https://maxar-opendata.s3.amazonaws.com/events/catalog.json). Licensed CC
BY-NC 4.0: free for non-commercial use, with attribution.

An event holds acquisitions (one satellite pass each); an acquisition holds
items, one per grid tile (about 5 km, in Maxar's own UTM grid) it touches.
For a box we take the items whose bounds touch it, measure how much of the box each acquisition
actually covers (its data footprint, not the tile), and rank: after the
storm, earliest clear pass first (closest to the damage, before cleanup);
before the storm, newest clear pass first.
"""

from __future__ import annotations

import json
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .geo import Window
from .sources import _get, window_bounds_3857

BASE = "https://maxar-opendata.s3.amazonaws.com/events/"
MAX_CLOUDS = 50  # percent of a ~5 km tile; the box itself may be clear
CLOUDY = 30


@dataclass(frozen=True)
class Pass:
    acquisition: str
    date: str
    platform: str
    gsd: float
    off_nadir: float
    clouds: float
    coverage: float  # share of the box with data
    hrefs: tuple[str, ...]  # visual COGs for the box's tiles

    @property
    def label(self) -> str:
        return f"Maxar {self.platform} {self.date} ({self.acquisition}, {self.gsd:.2f} m)"


def _json(url: str) -> dict:
    return json.loads(_get(url)[0])


def acquisitions(event: str, cache: Path) -> list[dict]:
    """Every acquisition in an event: id, date and its items' links and bounds."""
    path = cache / "maxar" / f"{event}.json"
    if path.exists():
        return json.loads(path.read_text())
    col = _json(f"{BASE}{event}/collection.json")
    hrefs = [link["href"][2:] for link in col["links"] if link["rel"] == "child"]

    def one(h: str) -> dict:
        a = _json(f"{BASE}{event}/{h}")
        base = f"{BASE}{event}/{h.rsplit('/', 1)[0]}/"
        links = [_resolve(base, link["href"]) for link in a["links"] if link["rel"] == "item"]
        # the extent lists the whole acquisition's bbox, then each item's in link order
        boxes = a["extent"]["spatial"]["bbox"][1:]
        items = [{"href": h, "bbox": b} for h, b in zip(links, boxes)]
        return {"id": a["id"], "date": a["extent"]["temporal"]["interval"][0][0][:10], "items": items}

    with ThreadPoolExecutor(16) as pool:
        out = list(pool.map(one, hrefs))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    return out


def _resolve(base: str, href: str) -> str:
    parts = base.rstrip("/").split("/")
    for p in href.split("/"):
        if p == "..":
            parts.pop()
        elif p != ".":
            parts.append(p)
    return "/".join(parts)


def _touches(a, b) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _coverage(items: list[dict], bbox: tuple[float, float, float, float]) -> float:
    from rasterio.warp import transform_geom
    from shapely.geometry import box, shape
    from shapely.ops import unary_union

    polys = [shape(transform_geom(f"EPSG:{it['properties']['proj:epsg']}", "EPSG:4326", it["properties"]["proj:geometry"])) for it in items]
    b = box(*bbox)
    return unary_union(polys).intersection(b).area / b.area if polys else 0.0


def passes(event: str, bbox: tuple[float, float, float, float], cache: Path) -> list[Pass]:
    """Every acquisition with data over the box, with how much of it it covers."""
    cand = [(a, [i["href"] for i in a["items"] if _touches(i["bbox"], bbox)]) for a in acquisitions(event, cache)]
    cand = [c for c in cand if c[1]]

    def one(c: tuple[dict, list[str]]) -> Pass | None:
        a, links = c
        items = [_json(h) for h in links]
        cover = _coverage(items, bbox)
        if cover <= 0:
            return None
        p = items[0]["properties"]
        return Pass(
            a["id"],
            a["date"],
            p.get("platform", ""),
            float(p.get("gsd", 0)),
            float(p.get("view:off_nadir", 0)),
            max(float(it["properties"].get("tile:clouds_percent", 0)) for it in items),
            round(cover, 3),
            tuple(_resolve(h.rsplit("/", 1)[0] + "/", it["assets"]["visual"]["href"]) for h, it in zip(links, items)),
        )

    with ThreadPoolExecutor(8) as pool:
        return [p for p in pool.map(one, cand) if p]


def pick(found: list[Pass], landfall: str, after: bool) -> list[Pass]:
    """Usable passes on one side of landfall, best first. More than one
    comes back so the mosaic can fill one pass's edges from the next."""
    side = [p for p in found if (p.date > landfall if after else p.date < landfall) and p.clouds <= MAX_CLOUDS]
    # earliest after (damage before cleanup), newest before (closest to how it stood)
    day = (lambda p: date.fromisoformat(p.date).toordinal()) if after else (lambda p: -date.fromisoformat(p.date).toordinal())
    side.sort(key=lambda p: (p.coverage < 0.95, p.clouds > CLOUDY, day(p), p.off_nadir))
    return side


def mosaic(hrefs: list[str], window: Window, zoom: int) -> tuple[np.ndarray, np.ndarray]:
    """Warp visual COGs into a Web Mercator pixel window, first href first,
    later ones filling only where earlier ones had no data. Reads only the
    blocks the window needs."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds
    from rasterio.vrt import WarpedVRT

    rgb = np.zeros((window.height, window.width, 3), np.uint8)
    ok = np.zeros((window.height, window.width), bool)
    transform = from_bounds(*window_bounds_3857(window, zoom), window.width, window.height)
    env = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MULTIRANGE="YES", GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES")
    with rasterio.Env(**env):
        for h in hrefs:
            if ok.all():
                break
            with rasterio.open(h) as src, WarpedVRT(
                src, crs="EPSG:3857", transform=transform, width=window.width, height=window.height, resampling=Resampling.bilinear, nodata=0
            ) as vrt:
                a = np.moveaxis(vrt.read(indexes=[1, 2, 3]), 0, -1)
            have = a.any(axis=2) & ~ok
            rgb[have] = a[have]
            ok |= have
    return rgb, ok
