"""Web Mercator math for turning building outlines into pixel crops.

Aerial tile services (NOAA emergency imagery, NAIP mosaics) are served as
XYZ tiles in Web Mercator, so everything here works in that pixel space.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

TILE_SIZE = 256


def lonlat_to_pixel(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    """Global pixel coordinates of a point at a zoom level."""
    scale = TILE_SIZE * (2**zoom)
    x = (lon + 180.0) / 360.0 * scale
    siny = math.sin(math.radians(lat))
    siny = min(max(siny, -0.9999), 0.9999)
    y = (0.5 - math.log((1 + siny) / (1 - siny)) / (4 * math.pi)) * scale
    return x, y


def pixel_to_lonlat(x: float, y: float, zoom: int) -> tuple[float, float]:
    scale = TILE_SIZE * (2**zoom)
    lon = x / scale * 360.0 - 180.0
    n = math.pi - 2 * math.pi * y / scale
    lat = math.degrees(math.atan(math.sinh(n)))
    return lon, lat


def meters_per_pixel(lat: float, zoom: int) -> float:
    return 156543.03392 * math.cos(math.radians(lat)) / (2**zoom)


@dataclass(frozen=True)
class Window:
    """A pixel rectangle in global pixel space: [x0, x1) by [y0, y1)."""

    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    def tiles(self) -> list[tuple[int, int]]:
        """Every (tx, ty) tile this window touches."""
        return [
            (tx, ty)
            for ty in range(self.y0 // TILE_SIZE, (self.y1 - 1) // TILE_SIZE + 1)
            for tx in range(self.x0 // TILE_SIZE, (self.x1 - 1) // TILE_SIZE + 1)
        ]


def building_window(
    outline: list[tuple[float, float]], zoom: int, margin_m: float = 8.0
) -> Window:
    """Pixel window around a building outline (lon, lat pairs), padded by a
    margin in meters so debris and water next to the house are included and
    the alignment search has room to shift."""
    pts = [lonlat_to_pixel(lon, lat, zoom) for lon, lat in outline]
    lat_c = sum(lat for _, lat in outline) / len(outline)
    pad = margin_m / meters_per_pixel(lat_c, zoom)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return Window(
        math.floor(min(xs) - pad),
        math.floor(min(ys) - pad),
        math.ceil(max(xs) + pad),
        math.ceil(max(ys) + pad),
    )


def outline_to_window_pixels(
    outline: list[tuple[float, float]], zoom: int, window: Window
) -> list[tuple[float, float]]:
    """Outline vertices in the window's local pixel coordinates."""
    out = []
    for lon, lat in outline:
        x, y = lonlat_to_pixel(lon, lat, zoom)
        out.append((x - window.x0, y - window.y0))
    return out
