"""Fetch the three inputs for an area: post-storm photos, pre-storm photos and
building outlines.

- Post-storm: NOAA NGS emergency response imagery, served as XYZ JPEG tiles
  per flight (https://storms.ngs.noaa.gov/storms/helene/services/index.json).
  Tiles outside a flight's footprint redirect to a 1x1 clear PNG.
- Pre-storm: USDA NAIP, read from Cloud-Optimized GeoTIFFs on Microsoft
  Planetary Computer and warped into the same Web Mercator pixel grid.
- Buildings: Microsoft Global ML Building Footprints, one gzipped GeoJSON
  lines file per zoom-9 quadkey.

Everything lands in one Web Mercator pixel grid at a fixed zoom, so a building
crop is a plain array slice of each mosaic.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import math
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .geo import TILE_SIZE, Window, lonlat_to_pixel

NOAA_TILE_URL = "https://stormscdn.ngs.noaa.gov/{flight}/{z}/{x}/{y}"
MS_LINKS_URL = "https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv"
PC_STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
EARTH_HALF_CIRCUMFERENCE = 20037508.342789244  # Web Mercator, meters
USER_AGENT = "landfall/0.1 (+https://github.com/harrison0725/landfall)"


def _get(url: str, retries: int = 3) -> tuple[bytes, str]:
    """GET with retries. Returns the body and the final URL after redirects."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read(), r.geturl()
        except OSError:
            if attempt == retries - 1:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


# --- Web Mercator helpers -------------------------------------------------


def window_bounds_3857(window: Window, zoom: int) -> tuple[float, float, float, float]:
    """(left, bottom, right, top) of a pixel window in EPSG:3857 meters."""
    m_per_px = 2 * EARTH_HALF_CIRCUMFERENCE / (TILE_SIZE * 2**zoom)
    left = window.x0 * m_per_px - EARTH_HALF_CIRCUMFERENCE
    right = window.x1 * m_per_px - EARTH_HALF_CIRCUMFERENCE
    top = EARTH_HALF_CIRCUMFERENCE - window.y0 * m_per_px
    bottom = EARTH_HALF_CIRCUMFERENCE - window.y1 * m_per_px
    return left, bottom, right, top


def pixel_bbox_lonlat(window: Window, zoom: int) -> tuple[float, float, float, float]:
    """(west, south, east, north) of a pixel window."""
    from .geo import pixel_to_lonlat

    w, n = pixel_to_lonlat(window.x0, window.y0, zoom)
    e, s = pixel_to_lonlat(window.x1, window.y1, zoom)
    return (w, s, e, n)


def bbox_window(bbox: tuple[float, float, float, float], zoom: int) -> Window:
    """Pixel window covering a (west, south, east, north) lon/lat box."""
    w, s, e, n = bbox
    x0, y0 = lonlat_to_pixel(w, n, zoom)
    x1, y1 = lonlat_to_pixel(e, s, zoom)
    return Window(math.floor(x0), math.floor(y0), math.ceil(x1), math.ceil(y1))


# --- Post-storm: NOAA ------------------------------------------------------


def noaa_mosaic(
    flight: str, window: Window, zoom: int, cache_dir: Path, workers: int = 8
) -> tuple[np.ndarray, np.ndarray]:
    """Stitch NOAA tiles covering a window.

    Returns (rgb uint8 HxWx3, valid bool HxW). Pixels outside the flight's
    footprint are marked invalid rather than silently black.
    """
    from PIL import Image

    cache = cache_dir / flight / str(zoom)
    cache.mkdir(parents=True, exist_ok=True)

    def fetch(t: tuple[int, int]) -> tuple[tuple[int, int], np.ndarray | None]:
        tx, ty = t
        path = cache / f"{tx}_{ty}.jpg"
        missing = cache / f"{tx}_{ty}.none"
        if missing.exists():
            return t, None
        if not path.exists():
            body, final = _get(NOAA_TILE_URL.format(flight=flight, z=zoom, x=tx, y=ty))
            if final.endswith("clear.png") or len(body) < 200:
                missing.touch()
                return t, None
            path.write_bytes(body)
        return t, np.asarray(Image.open(path).convert("RGB"))

    tiles = window.tiles()
    tx0, ty0 = tiles[0]
    nty = (window.y1 - 1) // TILE_SIZE - ty0 + 1
    ntx = (window.x1 - 1) // TILE_SIZE - tx0 + 1
    big = np.zeros((nty * TILE_SIZE, ntx * TILE_SIZE, 3), np.uint8)
    valid = np.zeros(big.shape[:2], bool)
    with ThreadPoolExecutor(workers) as pool:
        for (tx, ty), img in pool.map(fetch, tiles):
            if img is None:
                continue
            ys, xs = (ty - ty0) * TILE_SIZE, (tx - tx0) * TILE_SIZE
            big[ys : ys + TILE_SIZE, xs : xs + TILE_SIZE] = img
            valid[ys : ys + TILE_SIZE, xs : xs + TILE_SIZE] = True
    oy, ox = window.y0 - ty0 * TILE_SIZE, window.x0 - tx0 * TILE_SIZE
    sl = (slice(oy, oy + window.height), slice(ox, ox + window.width))
    return big[sl], valid[sl]


