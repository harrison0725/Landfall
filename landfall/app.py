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

ROOT = Path(os.environ.get("LANDFALL_DATA", "data"))


def _seed(root: Path, demo: Path) -> None:
    """Copy the finished demo areas into the data dir if they are missing.

    A persistent disk mounted over the data dir hides what the image put
    there, so on first start it is empty; later starts find them already."""
    import shutil

    if not demo.is_dir() or demo.resolve() == root.resolve():
        return
    root.mkdir(parents=True, exist_ok=True)
    for src in demo.iterdir():
        dst = root / src.name
        if not dst.exists():
            (shutil.copytree if src.is_dir() else shutil.copy2)(src, dst)


_seed(ROOT, Path(__file__).resolve().parents[1] / "demo")
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
    return {"cpus": cpus, "memory_mb": mem, "fast_masks": bool(sam._decoder())}


_meta_checked: list = [0.0, None]


def _meta_api_status() -> str:
    """Whether this server's Meta API key is accepted (status only, checked at most every 10 minutes)."""
    import time

    import requests

    if time.time() - _meta_checked[0] > 600:
        try:
            r = requests.get("https://api.meta.ai/v1/models", headers={"Authorization": f"Bearer {os.environ.get('META_API_KEY', '')}"}, timeout=15)
            _meta_checked[1] = "ok" if r.ok else f"HTTP {r.status_code}"
        except Exception as e:
            _meta_checked[1] = type(e).__name__
        _meta_checked[0] = time.time()
    return _meta_checked[1]


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
    return [{"key": s.key, "name": s.name, "landfall": s.landfall, "where": s.where, "imagery": s.provider} for s in STORMS.values()]


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
        out.append({"name": d.name, "assessed": (d / "assessments.json").exists(), "spec": spec})
    return out


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
    return {
        **drafts[bid],
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
    w_lon, n_lat = pixel_to_lonlat(x0, y0, 19)
    e_lon, s_lat = pixel_to_lonlat(x0 + w, y0 + h, 19)
    return {"url": f"/api/areas/{area}/after.jpg", "bounds": [[s_lat, w_lon], [n_lat, e_lon]]}


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


@app.get("/api/areas/{area}/after.jpg")
def after_jpg(area: str) -> Response:
    from PIL import Image

    d = _area_dir(area)
    out = d / "post_preview.jpg"
    if (d / "post.png").exists() and (not out.exists() or out.stat().st_mtime < (d / "post.png").stat().st_mtime):
        im = Image.open(d / "post.png").convert("RGB")
        im.thumbnail((4096, 4096))
        im.save(out, quality=85)
    return FileResponse(out, media_type="image/jpeg")


@app.get("/api/areas/{area}/packet/{bid}", response_class=HTMLResponse)
def packet(area: str, bid: str) -> HTMLResponse:
    """Printable evidence packet for one building."""
    from .packet import render

    d = _area_dir(area)
    try:
        return HTMLResponse(render(d, bid, _pair_png(area, bid)))
    except KeyError:
        raise HTTPException(404, f"no draft for {bid}") from None


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
    live.session.may_run_pipeline = allowed
    live.session.max_buildings = MAX_BUILDINGS
    live.busy = True
    live.emit("user", text=body.text, box=body.box)

    def work() -> None:
        try:
            box = tuple(body.box) if body.box else None
            text = live.session.turn(body.text, box=box, storm=body.storm)
            live.emit("message", text=text, area=live.session.area)
        except Exception as e:
            live.emit("error", text=f"{type(e).__name__}: {e}"[:500])
        finally:
            live.busy = False
            live.emit("idle")

    threading.Thread(target=work, daemon=True).start()
    return {"ok": True}


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
