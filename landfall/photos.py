"""Photos the user uploads (a drone or plane shot of the land after the
disaster, and one from before if they have it) instead of NOAA or Maxar.

The user draws a box on the map over where the photos were taken, so the
after photo is laid north-up over that box at the pipeline's zoom. The before
photo is matched to the after one (SIFT features and a homography), so the
same pixel shows the same ground in both. Building outlines come from Segment
Anything on the photo itself, because a hand-drawn box is too rough to line
up the map's footprints with the picture. Everything after this works on the
same files a drawn box makes.
"""

from __future__ import annotations

import io
import json
import math
from pathlib import Path

import numpy as np

from .geo import Window, lonlat_to_pixel, pixel_to_lonlat

MAX_SIDE = 4000  # px; an upload is shrunk to this on arrival
DETECT_TILE = 320  # px at the pipeline zoom, for building detection
DETECT_OVERLAP = 64
DETECT_PROMPTS = ["building"]


def clean(raw: bytes) -> bytes:
    """An uploaded image as a JPEG: turned upright per its EXIF, shrunk to
    MAX_SIDE, and with its metadata (GPS included) dropped."""
    from PIL import Image, ImageOps

    im = Image.open(io.BytesIO(raw))
    im = ImageOps.exif_transpose(im).convert("RGB")
    im.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def register(before: np.ndarray, after: np.ndarray, min_inliers: int = 25) -> tuple[np.ndarray, np.ndarray] | None:
    """The before photo warped onto the after photo's pixels, and where it has
    data; None when the two can't be matched (different places, or too little
    left in common)."""
    import cv2

    def gray(img: np.ndarray, side: int = 1600) -> tuple[np.ndarray, float]:
        k = min(1.0, side / max(img.shape[:2]))
        g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        g = cv2.resize(g, None, fx=k, fy=k, interpolation=cv2.INTER_AREA) if k < 1 else g
        # even out exposure: a dark post-storm flight against a bright old photo
        return cv2.createCLAHE(3.0, (8, 8)).apply(g), k

    gb, kb = gray(before)
    ga, ka = gray(after)
    sift = cv2.SIFT_create(4000)
    pb, db = sift.detectAndCompute(gb, None)
    pa, da = sift.detectAndCompute(ga, None)
    if db is None or da is None or len(pb) < min_inliers or len(pa) < min_inliers:
        return None
    pairs = cv2.BFMatcher().knnMatch(db, da, k=2)
    good = [m for m, n in (p for p in pairs if len(p) == 2) if m.distance < 0.75 * n.distance]
    if len(good) < min_inliers:
        return None
    src = np.float32([pb[m.queryIdx].pt for m in good]) / kb
    dst = np.float32([pa[m.trainIdx].pt for m in good]) / ka
    H, inl = cv2.findHomography(src, dst, cv2.RANSAC, 4.0 / ka)
    if H is None or int(inl.sum()) < min_inliers:
        return None
    h, w = after.shape[:2]
    warped = cv2.warpPerspective(before, H, (w, h), flags=cv2.INTER_LINEAR)
    ok = cv2.warpPerspective(np.full(before.shape[:2], 255, np.uint8), H, (w, h), flags=cv2.INTER_NEAREST) > 0
    if ok.mean() < 0.3:  # matched, but barely overlapping: not the same view
        return None
    return warped, ok


