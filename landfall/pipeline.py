"""The per-area pipeline as library stages, so the scripts, the area agent
and the web app all run the same code.

    spec = AreaSpec.for_box("horseshoe", (-83.292, 29.437, -83.284, 29.444), "helene")
    prepare(spec, root)          # imagery, outlines, alignment
    segment(spec, root)          # SAM 3.1 structure masks -> structure loss
    flood(spec, root)            # surveyed water surface minus lidar ground
    debris(spec, root)           # SAM debris piles and blue tarps
    assess(spec, root)           # per-building agent drafts

Each stage reads and writes files in root/<area>/ and reports progress
through `log(event, **fields)`, which the app streams to the browser. Every
stage is safe to rerun: SAM lines and ground elevations are cached.
"""

from __future__ import annotations

import collections
import json
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

import numpy as np

from .geo import Window, building_window, lonlat_to_pixel, meters_per_pixel, outline_to_window_pixels

Log = Callable[..., None]


def _quiet(event: str, **fields) -> None:
    pass


ZOOM = 19
MARGIN_M = 8.0
MAX_SHIFT = 28  # ~7 m: roofs on pilings lean differently in each flight
TEMPLATE_PAD = 8  # px of ground kept around the outline's box for matching
SAM_SCALE = 4
POST_PAD = 16  # px (~4 m) the post-storm house may sit from where alignment put it
STRUCTURE_PROMPTS = ["roof", "house", "building"]
YARD_PAD = 60  # px (~16 m) of yard around the building window for debris
YARD_SCALE = 2
RING_PX = 30  # ~8 m around the outline counts as this building's yard
DEBRIS_PROMPTS = ["pile of debris", "trash pile"]
TARP_PROMPTS = ["blue tarp"]


@dataclass
class AreaSpec:
    name: str
    center: tuple[float, float]
    bbox: tuple[float, float, float, float]  # west, south, east, north: where to take buildings from
    storm: str  # key in storms.STORMS
    noaa_flight: str
    landfall: str  # YYYY-MM-DD
    stn_event: str
    n_buildings: int = 150
    min_area_m2: float = 40.0
    hwm_radius_km: float = 5.0
    gauges: list[dict] = field(default_factory=list)
    # Maxar satellite imagery, for storms outside NOAA's coverage: visual
    # COGs in the order they fill the mosaic, and labels for the record
    imagery: str = "noaa"
    post_hrefs: list[str] = field(default_factory=list)
    pre_hrefs: list[str] = field(default_factory=list)
    post_label: str = ""
    pre_label: str = ""
    pre_date: str = ""

    @property
    def post_source(self) -> str:
        return self.post_label or f"NOAA emergency response flight {self.noaa_flight}"

    @classmethod
    def for_box(
        cls, name: str, bbox: tuple[float, float, float, float], storm: str, n_buildings: int = 60, cache: Path = Path("data") / "cache"
    ) -> AreaSpec:
        """Look up the flight and gauges for a box drawn on the map."""
        from .storms import STORMS, find_flight, nearest_gauges

        s = STORMS[storm]
        center = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
        if s.provider == "maxar":
            return cls._maxar(name, bbox, center, s, n_buildings, cache)
        f = find_flight(storm, bbox)
        if f is None:
            raise LookupError(f"NOAA has no {s.name} imagery over this box")
        gauges = nearest_gauges(*center)
        return cls(name, center, tuple(bbox), storm, f.name, s.landfall, s.stn_event, n_buildings, gauges=gauges)

    @classmethod
    def _maxar(cls, name, bbox, center, s, n_buildings, cache) -> AreaSpec:
        from .maxar import passes, pick

        found = passes(s.maxar_event, tuple(bbox), cache)
        after, before = pick(found, s.landfall, True), pick(found, s.landfall, False)
        if not after:
            raise LookupError(f"Maxar has no clear {s.name} imagery after {s.landfall} over this box")
        if not before:
            raise LookupError(
                f"Maxar has {s.name} imagery after the storm here but none from before it, so there is nothing to compare against. "
                "Try a nearby box; before-storm coverage is patchy outside the cities."
            )
        return cls(
            name,
            center,
            tuple(bbox),
            s.key,
            "",
            s.landfall,
            "",
            n_buildings,
            imagery="maxar",
            post_hrefs=[h for p in after[:3] for h in p.hrefs],
            pre_hrefs=[h for p in before[:3] for h in p.hrefs],
            post_label=after[0].label,
            pre_label=before[0].label,
            pre_date=before[0].date,
        )

    def save(self, root: Path) -> None:
        d = root / self.name
        d.mkdir(parents=True, exist_ok=True)
        (d / "spec.json").write_text(json.dumps(asdict(self), indent=1))

    @classmethod
    def load(cls, root: Path, name: str) -> AreaSpec:
        raw = json.loads((root / name / "spec.json").read_text())
        raw["center"] = tuple(raw["center"])
        raw["bbox"] = tuple(raw["bbox"])
        return cls(**raw)


