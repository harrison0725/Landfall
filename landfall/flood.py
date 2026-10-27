"""Flood depth at each building: storm water surface minus ground elevation.

Water surface comes from surveyed high-water marks (USGS Short-Term Network)
and tide gauges (NOAA CO-OPS), both in feet NAVD88. Ground comes from the
USGS 3DEP 1 m lidar elevation model via the Elevation Point Query Service,
also NAVD88. Over one small town the surge peak is close to flat, so a single
water surface is used, from the observations nearest the area. This is
depth above ground; how much of it reached the living space depends on how
high the first floor is, which the photos can't show.
"""

from __future__ import annotations

import json
import math
import urllib.parse
from dataclasses import dataclass

from .sources import _get

STN_HWMS = "https://api.waterdata.usgs.gov/stnservices/hwms/?event={event}"
STN_EVENTS = "https://api.waterdata.usgs.gov/stnservices/events/"
COOPS = (
    "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?product=water_level&application=landfall"
    "&begin_date={begin}&end_date={end}&datum=NAVD&station={station}&time_zone=gmt&units=english&format=json"
)
EPQS = "https://epqs.nationalmap.gov/v1/json?x={lon}&y={lat}&units=Feet&wkid=4326"
NAVD88 = 2  # STN vertical datum id


@dataclass(frozen=True)
class WaterLevel:
    source: str  # e.g. "USGS HWM 46592" or "NOAA gauge 8727520"
    lon: float
    lat: float
    elev_ft: float  # NAVD88
    note: str = ""


def _km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    kx = 111.32 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lon1 - lon2) * kx, (lat1 - lat2) * 110.57)


def stn_event_id(name: str) -> int:
    events = json.loads(_get(STN_EVENTS)[0])
    for e in events:
        if e["name"] == name:
            return int(e["id"])
    raise LookupError(f"no STN event named {name!r}")


def high_water_marks(event: str, center: tuple[float, float], radius_km: float) -> list[WaterLevel]:
    """Surveyed NAVD88 high-water marks within radius_km of center."""
    eid = stn_event_id(event)
    out = []
    for h in json.loads(_get(STN_HWMS.format(event=eid))[0]):
        if h.get("vdatum") != NAVD88 or not h.get("elev_ft") or h.get("latitude_dd") is None:
            continue
        if _km(h["longitude_dd"], h["latitude_dd"], *center) > radius_km:
            continue
        out.append(
            WaterLevel(
                f"USGS HWM {h['id']}",
                h["longitude_dd"],
                h["latitude_dd"],
                float(h["elev_ft"]),
                (h.get("location_description") or "").strip(),
            )
        )
    return out


def gauge_peak(station: str, lon: float, lat: float, begin: str, end: str) -> WaterLevel:
    """Peak observed water level (NAVD88) at a NOAA tide gauge. Dates YYYYMMDD."""
    d = json.loads(_get(COOPS.format(station=station, begin=begin, end=end))[0])
    vals = [(x["t"], float(x["v"])) for x in d.get("data", []) if x.get("v")]
    if not vals:
        raise LookupError(f"no water levels from gauge {station}: {d.get('error')}")
    t, v = max(vals, key=lambda x: x[1])
    return WaterLevel(f"NOAA gauge {station}", lon, lat, v, f"peak at {t} UTC")


def ground_ft(lon: float, lat: float) -> float | None:
    """Bare-earth ground elevation (ft NAVD88) from USGS 3DEP. None if the
    service has no value there (e.g. over open water)."""
    body, _ = _get(EPQS.format(lon=urllib.parse.quote(str(lon)), lat=urllib.parse.quote(str(lat))))
    v = json.loads(body).get("value")
    return None if v is None or float(v) < -1000 else float(v)


def water_surface(levels: list[WaterLevel]) -> float:
    """One water surface for a small area: the median of the observations."""
    if not levels:
        raise ValueError("no water level observations")
    vals = sorted(w.elev_ft for w in levels)
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2


def local_surface(levels: list[WaterLevel], lon: float, lat: float, max_km: float = 1.0, n: int = 3) -> float | None:
    """The water surface at one point of a river flood: the nearest marks
    within max_km, weighted by inverse distance. None when no mark is that
    close, since a river's level a few km away says little about here."""
    near = sorted((_km(w.lon, w.lat, lon, lat), w.elev_ft) for w in levels)
    near = [x for x in near if x[0] <= max_km][:n]
    if not near:
        return None
    if near[0][0] < 0.01:
        return near[0][1]
    wts = [1 / d for d, _ in near]
    return sum(w * e for w, (_, e) in zip(wts, near)) / sum(wts)


def depth_above_ground(surface_ft: float, ground: float | None) -> float | None:
    if ground is None:
        return None
    return max(0.0, surface_ft - ground)
