"""Before photos from anywhere: Esri World Imagery Wayback.

Esri keeps every past version ("release") of its World Imagery basemap, and
a metadata service says when the photo under any point was taken in each
release. For a disaster at a place and date, the newest release whose photo
there was taken before the date is the before photo. It covers the world and
is updated every few weeks, so it serves new disasters that NAIP (US only,
every two or three years) and Maxar (only events it activated for) don't.

Imagery: Esri, Maxar, Earthstar Geographics and the GIS user community,
under Esri's terms of use.
"""

from __future__ import annotations

import functools
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .geo import TILE_SIZE, Window
from .sources import _get

CONFIG = "https://s3-us-west-2.amazonaws.com/config.maptiles.arcgis.com/waybackconfig.json"
TILE = "https://wayback.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/WMTS/1.0.0/default028mm/MapServer/tile/{release}/{z}/{y}/{x}"
ATTRIBUTION = "Esri World Imagery Wayback (Esri, Maxar, Earthstar Geographics)"


@dataclass(frozen=True)
class Release:
    num: int
    date: str  # when Esri published it, YYYY-MM-DD
    metadata: str  # the metadata MapServer for this release


@dataclass(frozen=True)
class Photo:
    release: Release
    captured: str  # when the photo at the point was taken, YYYY-MM-DD
    source: str  # who took it, e.g. "Maxar" or "State of Florida"

    @property
    def label(self) -> str:
        return f"Esri World Imagery ({self.source}, {self.captured})"

    @property
    def tiles(self) -> str:
        return TILE.replace("{release}", str(self.release.num))


def releases(cache: Path) -> list[Release]:
    """Every Wayback release, newest first (fetched at most once a day)."""
    path = cache / "wayback" / "config.json"
    if not path.exists() or time.time() - path.stat().st_mtime > 86400:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_get(CONFIG)[0])
    rows = [
        Release(int(k), v["itemTitle"].rsplit(" ", 1)[-1].rstrip(")"), v["metadataLayerUrl"])
        for k, v in json.loads(path.read_text()).items()
    ]
    return sorted(rows, key=lambda r: r.date, reverse=True)


@functools.lru_cache(maxsize=4096)
def captured(rel: Release, lon: float, lat: float, zoom: int = 19) -> tuple[str, str] | None:
    """When and by whom the photo at a point was taken in a release; None
    where the release has no photo there."""
    # one layer per resolution; prefer the photo drawn at this zoom, else the
    # sharpest one there (drawn enlarged)
    q = (
        f"{rel.metadata}/identify?f=json&geometry={lon},{lat}&geometryType=esriGeometryPoint&sr=4326"
        f"&layers=all:2,3,4,5,6,7&tolerance=0&mapExtent={lon - 0.01},{lat - 0.01},{lon + 0.01},{lat + 0.01}"
        "&imageDisplay=400,400,96&returnGeometry=false"
    )
    rows = [r["attributes"] for r in json.loads(_get(q)[0]).get("results", []) if r["attributes"].get("SRC_DATE") not in (None, "Null", "")]
    if not rows:
        return None
    level = lambda a: int(a.get("MaxMapLevel") or 0)  # noqa: E731
    a = next((a for a in rows if int(a.get("MinMapLevel") or 0) <= zoom <= level(a)), None) or max(rows, key=level)
    d = str(a["SRC_DATE"])
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}", (a.get("NICE_DESC") or "unknown source").strip()


def before(lon: float, lat: float, date: str, cache: Path, zoom: int = 19) -> Photo | None:
    """The newest Wayback photo of a point taken before a date (YYYY-MM-DD).
    Asked several times in one assessment (finding the spot, then laying it
    out), so answers are kept per spot of about 1 km: photos are dated in
    blocks far bigger than that."""
    return _before(round(lon, 2), round(lat, 2), date, cache, zoom)


@functools.lru_cache(maxsize=512)
def _before(lon: float, lat: float, date: str, cache: Path, zoom: int = 19) -> Photo | None:
    """Releases, oldest first, fall in three runs: no photo here yet (-1),
    a photo from before the disaster (0), and a photo from after it, or none
    in a release after it (+1). The answer is the last 0. The start of the
    +1 run is found by binary search, about 8 lookups of a second each."""
    rels = releases(cache)[::-1]
    store = cache / "wayback" / "dates.json"  # photo dates already asked, kept across runs
    try:
        known = json.loads(store.read_text())
    except (OSError, ValueError):
        known = {}

    def dated(rel: Release) -> tuple[str, str] | None:
        k = f"{rel.num}/{lon}/{lat}/{zoom}"
        if k not in known:
            known[k] = captured(rel, lon, lat, zoom)
        return tuple(known[k]) if known[k] else None

    def key(i: int) -> int:
        got = dated(rels[i])
        if got is None:
            return -1 if rels[i].date < date else 1
        return 0 if got[0] < date else 1

    if key(len(rels) - 1) == 0:  # a disaster newer than Esri's latest photo here: one lookup
        lo = len(rels)
    else:
        lo, hi = 0, len(rels) - 1  # the first +1 is in [lo, hi]
        while lo < hi:  # one at a time: Esri answers slower, not sooner, when asked several at once
            mid = (lo + hi) // 2
            lo, hi = (mid + 1, hi) if key(mid) < 1 else (lo, mid)
    store.parent.mkdir(parents=True, exist_ok=True)
    tmp = store.with_suffix(f".{threading.get_ident()}.tmp")  # two assessments at once mustn't half-write it
    tmp.write_text(json.dumps(known))
    tmp.replace(store)
    if lo == 0 or key(lo - 1) != 0:
        return None
    return Photo(rels[lo - 1], *dated(rels[lo - 1]))


def mosaic(photo: Photo, window: Window, zoom: int, cache: Path, workers: int = 8) -> tuple[np.ndarray, np.ndarray]:
    """Stitch a release's tiles over a window: (rgb uint8 HxWx3, valid HxW)."""
    from io import BytesIO

    from PIL import Image

    folder = cache / "wayback" / str(photo.release.num) / str(zoom)
    folder.mkdir(parents=True, exist_ok=True)

    def fetch(t: tuple[int, int]) -> tuple[tuple[int, int], np.ndarray | None]:
        tx, ty = t
        path = folder / f"{tx}_{ty}.jpg"
        if not path.exists():
            try:
                body, _ = _get(photo.tiles.format(z=zoom, x=tx, y=ty))
            except OSError:
                return t, None
            path.write_bytes(body)
        try:
            img = np.asarray(Image.open(BytesIO(path.read_bytes())).convert("RGB"))
        except OSError:
            return t, None
        # past its photos Esri draws a flat "map data not yet available" tile
        return t, None if img.std() < 3 else img

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
