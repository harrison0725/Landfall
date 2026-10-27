"""Which storm, which post-storm flight, which tide gauges: for any box.

NOAA's Emergency Response Imagery site publishes a catalog per storm
(services/index.json) with every flight's tile URL and bounds. Bounds are a
flight's bounding box, not its footprint, so a flight is only used where a
tile at the box's center actually exists (missing tiles redirect to
clear.png).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from .geo import lonlat_to_pixel
from .sources import _get

CATALOG = "https://storms.ngs.noaa.gov/storms/{storm}/services/index.json"
COOPS_STATIONS = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json?type=waterlevels"


@dataclass(frozen=True)
class Storm:
    key: str
    name: str
    landfall: str  # YYYY-MM-DD; pre-storm imagery must be older
    stn_event: str  # USGS STN event name for high-water marks


STORMS = {
    s.key: s
    for s in [
        Storm("helene", "Hurricane Helene", "2024-09-26", "2024 Helene"),
        Storm("milton", "Hurricane Milton", "2024-10-09", "2024 Milton"),
        Storm("idalia", "Hurricane Idalia", "2023-08-30", "2023 Idalia"),
        Storm("ian", "Hurricane Ian", "2022-09-28", "2022 Ian"),
    ]
}


@dataclass(frozen=True)
class Flight:
    name: str  # e.g. 20240928b-rgb
    tiles: str
    bounds: tuple[float, float, float, float]

    @property
    def date(self) -> str:
        d = self.name[:8]
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"


def flights(storm: str) -> list[Flight]:
    out = []
    for e in json.loads(_get(CATALOG.format(storm=storm))[0]):
        url = e["tiles"][0]
        out.append(Flight(url.rstrip("/").split("/")[-4], url, tuple(e["bounds"])))
    return out


def covers(f: Flight, lon: float, lat: float, zoom: int = 19) -> bool:
    x, y = lonlat_to_pixel(lon, lat, zoom)
    url = f.tiles.format(z=zoom, x=int(x // 256), y=int(y // 256))
    try:
        _, final = _get(url)
    except Exception:
        return False
    return "clear.png" not in final


def find_flight(storm: str, bbox: tuple[float, float, float, float]) -> Flight | None:
    """The earliest flight after the storm with real tiles over the box."""
    lon = (bbox[0] + bbox[2]) / 2
    lat = (bbox[1] + bbox[3]) / 2
    cands = [f for f in flights(storm) if f.bounds[0] <= lon <= f.bounds[2] and f.bounds[1] <= lat <= f.bounds[3]]
    for f in sorted(cands, key=lambda f: f.name):
        if covers(f, lon, lat):
            return f
    return None


def nearest_gauges(lon: float, lat: float, max_km: float = 15.0, n: int = 2) -> list[dict]:
    """NOAA CO-OPS water-level stations near a point."""
    st = json.loads(_get(COOPS_STATIONS)[0])["stations"]
    rows = []
    for s in st:
        d = _km(lon, lat, s["lng"], s["lat"])
        if d <= max_km:
            rows.append((d, dict(station=s["id"], name=s["name"], lon=s["lng"], lat=s["lat"])))
    return [r for _, r in sorted(rows, key=lambda r: r[0])[:n]]


def _km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    kx = 111.32 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lon1 - lon2) * kx, (lat1 - lat2) * 110.57)
