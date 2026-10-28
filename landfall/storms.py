"""Which storm, which post-storm photos, which tide gauges: for any box.

US storms use NOAA's aerial flights (sharpest, flown within days). Storms
anywhere else use Maxar's Open Data satellite imagery (maxar.py), which
also supplies the pre-storm photos there. Landfall dates split an event's
images into before and after.

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
    stn_event: str  # USGS STN event name for high-water marks ("" outside the US)
    maxar_event: str = ""  # Maxar Open Data event id, for storms outside NOAA's coverage
    where: str = "United States"
    kind: str = "hurricane"  # hurricane, wildfire, earthquake or flood: decides what damage looks like
    catalog: str = ""  # NOAA catalog when it isn't the key (yearly ones like 2025_eri hold many events)
    until: str = ""  # last flight date for this event in a shared catalog
    view: tuple = ()  # (lat, lon, zoom) where its photos are, for picking it in the menu

    @property
    def provider(self) -> str:
        return "maxar" if self.maxar_event else "noaa"


STORMS = {
    s.key: s
    for s in [
        Storm("helene", "Hurricane Helene", "2024-09-26", "2024 Helene", where="Florida, United States", view=(29.137, -83.035, 15)),
        Storm("milton", "Hurricane Milton", "2024-10-09", "2024 Milton", where="Florida, United States", view=(27.33, -82.54, 14)),
        Storm("idalia", "Hurricane Idalia", "2023-08-30", "2023 Idalia", where="Florida, United States", view=(29.66, -83.39, 14)),
        Storm("ian", "Hurricane Ian", "2022-09-28", "2022 Ian", where="Florida, United States", view=(26.45, -81.95, 14)),
        # Outside the US: Maxar Open Data. Dates are landfall on the place
        # most of the event's imagery covers.
        Storm("melissa", "Hurricane Melissa", "2025-10-28", "", "Hurricane-Melissa-Oct-2025", "Jamaica, Cuba, Haiti", view=(18.465, -77.915, 14)),
        Storm("ditwah", "Cyclone Ditwah", "2025-11-28", "", "Cyclone-Ditwah-Sri-Lanka-Nov-2025", "Sri Lanka", view=(7.1684, 79.9368, 16)),
        # Other disasters. NOAA flew the LA fires and the Texas floods into its
        # 2025 catalog, so a date window picks out each event's flights.
        Storm("la_fires", "Los Angeles wildfires", "2025-01-07", "", where="Los Angeles, United States", kind="wildfire", catalog="2025_eri", until="2025-01-31", view=(34.19, -118.13, 14)),
        Storm("texas_floods", "Texas Hill Country floods", "2025-07-04", "2025 July TX Flood", where="Texas, United States", kind="flood", catalog="2025_eri", until="2025-07-31", view=(30.07, -99.24, 14)),
        Storm("myanmar_quake", "Myanmar earthquake", "2025-03-28", "", "Earthquake-Myanmar-March-2025", "Myanmar", kind="earthquake", view=(21.959, 96.118, 15)),
        # More US disasters NOAA flew (older storms are only on NOAA's site as
        # downloads, not map tiles, so they can't be read here)
        Storm("michael", "Hurricane Michael", "2018-10-10", "2018 Michael", where="Florida, United States", view=(29.94, -85.41, 15)),
        Storm("ida", "Hurricane Ida", "2021-08-29", "2021 Ida", where="Louisiana, United States", view=(29.236, -89.99, 15)),
        Storm("nicole", "Hurricane Nicole", "2022-11-10", "", where="Florida, United States", view=(29.12, -80.97, 15)),
        Storm("debby", "Hurricane Debby", "2024-08-05", "2024 Debby", where="Florida, United States", view=(29.67, -83.39, 15)),
        Storm("francine", "Hurricane Francine", "2024-09-11", "2024 Francine", where="Louisiana, United States", view=(29.70, -91.20, 15)),
        Storm("kentucky_floods_2025", "Kentucky floods", "2025-04-05", "2025 April KY Flood", where="Kentucky, United States", kind="flood", catalog="2025_eri", until="2025-04-30", view=(38.20, -84.87, 15)),
        # Worldwide, from Maxar Open Data: events with photos from both before
        # and after (several in the catalog only have one side, and are left out)
        Storm("turkey_quake", "Turkey–Syria earthquake", "2023-02-06", "", "Kahramanmaras-turkey-earthquake-23", "Turkey, Syria", kind="earthquake", view=(36.205, 36.16, 16)),
        Storm("spain_floods", "Valencia floods", "2024-10-29", "", "Floods-Spain-Oct24", "Spain", kind="flood", view=(39.5099, -0.3538, 16)),
        Storm("brazil_floods", "Rio Grande do Sul floods", "2024-05-01", "", "Brazil-Flooding-May24", "Brazil", kind="flood", view=(-29.2628, -51.9797, 16)),
        Storm("nigeria_floods", "Mokwa flood", "2025-05-29", "", "Nigeria-Flooding-May-2025", "Nigeria", kind="flood", view=(9.2893, 5.071, 16)),
    ]
}


@dataclass(frozen=True)
class Flight:
    name: str  # e.g. 20240928b-rgb
    tiles: str
    bounds: tuple[float, float, float, float]
    maxzoom: int = 21

    @property
    def date(self) -> str:
        d = self.name[:8]
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"


def flights(storm: str) -> list[Flight]:
    s = STORMS.get(storm)
    out = []
    for e in json.loads(_get(CATALOG.format(storm=(s and s.catalog) or storm))[0]):
        url = e["tiles"][0]
        f = Flight(url.rstrip("/").split("/")[-4], url, tuple(e["bounds"]), int(e.get("maxzoom", 21)))
        if s and s.until and not (s.landfall <= f.date <= s.until):
            continue
        out.append(f)
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
    """The earliest flight after the storm with real tiles over the box,
    preferring NOAA's own aerial photos to the satellite scenes it also
    publishes (those stop at zoom 19 and come through haze and smoke)."""
    lon = (bbox[0] + bbox[2]) / 2
    lat = (bbox[1] + bbox[3]) / 2
    cands = [f for f in flights(storm) if f.bounds[0] <= lon <= f.bounds[2] and f.bounds[1] <= lat <= f.bounds[3]]
    for f in sorted(cands, key=lambda f: (f.maxzoom < 20, f.name)):
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
