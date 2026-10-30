"""The Landfall web app: a conversation with the agent, with the map as its
evidence.

    pip install -e ".[app,data,sam]"
    BASETEN_API_KEY=... META_API_KEY=... uvicorn landfall.app:app --port 8000

The browser talks to one Session per tab. A turn runs on a worker thread
and everything the agent does (tool calls, stage progress, each building's
draft) goes into the session's event log, which /events streams as
server-sent events.
"""

from __future__ import annotations

import io
import functools
import json
import os
import threading
import uuid
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .pipeline import AreaSpec
from .session import Session, area_geojson
from .storms import STORMS
from .translate import LANGS

ROOT = Path(os.environ.get("LANDFALL_DATA", "data"))


def _seed(root: Path, demo: Path) -> None:
    """Copy the finished demo areas into the data dir if they are missing.

    A persistent disk mounted over the data dir hides what the image put
    there, so on first start it is empty; later starts find them already."""
    import shutil

    if not demo.is_dir() or demo.resolve() == root.resolve():
        return
    root.mkdir(parents=True, exist_ok=True)
    # tiles/ holds one folder per tile layer; a layer added later still needs copying
    for src in [*demo.iterdir(), *((demo / "tiles").iterdir() if (demo / "tiles").is_dir() else [])]:
        dst = root / src.relative_to(demo)
        if not dst.exists():
            (shutil.copytree if src.is_dir() else shutil.copy2)(src, dst)
        elif src.is_dir() and src.name != "tiles" and (src / "assessments.json").exists():
            # a demo area already copied: add files shipped since (plain.json, say)
            for f in src.iterdir():
                if f.is_file() and not (dst / f.name).exists():
                    shutil.copy2(f, dst / f.name)


try:
    _seed(ROOT, Path(__file__).resolve().parents[1] / "demo")
except OSError as e:  # serve what is there rather than not start
    print(f"could not copy the demo areas into {ROOT}: {e}", flush=True)
# Hosted publicly, new assessments need this code (they spend API credits);
# unset, anyone running the app can assess anything.
ACCESS_CODE = os.environ.get("LANDFALL_ACCESS_CODE", "")
MAX_BUILDINGS = int(os.environ.get("LANDFALL_MAX_BUILDINGS", "150"))
TURNS_PER_HOUR = int(os.environ.get("LANDFALL_TURNS_PER_HOUR", "0"))  # per visitor; 0 = no limit
STATIC = Path(__file__).resolve().parent.parent / "app"

app = FastAPI(title="Landfall")


class Live:
    """A session plus its event log. Events are kept so a reconnecting
    browser can replay from where it left off."""

    def __init__(self, area: str | None):
        self.session = Session(ROOT, area)
        self.events: list[dict] = []
        self.cond = threading.Condition()
        self.busy = False
        self.session.on_event = self.emit

    def emit(self, kind: str, **fields) -> None:
        with self.cond:
            self.events.append({"kind": kind, **fields})
            self.cond.notify_all()


LIVE: dict[str, Live] = {}


class NewSession(BaseModel):
    area: str | None = None


class Turn(BaseModel):
    text: str
    box: list[float] | None = None
    storm: str | None = None
    access_code: str | None = None
    upload: str | None = None  # id from /api/uploads: photos to assess instead of NOAA or Maxar
    lang: str | None = None  # "es" or "ht": the agent answers in Spanish or Haitian Creole
    event: dict | None = None  # with an upload, a disaster not in the catalog: {kind, date, name}


RECENT: dict[str, list[float]] = {}


def _rate_limit(request: Request) -> None:
    """Questions and voice clips cost model calls; cap each visitor."""
    if not TURNS_PER_HOUR:
        return
    import time

    who = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    now = time.time()
    hits = [t for t in RECENT.get(who, []) if now - t < 3600]
    if len(hits) >= TURNS_PER_HOUR:
        raise HTTPException(429, "That's the limit of questions for this hour on the public copy. Try again later.")
    RECENT[who] = [*hits, now]