CEDAR_KEY = AreaSpec(
    # Hurricane Helene landfall 2024-09-26. Downtown Cedar Key and the Dock
    # Street waterfront, which took the surge.
    name="cedar_key",
    center=(-83.0355, 29.1375),
    bbox=(-83.06, 29.12, -83.01, 29.16),
    storm="helene",
    noaa_flight="20240928b-rgb",
    landfall="2024-09-26",
    stn_event="2024 Helene",
    n_buildings=150,
    # NOAA CO-OPS 8727520 Cedar Key, on the Dock Street waterfront
    gauges=[dict(station="8727520", name="Cedar Key", lon=-83.0317, lat=29.1350)],
)


# ---------------------------------------------------------------- prepare


def area_m2(outline: list[tuple[float, float]]) -> float:
    lat0 = outline[0][1]
    kx = 111320 * math.cos(math.radians(lat0))
    ky = 110540
    pts = [(lon * kx, lat * ky) for lon, lat in outline]
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]))) / 2


def pick_buildings(feats: list[dict], center, n: int, min_area: float) -> list[dict]:
    cx, cy = center
    kx = math.cos(math.radians(cy))
    for f in feats:
        ring = [tuple(p) for p in f["geometry"]["coordinates"][0]]
        f["properties"]["area_m2"] = round(area_m2(ring[:-1]), 1)
    big = [f for f in feats if f["properties"]["area_m2"] >= min_area]
    big.sort(key=lambda f: ((f["properties"]["centroid"][0] - cx) * kx) ** 2 + (f["properties"]["centroid"][1] - cy) ** 2)
    chosen = big[:n]
    for i, f in enumerate(chosen):
        f["properties"]["id"] = f"b{i:03d}"
    return chosen


def prepare(spec: AreaSpec, root: Path, log: Log = _quiet) -> dict:
    """Footprints, pre/post mosaics, per-building alignment and crops."""
    from PIL import Image

    from .align import match_building, place_buildings
    from .sources import ms_buildings, naip_mosaic, naip_scenes, noaa_mosaic

    out = root / spec.name
    cache = root / "cache"
    (out / "crops").mkdir(parents=True, exist_ok=True)
    spec.save(root)

    feats = ms_buildings(spec.bbox, cache, region=None)
    chosen = pick_buildings(feats, spec.center, spec.n_buildings, spec.min_area_m2)
    if not chosen:
        raise LookupError("no building footprints in this box")
    log("buildings", found=len(feats), kept=len(chosen))
    (out / "buildings.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": chosen}))

    wins = {f["properties"]["id"]: building_window([tuple(p) for p in f["geometry"]["coordinates"][0][:-1]], ZOOM, MARGIN_M) for f in chosen}
    pad = 64 + MAX_SHIFT
    area = Window(
        min(w.x0 for w in wins.values()) - pad,
        min(w.y0 for w in wins.values()) - pad,
        max(w.x1 for w in wins.values()) + pad,
        max(w.y1 for w in wins.values()) + pad,
    )
    if spec.imagery == "maxar":
        from .maxar import mosaic

        post, post_ok = mosaic(spec.post_hrefs, area, ZOOM)
        pre, pre_ok = mosaic(spec.pre_hrefs, area, ZOOM)
        pre_date, pre_source, pre_id = spec.pre_date, spec.pre_label, spec.pre_label
    else:
        post, post_ok = noaa_mosaic(spec.noaa_flight, area, ZOOM, cache)
        scene, pre, pre_ok = _naip_fill(area, spec, naip_scenes, naip_mosaic)
        pre_date, pre_source, pre_id = scene.date, f"USDA NAIP {scene.date}", scene.id
    log("imagery", side="post", source=spec.post_source, px=[area.width, area.height], covered=round(float(post_ok.mean()), 3))
    log("imagery", side="pre", source=pre_source, px=[area.width, area.height], covered=round(float(pre_ok.mean()), 3))
    between = storms_between(pre_date, spec.landfall, spec.storm)
    if between:
        log("warning", text=f"{', '.join(between)} hit between the pre-storm photos ({pre_date}) and this storm; damage from it will look like damage from this one.")
    Image.fromarray(pre).save(out / "pre.png")
    Image.fromarray(post).save(out / "post.png")

    matches, centers, coverage = [], [], []
    for f in chosen:
        ring = f["geometry"]["coordinates"][0][:-1]
        px = np.array([lonlat_to_pixel(lon, lat, ZOOM) for lon, lat in ring]) - [area.x0, area.y0]
        bx0, by0 = np.floor(px.min(0)).astype(int) - TEMPLATE_PAD
        bx1, by1 = np.ceil(px.max(0)).astype(int) + TEMPLATE_PAD
        m = MAX_SHIFT
        ctx = (slice(by0 - m, by1 + m), slice(bx0 - m, bx1 + m))
        coverage.append(float(min(pre_ok[ctx].mean(), post_ok[ctx].mean())))
        matches.append(match_building(pre[ctx], post[ctx], (m, m, m + bx1 - bx0, m + by1 - by0), m))
        centers.append(tuple(px.mean(0)))
    places = place_buildings(matches, centers)

    results = []
    for f, pl, cover in zip(chosen, places, coverage):
        bid = f["properties"]["id"]
        w = wins[bid]
        status = pl.status if cover >= 0.99 else "unaligned"
        rec = dict(
            id=bid,
            window=[w.x0, w.y0, w.x1, w.y1],
            area_m2=f["properties"]["area_m2"],
            coverage=round(cover, 3),
            dx=pl.dx,
            dy=pl.dy,
            status=status,
            aligned=status == "matched",
            score=round(pl.own.score, 3),
            margin=round(pl.own.margin, 3),
            own_dx=pl.own.dx,
            own_dy=pl.own.dy,
            neighbor_dx=None if pl.neighbor_dx is None else round(pl.neighbor_dx, 1),
            neighbor_dy=None if pl.neighbor_dy is None else round(pl.neighbor_dy, 1),
        )
        a = pre[w.y0 - area.y0 : w.y1 - area.y0, w.x0 - area.x0 : w.x1 - area.x0]
        b = post[w.y0 - area.y0 + pl.dy : w.y1 - area.y0 + pl.dy, w.x0 - area.x0 + pl.dx : w.x1 - area.x0 + pl.dx]
        Image.fromarray(a).save(out / "crops" / f"{bid}_pre.png")
        Image.fromarray(b).save(out / "crops" / f"{bid}_post.png")
        results.append(rec)

    summary = dict(
        mosaic_origin=[area.x0, area.y0],  # global pixel of pre.png / post.png (0, 0)
        area=spec.name,
        zoom=ZOOM,
        m_per_px=round(meters_per_pixel(spec.center[1], ZOOM), 3),
        noaa_flight=spec.noaa_flight,
        naip_scene=pre_id if spec.imagery != "maxar" else None,
        naip_date=pre_date if spec.imagery != "maxar" else None,
        post_source=spec.post_source,
        pre_source=pre_source,
        pre_date=pre_date,
        storms_between=between,
        buildings=len(results),
        aligned=sum(r["aligned"] for r in results),
        placed_from_neighbors=sum(r["status"] == "neighbors" for r in results),
        unaligned=sum(r["status"] == "unaligned" for r in results),
    )
    (out / "alignment.json").write_text(json.dumps(dict(summary=summary, buildings=results), indent=1))
    log("aligned", matched=summary["aligned"], from_neighbors=summary["placed_from_neighbors"], unaligned=summary["unaligned"])
    return summary