def _edges(img: np.ndarray, k: float) -> np.ndarray:
    """Edge strength at scale k, evened out: roof and road edges survive a
    change of camera, season and exposure better than colours do."""
    import cv2

    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    g = cv2.resize(g, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    g = cv2.createCLAHE(3.0, (8, 8)).apply(g)
    mag = cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
    return mag / (mag.mean() + 1e-6)


def locate(after: np.ndarray, archive: np.ndarray, box: tuple[int, int], k: float = 0.25, min_score: float = 0.12) -> dict | None:
    """Where the after photo sits in a wider archive photo of the place.

    The user's box gives a rough place and size (`box`, its width and height
    in archive pixels); this tries the photo turned by quarter turns and at
    0.8x to 1.25x that size (by area, so a turned photo keeps its size), and takes the placement whose edges best match
    the archive's. Returns the turn (quarter turns counter-clockwise), the
    top-left corner and size in archive pixels, and the match score; None
    when no placement stands out, as with a different place or a scene the
    disaster changed beyond recognition."""
    import cv2

    A = _edges(archive, k)
    tries = []
    for rot in range(4):
        img = np.ascontiguousarray(np.rot90(after, rot))
        aspect = img.shape[0] / img.shape[1]
        for scale in np.linspace(0.8, 1.25, 19):
            w = int(math.sqrt(box[0] * box[1] / aspect) * scale)
            h = int(w * aspect)
            if w * k >= A.shape[1] - 2 or h * k >= A.shape[0] - 2:
                continue
            T = _edges(cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA), k)
            r = cv2.matchTemplate(A, T, cv2.TM_CCOEFF_NORMED)
            _, score, _, (x, y) = cv2.minMaxLoc(r)
            tries.append(dict(score=float(score), rot=rot, x=int(x / k), y=int(y / k), w=w, h=h))
    if not tries:
        return None
    tries.sort(key=lambda t: -t["score"])
    best = tries[0]
    # the runner-up somewhere else entirely: a real match stands well above it
    far = [t for t in tries if t["rot"] != best["rot"] or abs(t["x"] - best["x"]) + abs(t["y"] - best["y"]) > 0.2 * best["w"]]
    if best["score"] < min_score or (far and best["score"] < 1.5 * far[0]["score"]):
        return None
    return best


def box_window(bbox: tuple[float, float, float, float], zoom: int) -> Window:
    w, s, e, n = bbox
    x0, y0 = lonlat_to_pixel(w, n, zoom)
    x1, y1 = lonlat_to_pixel(e, s, zoom)
    return Window(int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1)))


def lay(d: Path, bbox, zoom: int, area: Window, archive: tuple[np.ndarray, np.ndarray] | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, str]:
    """Post and pre mosaics over `area` from the uploaded photos, with where
    each has data, and a note on how the before photo was matched. With no
    uploaded before photo, `archive` (an archive photo over `area` and where
    it has data) is the before side."""
    from PIL import Image

    box = box_window(bbox, zoom)
    after = np.asarray(Image.open(d / "upload_after.jpg").convert("RGB"))
    before = np.asarray(Image.open(d / "upload_before.jpg").convert("RGB")) if (d / "upload_before.jpg").exists() else None
    note = "no before photo"
    pre_full, pre_ok_full = None, None
    if before is not None:
        reg = register(before, after)
        if reg is not None:
            pre_full, pre_ok_full = reg
            note = "before photo matched to the after photo"
        else:
            # Same framing is the best guess left; the per-building
            # alignment absorbs a few meters of difference.
            pre_full = np.asarray(Image.fromarray(before).resize((after.shape[1], after.shape[0]), Image.LANCZOS))
            pre_ok_full = np.ones(after.shape[:2], bool)
            note = "before photo could not be matched automatically; laid over the same box"

    def place(img: np.ndarray, ok: np.ndarray | None):
        out = np.zeros((area.height, area.width, 3), np.uint8)
        valid = np.zeros((area.height, area.width), bool)
        im = Image.fromarray(img).resize((box.width, box.height), Image.LANCZOS)
        oy, ox = box.y0 - area.y0, box.x0 - area.x0
        out[oy : oy + box.height, ox : ox + box.width] = np.asarray(im)
        m = np.ones((box.height, box.width), bool) if ok is None else np.asarray(Image.fromarray(ok).resize((box.width, box.height), Image.NEAREST))
        valid[oy : oy + box.height, ox : ox + box.width] = m
        return out, valid

    post, post_ok = place(after, None)
    if pre_full is None and archive is not None:
        pre, pre_ok = archive
        return post, post_ok, pre, pre_ok & post_ok, "before photo from the archive, lined up with the after photo"
    if pre_full is None:
        # Nothing to compare against: a flat grey stands in, and every
        # building is judged from the after photo alone.
        pre, pre_ok = np.full_like(post, 128), post_ok.copy()
    else:
        pre, pre_ok = place(pre_full, pre_ok_full)
    return post, post_ok, pre, pre_ok, note