def _code_ok(code: str | None) -> bool:
    import hmac

    return not ACCESS_CODE or hmac.compare_digest((code or "").encode(), ACCESS_CODE.encode())

def _area_dir(area: str) -> Path:
    d = (ROOT / area).resolve()
    if ROOT.resolve() not in d.parents or not (d / "buildings.geojson").exists():
        raise HTTPException(404, f"no area {area}")
    return d


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
def config() -> dict:
    missing = _missing_for_assessing()
    return {
        "access_code_required": bool(ACCESS_CODE),
        "max_buildings": MAX_BUILDINGS,
        "can_assess": not missing,
        "missing": missing,
        "meta_api": _meta_api_status(),
        "meta_models": _meta_models,
        "host": _host(),
    }


@functools.lru_cache(maxsize=1)
def _host() -> dict:
    """CPU share and memory this container gets, and whether the fast mask
    decoder loaded: what decides how long a new box takes."""
    from . import sam

    cpus = mem = None
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        cpus = round(int(quota) / int(period), 2) if quota != "max" else os.cpu_count()
    except (OSError, ValueError):
        cpus = os.cpu_count()
    try:
        raw = Path("/sys/fs/cgroup/memory.max").read_text().strip()
        mem = None if raw == "max" else int(raw) // 2**20
    except (OSError, ValueError):
        pass
    return {"cpus": cpus, "memory_mb": mem, "fast_masks": bool(sam._decoder()), "data_on_disk": os.path.ismount(ROOT)}


_meta_checked: list = [0.0, None]
_meta_models: list[str] = []


def _meta_api_status() -> str:
    """Whether this server's Meta API key is accepted (status only, checked at most every 10 minutes)."""
    import time

    import requests

    if time.time() - _meta_checked[0] > 600:
        try:
            r = requests.get("https://api.meta.ai/v1/models", headers={"Authorization": f"Bearer {os.environ.get('META_API_KEY', '')}"}, timeout=15)
            _meta_checked[1] = "ok" if r.ok else f"HTTP {r.status_code}"
            # which models this key can use (ids only): a model missing here answers 404
            _meta_models[:] = sorted(m.get("id", "") for m in r.json().get("data", [])) if r.ok else []
        except Exception as e:
            _meta_checked[1] = type(e).__name__
        _meta_checked[0] = time.time()
    return _meta_checked[1]


_probe_at: list = [0.0, None]