def _naip_fill(area: Window, spec: AreaSpec, naip_scenes, naip_mosaic):
    """Pre-storm mosaic from the newest NAIP year, filling scene edges from
    neighboring scenes of that year (one scene is a quarter quad, so a town
    often spans two), then from older years where still empty."""
    from .sources import pixel_bbox_lonlat

    scenes = naip_scenes(*spec.center, before=spec.landfall, bbox=pixel_bbox_lonlat(area, ZOOM))
    if not scenes:
        raise LookupError("no NAIP pre-storm imagery here")
    newest = scenes[0]
    pre = np.zeros((area.height, area.width, 3), np.uint8)
    ok = np.zeros((area.height, area.width), bool)
    for sc in scenes:
        img, valid = naip_mosaic(sc, area, ZOOM)
        fill = valid & ~ok
        pre[fill] = img[fill]
        ok |= valid
        if ok.all():
            break
    return newest, pre, ok


def storms_between(pre_date: str, landfall: str, this: str) -> list[str]:
    from .storms import STORMS

    places = {p.strip() for p in STORMS[this].where.split(",")} if this in STORMS else set()
    return [
        f"{s.name} ({s.landfall})"
        for s in STORMS.values()
        if s.key != this and pre_date < s.landfall < landfall and places & {p.strip() for p in s.where.split(",")}
    ]


# ---------------------------------------------------------------- SAM helpers


