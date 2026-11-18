"""Street-level photos of each building from Mapillary.

A photo from above sees the roof. It cannot see walls punched in by surge,
broken windows and doors, a house knocked off its pilings, or how high the
first floor is, which decides whether flood water reached the living space.
Street-level photos show those. Mapillary (Meta's street-level imagery
platform) has photos taken by volunteers, cities and survey teams, and some
towns were driven or walked in the weeks after a storm.

For each building this finds photos taken after the disaster (within
`days_after`), within `radius_m` of the building, that look toward it: the
camera's heading within `max_offset_deg` of the bearing to the building, or a
360° panorama, cut down to the view toward it. It keeps up to `per_building`
photos, closest and best-aimed first, and saves them for the agent.

Needs a Mapillary access token in MAPILLARY_ACCESS_TOKEN (a free client token
from https://www.mapillary.com/dashboard/developers). Without one this step
does nothing and the agent works from the aerial photos alone, as before.
The token goes in an Authorization header, never in a URL or a saved file.

Mapillary's imagery is CC BY-SA 4.0: credit "Mapillary" and the photo's
contributor where photos are shown.
"""

from __future__ import annotations

import io
import json
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

GRAPH = "https://graph.mapillary.com"
FIELDS = "id,captured_at,compass_angle,computed_compass_angle,geometry,computed_geometry,is_pano,thumb_1024_url,thumb_2048_url,sequence,creator"
TILE_DEG = 0.004  # Mapillary refuses bbox queries much larger than ~0.01 deg^2
M_PER_DEG_LAT = 111_320.0


def token() -> str | None:
    return os.environ.get("MAPILLARY_ACCESS_TOKEN") or None


@dataclass(frozen=True)
class Shot:
    image_id: str
    captured: str  # ISO date
    lon: float
    lat: float
    heading: float  # camera compass angle, degrees from north
    is_pano: bool
    url: str
    sequence: str
    creator: str