@app.get("/api/meta-probe")
def meta_probe() -> dict:
    """How Meta's API answers this server, per endpoint (status codes and error codes only, at most every 2 minutes)."""
    import time

    import requests

    if _probe_at[1] is not None and time.time() - _probe_at[0] < 120:
        return _probe_at[1]
    key = os.environ.get("META_API_KEY", "")
    h = {"Authorization": f"Bearer {key}"}
    hi = [{"role": "user", "content": "Say ok."}]
    tries = [
        ("models", "get", "https://api.meta.ai/v1/models", None),
        *[("chat spark", "post", "https://api.meta.ai/v1/chat/completions", {"model": "muse-spark-1.3", "messages": hi, "max_tokens": 5})] * 3,
        ("chat wonder", "post", "https://api.meta.ai/v1/chat/completions", {"model": "muse-wonder-1.0", "messages": hi, "max_tokens": 5}),
        *[("responses spark", "post", "https://api.meta.ai/v1/responses", {"model": "muse-spark-1.3", "input": "Say ok."})] * 2,
        ("responses spark+search", "post", "https://api.meta.ai/v1/responses", {"model": "muse-spark-1.3", "input": "Say ok.", "tools": [{"type": "web_search"}]}),
    ]
    out = []
    for name, method, url, body in tries:
        try:
            r = requests.request(method, url, headers=h, json=body, timeout=(5, 60))
            try:
                j = r.json()
            except ValueError:
                j = {}
            err = j.get("error") if isinstance(j, dict) else None
            out.append({
                "call": name,
                "status": r.status_code,
                "error": (err or {}).get("code") if isinstance(err, dict) else (str(err)[:80] if err else None),
                "models": len(j.get("data", [])) if name == "models" and isinstance(j, dict) else None,
                "route": r.headers.get("x-route"),
            })
        except Exception as e:
            out.append({"call": name, "status": type(e).__name__})
    import hashlib

    # a fingerprint to tell two copies of the key apart without showing it
    key_id = {
        "sha256_prefix": hashlib.sha256(key.encode()).hexdigest()[:8],
        "stripped_sha256_prefix": hashlib.sha256(key.strip().encode()).hexdigest()[:8],
        "length": len(key),
    }
    import socket

    import urllib3

    # which of Meta's front doors this server reaches, and whether another one answers differently
    try:
        doors = sorted({a[4][0] for a in socket.getaddrinfo("api.meta.ai", 443, socket.AF_INET)})
    except Exception as e:
        doors = [type(e).__name__]
    pinned = []
    for ip in dict.fromkeys(["157.240.254.12", *doors, *os.environ.get("META_PROBE_IPS", "").split()]):
        if not ip[:1].isdigit():
            continue
        try:
            pool = urllib3.HTTPSConnectionPool(ip, 443, server_hostname="api.meta.ai", assert_hostname="api.meta.ai", timeout=urllib3.Timeout(connect=5, read=30))
            r = pool.request("GET", "/v1/models", headers={"Host": "api.meta.ai", **h})
            pinned.append({"ip": ip, "status": r.status, "models": len(json.loads(r.data or b"{}").get("data", []))})
        except Exception as e:
            pinned.append({"ip": ip, "status": type(e).__name__})
    try:
        # where the API sees this server calling from
        w = requests.get("https://ipinfo.io/json", timeout=10).json()
        seen_from = {k: w.get(k) for k in ("city", "region", "country", "org")}
    except Exception as e:
        seen_from = type(e).__name__
    _probe_at[:] = [time.time(), {"at": time.strftime("%H:%M:%S UTC", time.gmtime()), "key": key_id, "from": seen_from, "doors": doors, "pinned": pinned, "calls": out}]
    return _probe_at[1]


@functools.lru_cache(maxsize=1)
def _missing_for_libraries() -> tuple[str, ...]:
    out = []
    for mod in ("rasterio.warp", "shapely", "meta_sam_parser"):
        try:
            __import__(mod)
        except Exception as e:
            out.append(f"{mod} ({type(e).__name__})")
    return tuple(out)


def _missing_for_assessing() -> list[str]:
    """What a new assessment would fail on: libraries and API keys (names only, never values)."""
    keys = [k for k in ("META_API_KEY", "BASETEN_API_KEY") if not os.environ.get(k)]
    return [*_missing_for_libraries(), *keys]


@app.get("/api/storms")
def storms() -> list[dict]:
    return [
        {"key": s.key, "name": s.name, "landfall": s.landfall, "where": s.where, "imagery": s.provider, "kind": s.kind, "view": list(s.view), "tiles": _storm_tiles().get(s.key, [])}
        for s in STORMS.values()
    ]


@functools.lru_cache(maxsize=1)
def _storm_tiles() -> dict[str, list[dict]]:
    """After-disaster tile layers shipped for satellite disasters (made by
    scripts/maxar_tiles.py), so the map shows photos as soon as one is
    picked. Read from the shipped copy, which a persistent disk can't hide;
    the tiles themselves are served from the data dir."""
    out: dict[str, list[dict]] = {}
    for f in sorted((Path(__file__).resolve().parents[1] / "demo" / "tiles").glob("*/layer.json")):
        t = json.loads(f.read_text())
        out.setdefault(t.pop("storm"), []).append(t)
    # wide, coarse layers first so sharper ones draw on top
    return {k: sorted(v, key=lambda t: t["max_native_zoom"]) for k, v in out.items()}


@app.get("/api/storms/{key}/flights")
def storm_flights(key: str) -> list[dict]:
    """Every NOAA flight after the storm, so the map shows after-storm photos
    beyond the one flight an area was drafted from."""
    if key not in STORMS or STORMS[key].provider != "noaa":
        return []
    return _flights(key)