def stretch(img: np.ndarray, low: float = 1.0, high: float = 99.0) -> np.ndarray:
    """Per-crop contrast stretch. The post-storm flight is underexposed, and
    SAM misses white roofs sitting in near-black surroundings without it."""
    lo, hi = np.percentile(img, [low, high])
    if hi <= lo:
        return img
    return np.clip((img.astype(np.float32) - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)


def _sam_line(cache: Path, img: np.ndarray, prompt: str, scale: int) -> None:
    from . import sam

    if not cache.exists():
        cache.write_text(sam.request(img, prompt, scale))


# Stop calling SAM once more than this share of the first calls failed.
OUTAGE_SAMPLE = 24
OUTAGE_SHARE = 0.6


def _sam_all(jobs: list, fetch, workers: int, log: Log) -> set:
    """Run every SAM job; return the ones that failed.

    Meta's API sometimes stops answering for SAM, from a minute to much
    longer. A few failures get a second pass. When most calls fail, stop
    asking: the buildings left are judged from the photos alone, which
    beats making the user wait many minutes for an outage to end.
    """
    import threading

    lock = threading.Lock()
    seen = {"done": 0, "failed": 0}
    tripped = threading.Event()

    def safe(job):
        if tripped.is_set():
            return job, "skipped: Segment Anything is not answering"
        try:
            fetch(job)
            err = None
        except Exception as e:  # noqa: BLE001 - any API failure is handled the same way
            err = f"{type(e).__name__}: {e}"[:200]
        with lock:
            seen["done"] += 1
            seen["failed"] += err is not None
            if seen["done"] >= OUTAGE_SAMPLE and seen["failed"] > OUTAGE_SHARE * seen["done"]:
                tripped.set()
        return (job, err) if err else None

    with ThreadPoolExecutor(_w(workers)) as pool:
        failed = [f for f in pool.map(safe, jobs) if f]
    if failed and not tripped.is_set() and len(failed) <= 0.2 * len(jobs):
        log("sam_retry", failed=len(failed), of=len(jobs), error=failed[0][1])
        time.sleep(2)
        with ThreadPoolExecutor(_w(workers)) as pool:
            failed = [f for f in pool.map(safe, [j for j, _ in failed]) if f]
    if tripped.is_set():
        log("warning", text="Segment Anything (Meta's API) is mostly not answering right now, so the buildings it missed are judged from the photos alone.")
    elif failed:
        log("warning", text=f"Segment Anything did not answer for {len(failed)} of {len(jobs)} images; those buildings are judged from the photos alone.")
    return {j for j, _ in failed}


# Small hosts (512 MB) set LANDFALL_PROCS=1 to decode masks in this process
# instead of one process per CPU. LANDFALL_CONCURRENCY caps how many images
# are in flight to the APIs at once; each is a few kB, so it stays high.
PROCS = int(os.environ.get("LANDFALL_PROCS", "0")) or None
WORKERS = int(os.environ.get("LANDFALL_CONCURRENCY", "24"))


def _w(n: int) -> int:
    return min(n, WORKERS) if WORKERS else n


def _decode_all(fn, *args) -> list:
    if PROCS == 1:
        return list(map(fn, *args))
    with ProcessPoolExecutor(PROCS) as pool:
        return list(pool.map(fn, *args, chunksize=8))


def _union(txt: Path, shape: tuple[int, int], scale: int) -> np.ndarray:
    m = _masks(txt, shape, scale)
    return m.any(0) if len(m) else np.zeros(shape, bool)


def _masks(txt: Path, shape: tuple[int, int], scale: int = SAM_SCALE) -> np.ndarray:
    """Decoded masks for a cached SAM line, stacked (n, h, w). Decoding is
    pure Python and slow (~1 s per line), so the result is stored next to it."""
    from . import sam

    npz = txt.with_suffix(".npz")
    if npz.exists():
        z = np.load(npz)
        if "masks" in z:
            return z["masks"]
        return z["union"][None]
    masks = [x.mask for x in sam.parse(txt.read_text(), shape, scale)]
    arr = np.array(masks, dtype=bool).reshape(len(masks), *shape)
    np.savez_compressed(npz, masks=arr)
    return arr


def _mosaic_crop(m: np.ndarray, x0: int, y0: int, w: int, h: int) -> np.ndarray:
    """Crop a mosaic, padding with black where the crop runs off its edge."""
    out = np.zeros((h, w, 3), np.uint8)
    sy, sx = max(0, y0), max(0, x0)
    ey, ex = min(m.shape[0], y0 + h), min(m.shape[1], x0 + w)
    if ey > sy and ex > sx:
        out[sy - y0 : ey - y0, sx - x0 : ex - x0] = m[sy:ey, sx:ex]
    return out


def _load(d: Path):
    al = json.loads((d / "alignment.json").read_text())
    feats = {f["properties"]["id"]: f for f in json.loads((d / "buildings.geojson").read_text())["features"]}
    return al, feats


def _outline(feats: dict, bid: str, w: Window, zoom: int) -> list[tuple[float, float]]:
    return outline_to_window_pixels([tuple(p) for p in feats[bid]["geometry"]["coordinates"][0][:-1]], zoom, w)


# ---------------------------------------------------------------- segment


def segment(spec: AreaSpec, root: Path, log: Log = _quiet, workers: int = 24, prompts: list[str] | None = None) -> list[dict]:
    """Structure loss: share of the pre-storm structure not found after.

    One prompt alone misses too much in the dark post-storm flight ("roof"
    missed about one roof in six that "house" or "building" found), so the
    structure is the union of all three. The post-storm side is segmented on
    a wider window and matched by shape, which absorbs the few meters the
    alignment can be off.
    """
    from PIL import Image

    from .measure import best_overlap, coverage, polygon_mask, structure_mask

    prompts = prompts or STRUCTURE_PROMPTS
    d = root / spec.name
    al, feats = _load(d)
    zoom = al["summary"]["zoom"]
    (d / "sam").mkdir(exist_ok=True)
    x0, y0 = al["summary"]["mosaic_origin"]
    post_mosaic = np.asarray(Image.open(d / "post.png").convert("RGB"))
    by_id = {r["id"]: r for r in al["buildings"]}

    def image(bid: str, side: str) -> np.ndarray:
        if side == "pre":
            return np.asarray(Image.open(d / "crops" / f"{bid}_pre.png").convert("RGB"))
        r = by_id[bid]
        w = Window(*r["window"])
        return _mosaic_crop(post_mosaic, w.x0 - x0 + r["dx"] - POST_PAD, w.y0 - y0 + r["dy"] - POST_PAD, w.width + 2 * POST_PAD, w.height + 2 * POST_PAD)

    def txt(job) -> Path:
        bid, side, p = job
        name = "pre" if side == "pre" else "postctx"
        return d / "sam" / f"{bid}_{name}_{p.replace(' ', '_')}_s.txt"

    def fetch(job):
        _sam_line(txt(job), stretch(image(job[0], job[1])), job[2], SAM_SCALE)

    jobs = [(r["id"], side, p) for r in al["buildings"] for side in ("pre", "post") for p in prompts]
    log("sam", calls=len(jobs), prompts=prompts)
    missing = _sam_all(jobs, fetch, workers, log)
    done = [j for j in jobs if j not in missing]
    masks = _decode_all(_masks, [txt(j) for j in done], [image(j[0], j[1]).shape[:2] for j in done])
    dets = dict(zip(done, masks))
    unanswered = {j[0] for j in missing}

    out = []
    for r in al["buildings"]:
        bid = r["id"]
        if bid in unanswered:
            out.append(dict(id=bid, status=r["status"], pre_cover=None, structure_loss=None, note="Segment Anything did not answer for this building"))
            continue
        w = Window(*r["window"])
        pts = _outline(feats, bid, w, zoom)
        fp = polygon_mask(pts, (w.height, w.width), dilate_px=2)
        pre_masks = [m for p in prompts for m in dets[(bid, "pre", p)]]
        post_masks = [m for p in prompts for m in dets[(bid, "post", p)]]
        pre = structure_mask(pre_masks, fp) & polygon_mask(pts, fp.shape, dilate_px=6)
        post_ctx = np.zeros((w.height + 2 * POST_PAD, w.width + 2 * POST_PAD), bool)
        for m in post_masks:
            post_ctx |= m
        cover = coverage([pre], fp)
        kept, sdx, sdy = best_overlap(pre, post_ctx, POST_PAD)
        rec = dict(id=bid, status=r["status"], pre_cover=round(cover, 3))
        if cover < 0.3:
            rec.update(structure_loss=None, note="pre-storm structure not found on the outline")
        else:
            rec.update(structure_loss=round(1 - kept, 3), shift=[sdx, sdy])
        out.append(rec)
    (d / "damage.json").write_text(json.dumps(out, indent=1))
    measured = [x for x in out if x["structure_loss"] is not None]
    log(
        "structure_loss",
        measured=len(measured),
        of=len(out),
        over_60=[x["id"] for x in measured if x["structure_loss"] >= 0.6],
        over_25=[x["id"] for x in measured if 0.25 <= x["structure_loss"] < 0.6],
    )
    return out


# ---------------------------------------------------------------- flood


def flood(spec: AreaSpec, root: Path, log: Log = _quiet) -> dict:
    """Water depth above ground at each building: the surveyed water surface
    (USGS high-water marks and NOAA tide gauge peaks) minus 3DEP ground."""
    from .flood import depth_above_ground, gauge_peak, ground_ft, high_water_marks, water_surface

    d = root / spec.name
    obs = []
    radius = spec.hwm_radius_km
    if not spec.stn_event and not spec.gauges:
        # Surveyed high-water marks (USGS), tide gauges (NOAA) and lidar
        # ground (3DEP) are US datasets; nothing comparable is public for
        # most other countries, so depth stays unknown rather than guessed.
        (d / "flood.json").write_text(json.dumps(dict(surface_ft_navd88=None, observations=[], buildings=[])))
        log("flood", error="no surveyed high-water marks or tide gauges for this storm (they are US datasets); flood depth unknown, so the drafts rest on the photos alone")
        return {}
    try:
        obs += high_water_marks(spec.stn_event, spec.center, radius)
        if not obs and not spec.gauges:
            # Surge height changes along a coast, so a farther mark is a
            # rougher estimate; say so rather than report no depth at all.
            radius = 3 * spec.hwm_radius_km
            obs += high_water_marks(spec.stn_event, spec.center, radius)
            if obs:
                log("warning", text=f"No high-water marks within {spec.hwm_radius_km:g} km; using marks up to {radius:g} km away, so depths are rougher.")
    except LookupError as e:
        log("flood_source", error=str(e))
    t0 = date.fromisoformat(spec.landfall)
    for g in spec.gauges:
        try:
            obs.append(
                gauge_peak(
                    g["station"], g["lon"], g["lat"], (t0 - timedelta(days=1)).strftime("%Y%m%d"), (t0 + timedelta(days=3)).strftime("%Y%m%d")
                )
            )
        except LookupError as e:
            log("flood_source", station=g["station"], error=str(e))
    if not obs:
        (d / "flood.json").write_text(json.dumps(dict(surface_ft_navd88=None, observations=[], buildings=[])))
        log("flood", error="no high-water marks or tide gauges near this area; flood depth unknown")
        return {}
    surface = water_surface(obs)
    for o in obs:
        log("flood_source", source=o.source, elev_ft=round(o.elev_ft, 2), note=o.note[:80])

    feats = json.loads((d / "buildings.geojson").read_text())["features"]
    cache = root / "cache" / "epqs.json"
    store = json.loads(cache.read_text()) if cache.exists() else {}

    def key(f) -> str:
        lon, lat = f["properties"]["centroid"]
        return f"{lon:.6f},{lat:.6f}"

    def one(f):
        k = key(f)
        return k, store[k] if k in store else ground_ft(*f["properties"]["centroid"])

    with ThreadPoolExecutor(8) as pool:
        store.update(dict(pool.map(one, feats)))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(store))

    buildings = []
    for f in feats:
        g = store[key(f)]
        dep = depth_above_ground(surface, g)
        buildings.append(dict(id=f["properties"]["id"], ground_ft=None if g is None else round(g, 2), flood_ft=None if dep is None else round(dep, 2)))
    result = dict(surface_ft_navd88=round(surface, 2), radius_km=radius, observations=[o.__dict__ for o in obs], buildings=buildings)
    (d / "flood.json").write_text(json.dumps(result, indent=1))
    deps = sorted(b["flood_ft"] for b in buildings if b["flood_ft"] is not None)
    log(
        "flood",
        surface_ft=round(surface, 2),
        flooded=sum(x > 0 for x in deps),
        of=len(buildings),
        max_ft=deps[-1] if deps else None,
    )
    return result