def find_buildings(img: np.ndarray, ok: np.ndarray, origin: tuple[int, int], zoom: int, cache: Path, log=lambda *a, **k: None) -> list[dict]:
    """Building outlines (GeoJSON features with a centroid) found by Segment
    Anything in overlapping tiles of `img`, whose pixel (0, 0) is global pixel
    `origin` at `zoom`. Tiles overlap so a house cut by one tile's edge is
    whole in the next; duplicates are merged."""
    import cv2

    from . import sam
    from .pipeline import _sam_all, stretch

    cache.mkdir(parents=True, exist_ok=True)
    h, w = img.shape[:2]
    step = DETECT_TILE - DETECT_OVERLAP
    tiles = [
        (x, y)
        for y in range(0, max(1, h - DETECT_OVERLAP), step)
        for x in range(0, max(1, w - DETECT_OVERLAP), step)
        if ok[y : y + DETECT_TILE, x : x + DETECT_TILE].mean() > 0.2
    ]
    jobs = [(x, y, p) for x, y in tiles for p in DETECT_PROMPTS]

    def txt(job) -> Path:
        x, y, p = job
        return cache / f"detect_{x}_{y}_{p}.txt"

    def fetch(job):
        x, y, _ = job
        if not txt(job).exists():
            txt(job).write_text(sam.request(stretch(img[y : y + DETECT_TILE, x : x + DETECT_TILE]), job[2], 2))

    log("detect", tiles=len(tiles), prompts=DETECT_PROMPTS)
    _sam_all(jobs, fetch, 16, log)

    found: list[tuple[np.ndarray, tuple[int, int, int, int]]] = []  # mask in tile, tile box
    for job in jobs:
        if not txt(job).exists():
            continue
        x, y, _ = job
        tile = img[y : y + DETECT_TILE, x : x + DETECT_TILE]
        th, tw = tile.shape[:2]
        for det in sam.parse(txt(job).read_text(), (th, tw), 2):
            m = det.mask
            if m.sum() < 20 or m.mean() > 0.4:  # specks, or a whole field
                continue
            ys, xs = np.nonzero(m)
            # cut by an inner tile edge: the neighbouring tile has it whole
            inner = (xs.min() == 0 and x > 0) or (ys.min() == 0 and y > 0) or (xs.max() == tw - 1 and x + tw < w) or (ys.max() == th - 1 and y + th < h)
            if inner:
                continue
            found.append((m, (x, y, tw, th)))

    # merge the same house seen from two tiles: keep the bigger mask
    found.sort(key=lambda f: -f[0].sum())
    taken = np.zeros((h, w), np.int32)
    feats = []
    for m, (x, y, tw, th) in found:
        region = taken[y : y + th, x : x + tw]
        overlap = (region[m] > 0).mean()
        if overlap > 0.3:
            continue
        region[m & (region == 0)] = len(feats) + 1
        cs, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        c = max(cs, key=cv2.contourArea)
        c = cv2.approxPolyDP(c, 1.5, True).reshape(-1, 2)
        if len(c) < 3:
            continue
        ring = [list(pixel_to_lonlat(origin[0] + x + px + 0.5, origin[1] + y + py + 0.5, zoom)) for px, py in c]
        ring.append(ring[0])
        cx, cy = np.mean(ring[:-1], axis=0)
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": {"centroid": [float(cx), float(cy)], "source": "Segment Anything on the uploaded photo"}})
    log("detected", buildings=len(feats))
    return feats


def save_upload(root: Path, uid: str, side: str, raw: bytes) -> dict:
    d = root / "uploads" / uid
    d.mkdir(parents=True, exist_ok=True)
    data = clean(raw)
    (d / f"{side}.jpg").write_bytes(data)
    from PIL import Image

    w, h = Image.open(io.BytesIO(data)).size
    return {"id": uid, "side": side, "width": w, "height": h}


def attach(root: Path, uid: str, area_dir: Path) -> dict:
    """Copy an upload's photos into an area and say what came with it."""
    if not uid.isalnum():
        raise LookupError("not an upload id")
    src = root / "uploads" / uid
    if not (src / "after.jpg").exists():
        raise LookupError("no after photo was uploaded")
    area_dir.mkdir(parents=True, exist_ok=True)
    (area_dir / "upload_after.jpg").write_bytes((src / "after.jpg").read_bytes())
    has_before = (src / "before.jpg").exists()
    if has_before:
        (area_dir / "upload_before.jpg").write_bytes((src / "before.jpg").read_bytes())
    meta = {"before": has_before}
    (area_dir / "upload.json").write_text(json.dumps(meta))
    return meta