@functools.lru_cache(maxsize=16)
def _flights(key: str) -> list[dict]:
    from .storms import flights

    try:
        return [{"name": f.name, "bounds": [[f.bounds[1], f.bounds[0]], [f.bounds[3], f.bounds[2]]]} for f in flights(key) if f.date >= STORMS[key].landfall]
    except Exception:
        return []


@app.get("/api/areas")
def areas() -> list[dict]:
    out = []
    for d in sorted(ROOT.iterdir()) if ROOT.exists() else []:
        if not (d / "buildings.geojson").exists():
            continue
        spec = AreaSpec.load(ROOT, d.name).__dict__ if (d / "spec.json").exists() else None
        if spec is None and d.name == "cedar_key":
            from .pipeline import CEDAR_KEY

            spec = CEDAR_KEY.__dict__
        label = (d / "label.txt").read_text().strip() if (d / "label.txt").exists() else None
        if label is None and spec:
            _name_later(d, spec["center"])
        row = {"name": d.name, "assessed": (d / "assessments.json").exists(), "spec": spec, "label": label}
        if spec and spec.get("imagery") == "photos":  # what the before side was, for the intro
            row["archive_before"] = (d / "archive_before.json").exists()
            row["uploaded_before"] = (d / "upload_before.jpg").exists()
        out.append(row)
    return out


_naming: set[str] = set()


def _name_later(d: Path, center) -> None:
    """Name an area after its place ("Cedar Key, Florida") in the background;
    the menu shows the folder name until then."""
    if d.name in _naming:
        return
    _naming.add(d.name)

    def run() -> None:
        from .places import name_of

        try:
            label = name_of(round(center[1], 5), round(center[0], 5))
            if label:
                (d / "label.txt").write_text(label)
        except Exception:
            _naming.discard(d.name)  # try again on a later visit

    threading.Thread(target=run, daemon=True).start()


@app.get("/api/places")
def places(q: str) -> list[dict]:
    """Places matching a search, each with the disasters that have photos of it."""
    from .places import disasters_at, search

    q = q.strip()[:120]
    if not q:
        return []
    try:
        rows = search(q)[:5]
        # a full address the map doesn't know exactly (a unit number, a new
        # street): drop its first part until something matches, down to the town
        parts = [p.strip() for p in q.split(",") if p.strip()]
        while not rows and len(parts) > 1:
            parts = parts[1:]
            rows = search(", ".join(parts))[:5]
    except Exception:
        raise HTTPException(502, "place search is not answering; try again in a moment")
    for r in rows:
        r["disasters"] = disasters_at(r["lon"], r["lat"], r["bbox"], ROOT / "cache")
    return rows


@app.get("/api/wayback")
def wayback(lon: float, lat: float, date: str) -> dict:
    """The newest worldwide archive photo of a spot from before a date, as
    map tiles: what an uploader places their photo on when Landfall has no
    disaster there."""
    from .wayback import ATTRIBUTION, before

    try:
        photo = before(lon, lat, date[:10], ROOT / "cache")
    except (OSError, ValueError):
        raise HTTPException(502, "the photo archive is not answering; try again in a moment") from None
    if photo is None:
        raise HTTPException(404, "no archive photo of this spot from before that date")
    return {"tiles": photo.tiles, "captured": photo.captured, "source": photo.source, "attribution": f"{ATTRIBUTION}; photo {photo.captured}"}


def _warm_catalogs() -> None:
    """Fetch every disaster's flight and acquisition lists once, so the first
    place search doesn't wait on them."""
    from .places import disasters_at

    try:
        disasters_at(0.0, 0.0, None, ROOT / "cache")
    except Exception:
        pass


threading.Thread(target=_warm_catalogs, daemon=True).start()


@app.get("/api/areas/{area}/buildings")
def buildings(area: str) -> JSONResponse:
    _area_dir(area)
    return JSONResponse(area_geojson(ROOT, area))