def _get(path: str, params: dict, tok: str, timeout: float = 30):
    import requests

    for attempt in range(4):
        r = requests.get(f"{GRAPH}/{path}", params=params, headers={"Authorization": f"OAuth {tok}"}, timeout=timeout)
        if r.status_code in (429, 500, 502, 503) and attempt < 3:
            import time

            time.sleep(2 ** (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise AssertionError("unreachable")


def _shot(row: dict) -> Shot | None:
    geom = row.get("computed_geometry") or row.get("geometry")
    if not geom:
        return None
    lon, lat = geom["coordinates"][:2]
    heading = row.get("computed_compass_angle")
    if heading is None:
        heading = row.get("compass_angle")
    url = row.get("thumb_2048_url") if row.get("is_pano") else row.get("thumb_1024_url")
    if heading is None or not url:
        return None
    when = datetime.fromtimestamp(int(row["captured_at"]) / 1000, tz=timezone.utc).date().isoformat()
    creator = (row.get("creator") or {}).get("username", "") if isinstance(row.get("creator"), dict) else ""
    seq = row.get("sequence") or ""
    if isinstance(seq, dict):
        seq = seq.get("id", "")
    return Shot(str(row["id"]), when, float(lon), float(lat), float(heading) % 360, bool(row.get("is_pano")), url, str(seq), creator)


def search(bbox: tuple[float, float, float, float], start: date, end: date, tok: str) -> list[Shot]:
    """Every photo in a box captured between two dates, split into tiles
    small enough for Mapillary's search."""
    w, s, e, n = bbox
    shots: dict[str, Shot] = {}
    t0 = datetime(start.year, start.month, start.day, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    t1 = datetime(end.year, end.month, end.day, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    y = s
    while y < n:
        x = w
        while x < e:
            tile = f"{x:.6f},{y:.6f},{min(x + TILE_DEG, e):.6f},{min(y + TILE_DEG, n):.6f}"
            out = _get("images", {"fields": FIELDS, "bbox": tile, "start_captured_at": t0, "end_captured_at": t1, "limit": 2000}, tok)
            for row in out.get("data", []):
                sh = _shot(row)
                if sh:
                    shots[sh.image_id] = sh
            x += TILE_DEG
        y += TILE_DEG
    return list(shots.values())


def _offset(lon: float, lat: float, lon2: float, lat2: float) -> tuple[float, float]:
    """(distance m, bearing deg from north) from the first point to the second."""
    dx = (lon2 - lon) * M_PER_DEG_LAT * math.cos(math.radians(lat))
    dy = (lat2 - lat) * M_PER_DEG_LAT
    return math.hypot(dx, dy), math.degrees(math.atan2(dx, dy)) % 360


def _angle(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def pick(shots: list[Shot], lon: float, lat: float, radius_m: float = 40, max_offset_deg: float = 35, per_building: int = 3) -> list[dict]:
    """The photos that look at a building at (lon, lat), best first."""
    cands = []
    for sh in shots:
        dist, bearing = _offset(sh.lon, sh.lat, lon, lat)
        if dist > radius_m or dist < 2:
            continue
        off = 0.0 if sh.is_pano else _angle(sh.heading, bearing)
        if off > max_offset_deg:
            continue
        # a close, square-on view beats a far or glancing one
        cands.append((dist + 0.5 * off, dist, bearing, off, sh))
    cands.sort(key=lambda c: c[0])
    out, seqs = [], []
    for _, dist, bearing, off, sh in cands:
        # different sequences (passes) first: two frames a metre apart add little
        if sh.sequence in seqs and len(cands) > per_building:
            continue
        seqs.append(sh.sequence)
        out.append(dict(image_id=sh.image_id, captured=sh.captured, distance_m=round(dist, 1), bearing=round(bearing),
                        heading=round(sh.heading), offset_deg=round(off), is_pano=sh.is_pano, creator=sh.creator, url=sh.url))
        if len(out) == per_building:
            break
    return out


def view_toward(img, heading: float, bearing: float, is_pano: bool, fov: float = 90.0):
    """For a panorama, the part facing the building (equirectangular: the
    middle column looks along `heading`). Other photos come back as they are."""
    if not is_pano:
        return img
    w, h = img.size
    cx = w * (0.5 + ((bearing - heading + 180) % 360 - 180) / 360.0)
    half = w * fov / 360.0 / 2
    left = int(cx - half)
    box_w = int(2 * half)
    top, bottom = int(h * 0.25), int(h * 0.75)
    from PIL import Image

    out = Image.new("RGB", (box_w, bottom - top))
    # paste in up to two pieces so a view across the panorama's seam works
    x = left % w
    first = min(box_w, w - x)
    out.paste(img.crop((x, top, x + first, bottom)), (0, 0))
    if first < box_w:
        out.paste(img.crop((0, top, box_w - first, bottom)), (first, 0))
    return out


def fetch(spec, root: Path, log=lambda *a, **k: None, days_after: int = 120, radius_m: float = 40, per_building: int = 3) -> dict:
    """Find and save street-level photos for every building in an area.

    Writes <area>/street.json ({"buildings": {id: [photo, ...]}, ...}) and
    the photos to <area>/street/<id>_<n>.jpg.
    """
    import requests
    from PIL import Image

    tok = token()
    d = root / spec.name
    if not tok:
        log("street_skipped", reason="no MAPILLARY_ACCESS_TOKEN")
        return {"skipped": "no token"}
    feats = json.loads((d / "buildings.geojson").read_text())["features"]
    landfall = date.fromisoformat(spec.landfall)
    start, end = landfall + timedelta(days=1), landfall + timedelta(days=days_after)
    cents = {f["properties"]["id"]: tuple(f["properties"]["centroid"]) for f in feats}
    lons = [c[0] for c in cents.values()]
    lats = [c[1] for c in cents.values()]
    pad = radius_m / M_PER_DEG_LAT * 1.5
    bbox = (min(lons) - pad, min(lats) - pad, max(lons) + pad, max(lats) + pad)
    shots = search(bbox, start, end, tok)
    log("street_search", photos=len(shots), start=start.isoformat(), end=end.isoformat())
    (d / "street").mkdir(exist_ok=True)
    out: dict[str, list[dict]] = {}
    for bid, (lon, lat) in cents.items():
        picks = pick(shots, lon, lat, radius_m=radius_m, per_building=per_building)
        kept = []
        for k, p in enumerate(picks):
            path = d / "street" / f"{bid}_{k}.jpg"
            try:
                r = requests.get(p["url"], timeout=60)
                r.raise_for_status()
                img = Image.open(io.BytesIO(r.content)).convert("RGB")
            except Exception as e:  # noqa: BLE001 - one bad photo shouldn't stop the area
                log("street_photo_failed", id=bid, image=p["image_id"], error=str(e)[:120])
                continue
            img = view_toward(img, p["heading"], p["bearing"], p["is_pano"])
            img.thumbnail((1024, 1024))
            img.save(path, "JPEG", quality=85)
            p = {k2: v for k2, v in p.items() if k2 != "url"}  # signed URLs expire; keep the id
            kept.append({**p, "file": path.name})
        if kept:
            out[bid] = kept
    summary = dict(buildings_with_photos=len(out), buildings=len(cents), photos=sum(len(v) for v in out.values()),
                   window=[start.isoformat(), end.isoformat()], radius_m=radius_m)
    (d / "street.json").write_text(json.dumps({"summary": summary, "buildings": out}, indent=1))
    log("street", **summary)
    return summary


def load(d: Path) -> dict[str, list[dict]]:
    """Saved street photos per building, with each photo as a data URL."""
    import base64

    p = d / "street.json"
    if not p.exists():
        return {}
    rows = json.loads(p.read_text())["buildings"]
    out = {}
    for bid, photos in rows.items():
        keep = []
        for ph in photos:
            f = d / "street" / ph["file"]
            if f.exists():
                keep.append({**ph, "data_url": "data:image/jpeg;base64," + base64.b64encode(f.read_bytes()).decode()})
        if keep:
            out[bid] = keep
    return out