# --- Pre-storm: NAIP -------------------------------------------------------


@dataclass(frozen=True)
class NaipScene:
    id: str
    date: str
    gsd: float
    href: str


def naip_scenes(lon: float, lat: float, before: str, bbox: tuple[float, float, float, float] | None = None) -> list[NaipScene]:
    """NAIP scenes covering a point (or touching a bbox), newest first,
    captured before a date (YYYY-MM-DD). Hrefs are signed and expire after
    about an hour."""
    import planetary_computer as pc
    import pystac_client

    if bbox:
        w, s, e, n = bbox
        geom = {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}
    else:
        geom = {"type": "Point", "coordinates": [lon, lat]}
    cat = pystac_client.Client.open(PC_STAC_URL, modifier=pc.sign_inplace)
    items = cat.search(
        collections=["naip"],
        intersects=geom,
        datetime=f"2000-01-01/{before}",
    ).items()
    scenes = [
        NaipScene(it.id, it.datetime.date().isoformat(), float(it.properties.get("gsd", 0)), it.assets["image"].href)
        for it in items
    ]
    return sorted(scenes, key=lambda s: s.date, reverse=True)


def naip_mosaic(scene: NaipScene, window: Window, zoom: int) -> tuple[np.ndarray, np.ndarray]:
    """Warp a NAIP scene into a Web Mercator pixel window.

    Returns (rgb uint8 HxWx3, valid bool HxW). NAIP's fourth band (near
    infrared) is dropped here; it is useful later for water and vegetation.
    """
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds
    from rasterio.warp import reproject

    data = np.zeros((3, window.height, window.width), np.uint8)
    dst_transform = from_bounds(*window_bounds_3857(window, zoom), window.width, window.height)
    with rasterio.open(scene.href) as src:
        for i in range(3):
            reproject(
                rasterio.band(src, i + 1),
                data[i],
                dst_transform=dst_transform,
                dst_crs="EPSG:3857",
                dst_nodata=0,
                resampling=Resampling.bilinear,
            )
    rgb = np.moveaxis(data, 0, -1).astype(np.uint8)
    return rgb, rgb.any(axis=2)


# --- Buildings: Microsoft footprints ---------------------------------------


def _ms_links(cache_dir: Path) -> list[dict]:
    path = cache_dir / "ms_dataset_links.csv"
    if not path.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_get(MS_LINKS_URL)[0])
    return list(csv.DictReader(io.StringIO(path.read_text())))


def ms_country(bbox: tuple[float, float, float, float], cache_dir: Path) -> str | None:
    """The country Microsoft files a box's buildings under (e.g. "Jamaica",
    "UnitedStates"); the first one on a border."""
    w, s, e, n = bbox
    qk = quadkey((w + e) / 2, (s + n) / 2)
    names = [r["Location"] for r in _ms_links(cache_dir) if r["QuadKey"] == qk]
    return names[0] if names else None


def quadkey(lon: float, lat: float, zoom: int = 9) -> str:
    n = 2**zoom
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    digits = []
    for i in range(zoom, 0, -1):
        mask = 1 << (i - 1)
        digits.append(str((1 if x & mask else 0) + (2 if y & mask else 0)))
    return "".join(digits)


def ms_buildings(
    bbox: tuple[float, float, float, float], cache_dir: Path, region: str | None = "UnitedStates"
) -> list[dict]:
    """Microsoft footprints whose centroid falls in (west, south, east, north).

    Each is a GeoJSON Feature with an added properties.centroid [lon, lat].
    Assumes the box sits inside one zoom-9 quadkey (about 70 km), which holds
    for a town.
    """
    from shapely.geometry import shape

    w, s, e, n = bbox
    qk = quadkey((w + e) / 2, (s + n) / 2)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"ms_{region or 'any'}_{qk}.geojsonl.gz"
    if not path.exists():
        rows = [r for r in _ms_links(cache_dir) if r["QuadKey"] == qk and (region is None or r["Location"] == region)]
        if not rows:
            raise LookupError(f"no Microsoft footprints for quadkey {qk}" + (f" in {region}" if region else ""))
        # a quadkey on a border has one file per country; keep them all
        path.write_bytes(b"".join(_get(r["Url"])[0] for r in rows))

    out = []
    with gzip.open(path, "rt") as f:
        for line in f:
            feat = json.loads(line)
            c = shape(feat["geometry"]).centroid
            if w <= c.x <= e and s <= c.y <= n:
                feat["properties"]["centroid"] = [c.x, c.y]
                out.append(feat)
    return out