@app.get("/api/areas/{area}/buildings/{bid}")
def building(area: str, bid: str) -> dict:
    d = _area_dir(area)
    drafts = {a["id"]: a for a in json.loads((d / "assessments.json").read_text())} if (d / "assessments.json").exists() else {}
    if bid not in drafts:
        raise HTTPException(404, f"no draft for {bid}")
    from .packet import after_source, before_source, facts

    f = facts(d, bid)
    rec = f["parcel"]
    plain = json.loads((d / "plain.json").read_text()) if (d / "plain.json").exists() else {}
    return {
        **drafts[bid],
        "plain": plain.get(bid),
        "photos": {"before": before_source(f["photos"]), "after": after_source(f["photos"])},
        "cost": f["cost"],
        "need": f["need"],
        "record": {k: rec.get(k) for k in ("year_built", "flood_zone", "bfe_ft", "basis", "land_use")} if rec else None,
    }


@app.get("/api/areas/{area}/pair/{bid}.jpg")
def pair_jpg(area: str, bid: str) -> Response:
    """The card's photo pair: a JPEG a tenth the size of the PNG, drawn once
    and kept beside the area, so a click shows it at once."""
    d = _area_dir(area)
    path = d / "cards" / f"{bid}.jpg"
    if not path.exists():
        from PIL import Image

        im = Image.open(io.BytesIO(_pair_png(area, bid))).convert("RGB")
        path.parent.mkdir(exist_ok=True)
        tmp = path.with_suffix(".tmp")
        im.save(tmp, "JPEG", quality=85)
        tmp.replace(path)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


