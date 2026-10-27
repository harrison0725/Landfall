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
    return {"access_code_required": bool(ACCESS_CODE), "max_buildings": MAX_BUILDINGS}


@app.get("/api/storms")
def storms() -> list[dict]:
    return [{"key": s.key, "name": s.name, "landfall": s.landfall, "where": s.where, "imagery": s.provider} for s in STORMS.values()]


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
    if not (d / "post.png").exists() and not (d / "post_size.json").exists():
        raise HTTPException(404, "no after-storm mosaic yet")
    from PIL import Image

    x0, y0 = json.loads((d / "alignment.json").read_text())["summary"]["mosaic_origin"]
    # shipped demo areas keep only a preview, with the full mosaic's size beside it
    w, h = json.loads((d / "post_size.json").read_text()) if not (d / "post.png").exists() else Image.open(d / "post.png").size
    w_lon, n_lat = pixel_to_lonlat(x0, y0, 19)
    e_lon, s_lat = pixel_to_lonlat(x0 + w, y0 + h, 19)
    return {"url": f"/api/areas/{area}/after.jpg", "bounds": [[s_lat, w_lon], [n_lat, e_lon]]}


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