# ---------------------------------------------------------------- debris


def debris(spec: AreaSpec, root: Path, log: Log = _quiet, workers: int = 24) -> list[dict]:
    """Debris piles around each building and blue tarps on its roof.

    SAM 3.1 finds nothing for "debris" on a tight building crop; it needs the
    yard around the house and the phrase "pile of debris" (or "trash pile").
    Even then it only fires on heavy debris, not scattered branches and
    shingles, so a hit is evidence and a miss is not proof of a clean yard.
    Debris share is the part of an 8 m ring around the house covered by debris
    masks after the storm minus those before (boat yards, junk piles).
    """
    from PIL import Image

    from .measure import polygon_mask

    d = root / spec.name
    al, feats = _load(d)
    zoom = al["summary"]["zoom"]
    x0, y0 = al["summary"]["mosaic_origin"]
    (d / "sam").mkdir(exist_ok=True)
    mosaics = {s: np.asarray(Image.open(d / f"{s}.png").convert("RGB")) for s in ("pre", "post")}
    by_id = {r["id"]: r for r in al["buildings"]}

    def image(bid: str, side: str) -> np.ndarray:
        r = by_id[bid]
        w = Window(*r["window"])
        dx, dy = (r["dx"], r["dy"]) if side == "post" else (0, 0)
        crop = _mosaic_crop(mosaics[side], w.x0 - x0 + dx - YARD_PAD, w.y0 - y0 + dy - YARD_PAD, w.width + 2 * YARD_PAD, w.height + 2 * YARD_PAD)
        return stretch(crop) if side == "post" else crop

    def txt(job) -> Path:
        bid, side, p = job
        return d / "sam" / f"{bid}_{side}yard_{p.replace(' ', '_')}.txt"

    def fetch(job):
        _sam_line(txt(job), image(job[0], job[1]), job[2], YARD_SCALE)

    jobs = [(r["id"], "post", p) for r in al["buildings"] for p in DEBRIS_PROMPTS + TARP_PROMPTS]
    jobs += [(r["id"], "pre", p) for r in al["buildings"] for p in DEBRIS_PROMPTS]
    log("sam", calls=len(jobs), prompts=DEBRIS_PROMPTS + TARP_PROMPTS)
    missing = _sam_all(jobs, fetch, workers, log)
    done = [j for j in jobs if j not in missing]
    shapes = [image(j[0], j[1]).shape[:2] for j in done]
    unions = dict(zip(done, _decode_all(_union, [txt(j) for j in done], shapes, [YARD_SCALE] * len(done))))
    unanswered = {j[0] for j in missing}

    out = []
    for r in al["buildings"]:
        bid = r["id"]
        if bid in unanswered:
            out.append(dict(id=bid, yard_note="Segment Anything did not answer for this building"))
            continue
        w = Window(*r["window"])
        pts = [(x + YARD_PAD, y + YARD_PAD) for x, y in _outline(feats, bid, w, zoom)]
        shape = (w.height + 2 * YARD_PAD, w.width + 2 * YARD_PAD)
        house = polygon_mask(pts, shape, dilate_px=2)
        yard = polygon_mask(pts, shape, dilate_px=RING_PX) & ~house
        post = np.zeros(shape, bool)
        pre = np.zeros(shape, bool)
        for p in DEBRIS_PROMPTS:
            post |= unions[(bid, "post", p)]
            pre |= unions[(bid, "pre", p)]
        new = post & ~pre
        tarp = np.zeros(shape, bool)
        for p in TARP_PROMPTS:
            tarp |= unions[(bid, "post", p)]
        lot = yard | house
        out.append(
            dict(
                id=bid,
                debris_share=round(float((new & lot).sum() / max(1, lot.sum())), 3),
                debris_on_house=round(float((new & house).sum() / max(1, house.sum())), 3),
                pre_debris=round(float((pre & yard).sum() / max(1, yard.sum())), 3),
                tarp_share=round(float((tarp & house).sum() / max(1, house.sum())), 3),
            )
        )
    (d / "debris.json").write_text(json.dumps(out, indent=1))
    log(
        "debris",
        debris=[x["id"] for x in out if x.get("debris_share", 0) >= 0.05],
        tarps=[x["id"] for x in out if x.get("tarp_share", 0) >= 0.05],
    )
    return out


