"""Place names: search a city, state or country, and name a drawn box.

Both go through OpenStreetMap's Nominatim, which asks for a real User-Agent
and at most one request a second, so every answer is cached and calls are
spaced out. Which disasters have photos of a place comes from the same
catalogs the assessment uses: NOAA flight bounds and Maxar acquisitions.
"""

from __future__ import annotations

import functools
import json
import threading
import time
import urllib.parse
from pathlib import Path

from .sources import _get

SEARCH = "https://nominatim.openstreetmap.org/search?format=jsonv2&limit=6&addressdetails=1&q={q}"
REVERSE = "https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=14&addressdetails=1&lat={lat}&lon={lon}"
_lock = threading.Lock()
_last = [0.0]


def _nominatim(url: str):
    with _lock:  # Nominatim's usage policy: one request a second
        wait = 1.0 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        return json.loads(_get(url + "&accept-language=en")[0])


def _short(addr: dict, fallback: str) -> str:
    """"Cedar Key, Florida" rather than OSM's whole address line."""
    town = next((addr[k] for k in ("city", "town", "village", "hamlet", "suburb", "municipality", "county") if addr.get(k)), "")
    region = addr.get("state") or addr.get("region") or addr.get("province") or ""
    country = addr.get("country", "")
    parts = [p for p in (town, region if addr.get("country_code") == "us" else (region if not town else country)) if p]
    if not parts:
        parts = [p for p in (region, country) if p]
    return ", ".join(dict.fromkeys(parts)) or fallback


@functools.lru_cache(maxsize=256)
def search(q: str) -> list[dict]:
    rows = _nominatim(SEARCH.format(q=urllib.parse.quote(q)))
    out = []
    for r in rows:
        s, n, w, e = (float(x) for x in r["boundingbox"])
        addr = r.get("address", {})
        # a street address: "12 2nd Street, Cedar Key, Florida", not just the town
        street = " ".join(p for p in (addr.get("house_number"), addr.get("road")) if p)
        town = _short(addr, r.get("name", ""))
        out.append(
            dict(
                label=r.get("display_name", ""),
                short=f"{street}, {town}" if street and addr.get("house_number") else town,
                address=bool(addr.get("house_number") or r.get("addresstype") in ("building", "house", "place")),
                lat=float(r["lat"]),
                lon=float(r["lon"]),
                bbox=[w, s, e, n],
            )
        )
    return out


@functools.lru_cache(maxsize=1024)
def name_of(lat: float, lon: float) -> str:
    r = _nominatim(REVERSE.format(lat=lat, lon=lon))
    return _short(r.get("address", {}), "") if isinstance(r, dict) else ""


def disasters_at(lon: float, lat: float, bbox: list[float] | None, cache: Path) -> list[str]:
    """Disasters with after-photos over a place (its bounding box, or a
    point), newest first."""
    from .maxar import acquisitions
    from .storms import STORMS

    w, s, e, n = bbox or (lon - 0.01, lat - 0.01, lon + 0.01, lat + 0.01)

    def touches(b) -> bool:
        return not (b[2] < w or b[0] > e or b[3] < s or b[1] > n)

    hits = []
    for st in STORMS.values():
        try:
            if st.provider == "noaa":
                ok = any(touches(f.bounds) for f in _flights(st.key) if f.date >= st.landfall)
            else:
                acq = acquisitions(st.maxar_event, cache)
                ok = any(touches(i["bbox"]) for a in acq if a["date"] > st.landfall for i in a["items"])
        except Exception:
            ok = False
        if ok:
            hits.append(st)
    return [h.key for h in sorted(hits, key=lambda h: h.landfall, reverse=True)]


@functools.lru_cache(maxsize=64)
def _flights(key: str):
    from .storms import flights

    return flights(key)