@app.get("/api/areas/{area}/pair/{bid}.png")
def pair(area: str, bid: str) -> Response:
    """Before | after with the outline, the same picture the agent saw."""
    return Response(_pair_png(area, bid), media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/areas/{area}/after")
def after_overlay(area: str) -> dict:
    """Where the after-storm mosaic sits on the map, for storms without NOAA
    tiles (the browser can't read Maxar's GeoTIFFs directly)."""
    from .geo import pixel_to_lonlat

    d = _area_dir(area)
    if (d / "tiles.json").exists():  # shipped demo areas: map tiles cut from the same imagery
        return {"tiles": json.loads((d / "tiles.json").read_text())}
    if shared := _shared_tiles(d):  # a new box inside a shipped area's tiles: same look
        return {"tiles": shared}
    if not (d / "post.png").exists() and not (d / "post_size.json").exists():
        raise HTTPException(404, "no after-storm mosaic yet")
    from PIL import Image

    x0, y0 = json.loads((d / "alignment.json").read_text())["summary"]["mosaic_origin"]
    # shipped demo areas keep only a preview, with the full mosaic's size beside it
    w, h = json.loads((d / "post_size.json").read_text()) if not (d / "post.png").exists() else Image.open(d / "post.png").size
    l, t, r, b = _photo_extent(d) or (0, 0, w, h)  # an uploaded photo: just the photo, not the black around it
    w_lon, n_lat = pixel_to_lonlat(x0 + l, y0 + t, 19)
    e_lon, s_lat = pixel_to_lonlat(x0 + r, y0 + b, 19)
    photos = json.loads((d / "spec.json").read_text()).get("imagery") == "photos" if (d / "spec.json").exists() else False
    out = {
        # a photo turned to north-up has blank corners: a see-through image there
        "url": f"/api/areas/{area}/after.{'webp' if (d / 'upload_after_ok.png').exists() else 'jpg'}",
        "bounds": [[s_lat, w_lon], [n_lat, e_lon]],
        "attribution": "Uploaded after photo" if photos else "Maxar Open Data (CC BY-NC 4.0)",
    }
    if photos:
        # around the uploaded photo: the archive's photo of the place, so it sits in a
        # satellite view rather than a street map (labelled as from before)
        from .wayback import ATTRIBUTION, before

        spec = json.loads((d / "spec.json").read_text())
        try:
            p = before(spec["center"][0], spec["center"][1], spec["landfall"], ROOT / "cache")
        except (OSError, ValueError):
            p = None
        if p:
            out["around"] = {"tiles": p.tiles, "captured": p.captured, "attribution": f"around it: {ATTRIBUTION}, photo {p.captured} (before)"}
    return out


def _photo_extent(d: Path) -> tuple[int, int, int, int] | None:
    """For an uploaded photo's area, the part of the after mosaic the photo covers."""
    if not (d / "spec.json").exists() or json.loads((d / "spec.json").read_text()).get("imagery") != "photos" or not (d / "post.png").exists():
        return None
    from PIL import Image

    return Image.open(d / "post.png").convert("L").point(lambda v: 255 if v > 0 else 0).getbbox()


@app.get("/api/tiles/{name}/{z}/{x}/{y}.jpg")
def tile(name: str, z: int, x: int, y: int) -> Response:
    """After-storm satellite tiles made by scripts/maxar_tiles.py; edge tiles are PNGs."""
    if not name.replace("_", "").isalnum():
        raise HTTPException(404)
    base = ROOT / "tiles" / name / str(z) / str(x)
    for ext, kind in (("jpg", "image/jpeg"), ("png", "image/png")):
        if (base / f"{y}.{ext}").exists():
            return FileResponse(base / f"{y}.{ext}", media_type=kind, headers={"Cache-Control": "max-age=86400"})
    raise HTTPException(404)


def _shared_tiles(d: Path) -> list[dict]:
    """Tile layers from finished areas of the same storm that overlap this area."""
    if not (d / "spec.json").exists():
        return []
    spec = json.loads((d / "spec.json").read_text())
    w, s, e, n = spec["bbox"]
    out: dict[str, dict] = {}
    for other in sorted(ROOT.glob("*/tiles.json")):
        o = other.parent / "spec.json"
        if not o.exists() or json.loads(o.read_text()).get("storm") != spec.get("storm"):
            continue
        layers = json.loads(other.read_text())
        for t in [layers] if isinstance(layers, dict) else layers:
            (ts, tw), (tn, te) = t["bounds"]
            if tw < e and te > w and ts < n and tn > s:
                out.setdefault(t["url"], t)
    # wide, coarse layers first so sharper ones draw on top
    return sorted(out.values(), key=lambda t: t["max_native_zoom"])


@app.get("/api/areas/{area}/before")
def area_before(area: str) -> dict:
    """The before photo the drafts were made from, for the map's before/after
    slider: the same pixel grid as the after mosaic, with its source and date."""
    from .geo import pixel_to_lonlat

    d = _area_dir(area)
    spec = json.loads((d / "spec.json").read_text()) if (d / "spec.json").exists() else {}
    if spec.get("imagery") == "photos" or not ((d / "pre.png").exists() or (d / "pre_preview.jpg").exists()):
        raise HTTPException(404, "no before photo for this area")
    from PIL import Image

    s = json.loads((d / "alignment.json").read_text())["summary"]
    x0, y0 = s["mosaic_origin"]
    w, h = json.loads((d / "pre_size.json").read_text()) if not (d / "pre.png").exists() else Image.open(d / "pre.png").size
    w_lon, n_lat = pixel_to_lonlat(x0, y0, s.get("zoom", 19))
    e_lon, s_lat = pixel_to_lonlat(x0 + w, y0 + h, s.get("zoom", 19))
    source = s.get("pre_source") or (f"USDA NAIP {s['naip_date']}" if s.get("naip_date") else "archive photo")
    return {
        "url": f"/api/areas/{area}/before.jpg",
        "bounds": [[s_lat, w_lon], [n_lat, e_lon]],
        "date": s.get("pre_date") or s.get("naip_date"),
        "source": source.split(" (")[0],
    }


@app.get("/api/areas/{area}/before.jpg")
def before_jpg(area: str) -> Response:
    from PIL import Image

    d = _area_dir(area)
    out = d / "pre_preview.jpg"
    if (d / "pre.png").exists() and (not out.exists() or out.stat().st_mtime < (d / "pre.png").stat().st_mtime):
        im = Image.open(d / "pre.png").convert("RGB")
        im.thumbnail((4096, 4096))
        im.save(out, quality=85)
    if not out.exists():
        raise HTTPException(404, "no before photo for this area")
    return FileResponse(out, media_type="image/jpeg")


@app.get("/api/areas/{area}/after.webp")
def after_webp(area: str) -> Response:
    """An uploaded photo turned to north-up, see-through where it has no picture."""
    from PIL import Image, ImageFilter

    d = _area_dir(area)
    out = d / "post_preview.webp"
    if not (d / "post.png").exists():
        raise HTTPException(404, "no after photo yet")
    if not out.exists() or out.stat().st_mtime < (d / "post.png").stat().st_mtime:
        im = Image.open(d / "post.png").convert("RGB")
        # pulled in a little from the photo's edge, where resampling left a dark fringe
        alpha = im.convert("L").point(lambda v: 255 if v > 0 else 0).filter(ImageFilter.MinFilter(9))
        im.putalpha(alpha)
        if box := _photo_extent(d):
            im = im.crop(box)
        im.thumbnail((4096, 4096))
        im.save(out, quality=85)
    return FileResponse(out, media_type="image/webp")


@app.get("/api/areas/{area}/after.jpg")
def after_jpg(area: str) -> Response:
    from PIL import Image

    d = _area_dir(area)
    out = d / "post_preview.jpg"
    if (d / "post.png").exists() and (not out.exists() or out.stat().st_mtime < (d / "post.png").stat().st_mtime):
        im = Image.open(d / "post.png").convert("RGB")
        if box := _photo_extent(d):
            im = im.crop(box)
        im.thumbnail((4096, 4096))
        im.save(out, quality=85)
    return FileResponse(out, media_type="image/jpeg")


@app.get("/api/areas/{area}/packet/{bid}", response_class=HTMLResponse)
def packet(area: str, bid: str, lang: str = "en") -> HTMLResponse:
    """Printable evidence packet for one building, in English, Spanish (es) or Haitian Creole (ht)."""
    from .packet import render
    from .translate import page

    d = _area_dir(area)
    try:
        doc = render(d, bid, _pair_png(area, bid))
    except KeyError:
        raise HTTPException(404, f"no draft for {bid}") from None
    return HTMLResponse(page(doc, lang, d, f"/api/areas/{area}/packet/{bid}") if lang != "en" else doc)


def _pair_png(area: str, bid: str) -> bytes:
    from PIL import Image

    from .agent import pair_image
    from .geo import Window, outline_to_window_pixels

    d = _area_dir(area)
    al = {r["id"]: r for r in json.loads((d / "alignment.json").read_text())["buildings"]}
    feats = {f["properties"]["id"]: f for f in json.loads((d / "buildings.geojson").read_text())["features"]}
    if bid not in al:
        raise HTTPException(404, bid)
    w = Window(*al[bid]["window"])
    pts = outline_to_window_pixels([tuple(p) for p in feats[bid]["geometry"]["coordinates"][0][:-1]], 19, w)
    pre = np.asarray(Image.open(d / "crops" / f"{bid}_pre.png").convert("RGB"))
    post = np.asarray(Image.open(d / "crops" / f"{bid}_post.png").convert("RGB"))
    import base64

    url = pair_image(pre, post, pts, scale=3)
    return base64.b64decode(url.split(",", 1)[1])


@app.post("/api/sessions")
def new_session(body: NewSession) -> dict:
    sid = uuid.uuid4().hex[:12]
    if body.area:
        _area_dir(body.area)
    LIVE[sid] = Live(body.area)
    return {"id": sid, "area": body.area}


@app.post("/api/sessions/{sid}/turn")
def turn(sid: str, body: Turn, request: Request) -> dict:
    live = LIVE.get(sid)
    if live is None:
        raise HTTPException(404, "no such session")
    if live.busy:
        raise HTTPException(409, "the agent is still working on the last message")
    allowed = _code_ok(body.access_code)
    if body.box and not allowed:
        raise HTTPException(403, "Assessing a new area needs the access code. You can still ask about the finished areas.")
    _rate_limit(request)
    event = _event(body.event) if body.upload and body.event else None
    live.session.may_run_pipeline = allowed
    live.session.max_buildings = MAX_BUILDINGS
    live.busy = True
    live.emit("user", text=body.text, box=body.box)

    def work() -> None:
        try:
            box = tuple(body.box) if body.box else None
            ask = body.text
            if body.lang in LANGS:
                ask += f"\n\n[Answer in {LANGS[body.lang]}, in everyday words. Keep building ids, numbers, phone numbers, web addresses and the names of agencies as they are.]"
            text = live.session.turn(ask, box=box, storm=body.storm, upload=body.upload if box else None, event=event)
            live.emit("message", text=text, area=live.session.area)
        except Exception as e:
            live.emit("error", text=f"{type(e).__name__}: {e}"[:500])
        finally:
            live.busy = False
            live.emit("idle")

    threading.Thread(target=work, daemon=True).start()
    return {"ok": True}


@app.post("/api/uploads")
async def upload(request: Request, side: str = "after", id: str | None = None, check: bool = True) -> dict:
    """One photo of the land: the after photo starts an upload, a before
    photo joins it by id. The body is the image file itself. Muse Wonder
    looks at the after photo first (`check`), so a photo that can't be used
    is caught in seconds instead of after a minute of searching."""
    import asyncio

    from .photos import save_upload
    from .wonder import look

    if side not in ("after", "before"):
        raise HTTPException(400, "side is after or before")
    if side == "before" and not (id and id.isalnum() and (ROOT / "uploads" / id / "after.jpg").exists()):
        raise HTTPException(400, "upload the after photo first")
    _rate_limit(request)
    raw = await request.body()
    if not raw or len(raw) > 30_000_000:
        raise HTTPException(400, "send one image under 30 MB")
    try:
        saved = save_upload(ROOT, id if side == "before" else uuid.uuid4().hex[:12], side, raw)
    except Exception as e:  # not an image PIL can read
        raise HTTPException(400, f"that file isn't an image I can read ({type(e).__name__})") from e
    if side == "after" and check:
        seen = await asyncio.to_thread(look, (ROOT / "uploads" / saved["id"] / "after.jpg").read_bytes())
        if seen:
            saved["check"] = seen
    return saved


def _event(raw: dict) -> dict:
    """A disaster the catalog doesn't have, as the uploader described it."""
    from datetime import date

    kind = str(raw.get("kind", ""))
    if kind not in ("hurricane", "flood", "wildfire", "earthquake"):
        raise HTTPException(400, "the disaster's kind is hurricane, flood, wildfire or earthquake")
    # the day if the uploader knows it, else just the year: then the before
    # photo has to be from before that year began
    given = str(raw.get("date", "")).strip()
    exact = len(given) != 4
    try:
        when = date.fromisoformat(given) if exact else date(int(given), 1, 1)
    except ValueError:
        raise HTTPException(400, "give the year the disaster happened, or its date") from None
    if when > date.today():
        raise HTTPException(400, "the date the disaster happened can't be in the future")
    name = " ".join(str(raw.get("name") or "").split())[:80]
    return {"kind": kind, "date": when.isoformat(), "exact": exact, "name": name}


@app.get("/api/sessions/{sid}/events")
def events(sid: str, request: Request, since: int = 0) -> StreamingResponse:
    live = LIVE.get(sid)
    if live is None:
        raise HTTPException(404, "no such session")
    start = int(request.headers.get("last-event-id", since - 1)) + 1 if request.headers.get("last-event-id") else since

    def stream():
        i = start
        while True:
            with live.cond:
                if i >= len(live.events):
                    live.cond.wait(timeout=15)
                batch = live.events[i:]
            if not batch:
                yield ": keepalive\n\n"
                continue
            for ev in batch:
                yield f"id: {i}\ndata: {json.dumps(ev, default=str)}\n\n"
                i += 1

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/transcribe")
async def transcribe(request: Request) -> dict:
    from .voice import transcribe as run

    _rate_limit(request)
    wav = await request.body()
    if not wav or len(wav) > 10_000_000:
        raise HTTPException(400, "send 16-bit mono WAV, under 10 MB")
    rate = int(request.headers.get("x-sample-rate", "16000"))
    return {"text": run(wav, rate)}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _bytes(img) -> bytes:  # pragma: no cover - helper for debugging
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