# ---------------------------------------------------------------- assess


def assess(
    spec: AreaSpec,
    root: Path,
    log: Log = _quiet,
    only: list[str] | None = None,
    limit: int = 0,
    workers: int = 24,
    escalate: bool = True,
) -> list[dict]:
    """Draft a FEMA category for every building with the agent loop.

    Buildings are assessed worst-measured first, so a partial run still
    covers the houses that matter most. A run limited by `only` or `limit`
    replaces just those buildings in an existing assessments.json.
    """
    from PIL import Image

    from .agent import assess as assess_one
    from .fema import Measures

    d = root / spec.name
    al, feats = _load(d)
    zoom = al["summary"]["zoom"]
    windows = {r["id"]: Window(*r["window"]) for r in al["buildings"]}
    status = {r["id"]: r["status"] for r in al["buildings"]}
    damage = json.loads((d / "damage.json").read_text())
    debris_path = d / "debris.json"
    yards = {b["id"]: b for b in json.loads(debris_path.read_text())} if debris_path.exists() else {}
    flood_path = d / "flood.json"
    floods = {b["id"]: b["flood_ft"] for b in json.loads(flood_path.read_text())["buildings"]} if flood_path.exists() else {}

    def worst(r: dict) -> tuple:
        y = yards.get(r["id"], {})
        return (
            -(r["structure_loss"] if r["structure_loss"] is not None else 0.5),
            -y.get("debris_share", 0),
            -(floods.get(r["id"]) or 0),
        )

    order = sorted(damage, key=worst)
    if only:
        keep = set(only)
        order = [r for r in order if r["id"] in keep]
    if limit:
        order = order[:limit]
    log("assess_plan", buildings=len(order), first=[r["id"] for r in order[:8]])

    def run(r: dict) -> dict:
        bid = r["id"]
        loss = r["structure_loss"]
        yard = yards.get(bid, {})
        m = Measures(
            roof_loss=loss or 0.0,
            tarp_share=yard.get("tarp_share", 0.0),
            debris_share=yard.get("debris_share", 0.0),
            flood_ft=floods.get(bid),
            aligned=loss is not None,
        )
        pre = np.asarray(Image.open(d / "crops" / f"{bid}_pre.png").convert("RGB"))
        post = np.asarray(Image.open(d / "crops" / f"{bid}_post.png").convert("RGB"))
        outline = _outline(feats, bid, windows[bid], zoom)
        try:
            a = assess_one(bid, pre, post, outline, m, {**r, **yard, "status": status[bid]}, escalate=escalate)
        except Exception as e:  # keep going; one bad building should not stop the area
            log("building_failed", id=bid, error=str(e)[:200])
            return dict(id=bid, error=str(e))
        rec = dict(
            id=bid,
            category=a.category.name.lower(),
            confidence=a.confidence,
            evidence=a.evidence,
            needs_human=a.needs_human,
            decided_by=a.decided_by,
            verified=a.verified,
            demolished=a.demolished,
            structure_loss=loss,
            flood_ft=floods.get(bid),
            debris_share=None if "yard_note" in yard else m.debris_share,
            tarp_share=None if "yard_note" in yard else m.tarp_share,
            trace=a.trace,
        )
        if spec.imagery == "maxar" and rec["category"] in ("minor", "major", "destroyed") and not rec["needs_human"]:
            # Satellite photos are coarser than NOAA's aerial ones and taken
            # at an angle, so a roof can look gone when it is hidden by its
            # own lean or a neighbor. On a satellite pair, damage calls are
            # leads for a field check, never final.
            rec["needs_human"] = True
            rec["trace"] = [*a.trace, {"model": "satellite rule", "note": "damage seen on satellite photos: field check"}]
        log(
            "building",
            id=bid,
            category=rec["category"],
            confidence=round(a.confidence, 2),
            needs_human=a.needs_human,
            decided_by=a.decided_by,
            revisions=sum(1 for t in a.trace if t.get("verifier")),
            evidence=a.evidence,
        )
        return rec

    with ThreadPoolExecutor(_w(workers)) as pool:
        results = list(pool.map(run, order))
    path = d / "assessments.json"
    if (only or limit) and path.exists():
        new = {r["id"]: r for r in results}
        results = [new.pop(r["id"], r) for r in json.loads(path.read_text())] + list(new.values())
    path.write_text(json.dumps(results, indent=1))
    counts = collections.Counter(r.get("category", "error") for r in results)
    log("assessed", counts=dict(counts), review=sum(r.get("needs_human", False) for r in results))
    return results


# ---------------------------------------------------------------- costs


def costs(spec: AreaSpec, root: Path, log: Log = _quiet) -> dict:
    """Repair cost ranges from the drafts and local building values."""
    from . import cost, parcels

    d = root / spec.name
    _, feats = _load(d)
    drafts = json.loads((d / "assessments.json").read_text())
    floods = {}
    if (d / "flood.json").exists():
        floods = {b["id"]: b["flood_ft"] for b in json.loads((d / "flood.json").read_text()).get("buildings", [])}
    from .priority import rank

    try:
        rate = cost.local_rate(spec.bbox if spec.name != "cedar_key" else _drafted_bbox(feats))
    except (LookupError, OSError) as e:
        # No Florida tax roll here: price repairs from the country's average
        # building value instead, and skip FEMA's US-only 50% rule.
        from .sources import ms_country

        country = ms_country(spec.bbox, root / "cache")
        try:
            rate = cost.country_rate(country)
        except LookupError as e2:
            (d / "costs.json").unlink(missing_ok=True)
            log("costs", error=f"no repair costs here: no Florida tax roll ({e}) and {e2}")
            rate = None
        needs = rank(drafts, floods, {})
        (d / "needs.json").write_text(json.dumps(needs, indent=1))
        log(
            "needs",
            households_likely_displaced=needs["households_likely_displaced"],
            households_possibly_displaced=needs["households_possibly_displaced"],
            note="no property records here, so each building counts as one household",
            first=[r["id"] for r in needs["buildings"][:8]],
        )
        if rate is None:
            return {}
        log("country_rate", country=rate["country"], usd_per_sqft=rate["usd_per_sqft"], basis="national average, 2010 prices")
        out = cost.estimate(drafts, feats, floods, rate, {}, us=False)
        out["note"] = (
            f"Building values are {rate['country']}'s national average from the European Commission JRC flood damage database "
            "(2010 prices, converted at the 2010 euro rate), not local property records: a rough planning number, likely low for today. "
            "FEMA's 50% substantial-damage rule is a US rule and isn't applied."
        )
        (d / "costs.json").write_text(json.dumps(out, indent=1))
        log("costs", total_low_usd=out["total_low_usd"], total_high_usd=out["total_high_usd"], basis="national average")
        return out
    log("local_rate", usd_per_sqft=rate["usd_per_sqft"], homes=rate["homes"], roll_year=rate["roll_year"])
    floors = parcels.lookup(d)
    known = [f for f in floors.values() if f.get("floor_high_ft") is not None]
    log("first_floors", estimated=len(known), of=len(floors), low=sum(f["floor_high_ft"] <= 3.0 for f in known))
    out = cost.estimate(drafts, feats, floods, rate, floors)
    (d / "costs.json").write_text(json.dumps(out, indent=1))

    needs = rank(drafts, floods, floors)
    (d / "needs.json").write_text(json.dumps(needs, indent=1))
    log(
        "needs",
        households_likely_displaced=needs["households_likely_displaced"],
        households_possibly_displaced=needs["households_possibly_displaced"],
        first=[r["id"] for r in needs["buildings"][:8]],
    )
    log(
        "costs",
        total_low_usd=out["total_low_usd"],
        total_high_usd=out["total_high_usd"],
        substantial_likely=len(out["substantial_damage_likely"]),
        substantial_possible=len(out["substantial_damage_possible"]),
    )
    return out


def _drafted_bbox(feats: dict) -> tuple[float, float, float, float]:
    xs = [f["properties"]["centroid"][0] for f in feats.values()]
    ys = [f["properties"]["centroid"][1] for f in feats.values()]
    return (min(xs), min(ys), max(xs), max(ys))


STAGES = {"prepare": prepare, "segment": segment, "flood": flood, "debris": debris, "assess": assess, "costs": costs}


def spec_for(root: Path, name: str) -> AreaSpec:
    """A saved area's spec, or the built-in Cedar Key one."""
    if (root / name / "spec.json").exists():
        return AreaSpec.load(root, name)
    if name == CEDAR_KEY.name:
        return CEDAR_KEY
    raise LookupError(f"unknown area {name!r}: no {root / name / 'spec.json'}")


def cli(stage: str, doc: str, **extra_args) -> None:
    """Shared command line for the stage scripts."""
    import argparse

    ap = argparse.ArgumentParser(description=doc)
    ap.add_argument("--area", default="cedar_key")
    ap.add_argument("--out", type=Path, default=Path("data"))
    for flag, kw in extra_args.items():
        ap.add_argument("--" + flag.replace("_", "-"), **kw)
    args = ap.parse_args()
    kwargs = {kw.get("dest", k): getattr(args, kw.get("dest", k)) for k, kw in extra_args.items()}

    def log(event: str, **fields) -> None:
        print(event, json.dumps(fields, default=str))

    STAGES[stage](spec_for(args.out, args.area), args.out, log, **kwargs)
