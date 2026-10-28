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


def gps(raw: bytes) -> tuple[float, float] | None:
    """Where a photo says it was taken (lat, lon), from its EXIF GPS block, as
    drone and phone cameras write; read before `clean` drops it."""
    from PIL import Image

    try:
        g = Image.open(io.BytesIO(raw)).getexif().get_ifd(0x8825)
        if not g or 2 not in g or 4 not in g:
            return None
        deg = lambda v: float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600  # noqa: E731
        lat, lon = deg(g[2]) * (-1 if g.get(1) == "S" else 1), deg(g[4]) * (-1 if g.get(3) == "W" else 1)
    except Exception:
        return None
    return (round(lat, 5), round(lon, 5)) if (lat, lon) != (0.0, 0.0) and abs(lat) <= 90 and abs(lon) <= 180 else None


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


def locate(after: np.ndarray, archive: np.ndarray, box: tuple[int, int], k: float = 0.25, min_score: float = 0.12, ok: np.ndarray | None = None, near: bool = False) -> dict | None:
    """Where the after photo sits in a wider archive photo of the place.

    The user's box gives a rough place and size (`box`, its width and height
    in archive pixels); this tries the photo turned by quarter turns and at
    0.8x to 1.25x that size (by area, so a turned photo keeps its size), and takes the placement whose edges best match
    the archive's. Returns the turn (quarter turns counter-clockwise), the
    top-left corner and size in archive pixels, and the match score; None
    when no placement stands out, as with a different place or a scene the
    disaster changed beyond recognition. With `near`, the box is known to
    within a few metres (the town search found it): the best placement is
    taken if it stands out from its own scores and sits about there."""
    import cv2

    # a big photo is matched coarser: a degree or two of turn, or a lens's
    # bend, then moves its far edges less than an edge is wide
    k = min(k, 256 / max(box))
    A = _edges(archive, k)
    tries = []
    for rot in range(4):
        img = np.ascontiguousarray(np.rot90(after, rot))
        okr = None if ok is None else np.ascontiguousarray(np.rot90(ok, rot))
        aspect = img.shape[0] / img.shape[1]
        for scale in np.linspace(0.8, 1.25, 19):
            w = int(math.sqrt(box[0] * box[1] / aspect) * scale)
            h = int(w * aspect)
            if w * k >= A.shape[1] - 2 or h * k >= A.shape[0] - 2:
                continue
            T = _edges(cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA), k)
            if okr is None:
                r = cv2.matchTemplate(A, T, cv2.TM_CCOEFF_NORMED)
            else:  # a turned photo: match only where it has picture
                M = _inside(cv2.resize(okr.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST), T.shape)
                r = np.nan_to_num(cv2.matchTemplate(A, T, cv2.TM_CCOEFF_NORMED, mask=M), nan=0.0, posinf=0.0, neginf=0.0)
            _, score, _, (x, y) = cv2.minMaxLoc(r)
            z = (score - float(r.mean())) / (float(r.std()) + 1e-6)
            tries.append(dict(score=float(score), z=z, rot=rot, x=int(x / k), y=int(y / k), w=w, h=h))
    if not tries:
        return None
    tries.sort(key=lambda t: -t["score"])
    best = tries[0]
    # the runner-up somewhere else entirely: a real match stands well above it
    far = [t for t in tries if t["rot"] != best["rot"] or abs(t["x"] - best["x"]) + abs(t["y"] - best["y"]) > 0.2 * best["w"]]
    if best["score"] < min_score:
        return None
    if near:
        # checking a spot already found from afar: it is there, close to
        # where expected, or not at all
        m = (archive.shape[1] - box[0]) / 2, (archive.shape[0] - box[1]) / 2
        return best if best["z"] >= 4 and math.hypot(best["x"] - m[0], best["y"] - m[1]) <= 0.25 * max(box) else None
    # a real match stands well above the best placement elsewhere, in score
    # or, for a big photo whose turned-round self matches part of a street
    # grid, in how far it stands above its own turn's spread of scores
    if far and best["score"] < 1.5 * far[0]["score"] and not (best["z"] >= 4.5 and best["z"] >= 1.5 * max(t["z"] for t in far)):
        return None
    return best


def _directions(img: np.ndarray, k: float, blur: float = 1.0) -> list[np.ndarray]:
    """Which way the edges run at scale k, weighted by how clear they are.
    Directions (doubled, so a dark-to-light edge matches a light-to-dark
    one) survive a different camera, season and storm far better than edge
    strength: across a whole town they tell the right spot from look-alikes."""
    import cv2

    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    g = cv2.resize(g, None, fx=k, fy=k, interpolation=cv2.INTER_AREA).astype(np.float32)
    g = cv2.GaussianBlur(g, (0, 0), 0.8)
    gx, gy = cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1)
    m2 = gx * gx + gy * gy + 1e-6
    m = np.sqrt(m2)
    w = m / (m + np.percentile(m, 70))  # saturates: contrast doesn't matter
    return [cv2.GaussianBlur(w * (gx * gx - gy * gy) / m2, (0, 0), blur), cv2.GaussianBlur(w * 2 * gx * gy / m2, (0, 0), blur)]


def _inside(ok: np.ndarray, shape: tuple[int, ...], margin: int = 3) -> np.ndarray:
    """`ok` shrunk to `shape` and pulled in from its edge by `margin` pixels."""
    import cv2

    small = cv2.resize(ok.astype(np.uint8), (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST)
    return cv2.erode(small, np.ones((2 * margin + 1, 2 * margin + 1), np.uint8)).astype(np.float32)


def _middle(ok: np.ndarray) -> tuple[int, int, int, int]:
    """The biggest box about the middle of a turned photo that is all picture."""
    H, W = ok.shape
    lo, hi = 0.0, 1.0
    for _ in range(12):
        f = (lo + hi) / 2
        x0, x1 = int(W / 2 * (1 - f)), int(W / 2 * (1 + f)) - 1
        y0, y1 = int(H / 2 * (1 - f)), int(H / 2 * (1 + f)) - 1
        lo, hi = (f, hi) if ok[y0, x0] and ok[y0, x1] and ok[y1, x0] and ok[y1, x1] else (lo, f)
    x0, x1 = int(W / 2 * (1 - lo)) + 1, int(W / 2 * (1 + lo)) - 1
    y0, y1 = int(H / 2 * (1 - lo)) + 1, int(H / 2 * (1 + lo)) - 1
    return x0, y0, x1, y1


def turn(img: np.ndarray, deg: float) -> tuple[np.ndarray, np.ndarray]:
    """The photo turned `deg` degrees counter-clockwise on a canvas big
    enough to hold all of it, and where the canvas has picture."""
    import cv2

    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1.0)
    c, s = abs(M[0, 0]), abs(M[0, 1])
    W, H = int(round(h * s + w * c)), int(round(h * c + w * s))
    M[0, 2] += W / 2 - w / 2
    M[1, 2] += H / 2 - h / 2
    out = cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR)
    ok = cv2.warpAffine(np.full((h, w), 255, np.uint8), M, (W, H), flags=cv2.INTER_NEAREST) > 0
    return out, ok


def _grid(img: np.ndarray, k: float = 0.25) -> np.ndarray:
    """How much strong edge runs at each angle, 0 to 89 degrees (1 is
    average): streets and buildings mostly run along a grid, whose angle
    repeats every 90. Angles are as `turn` counts them, so turning a photo
    by d takes d off each."""
    import cv2

    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    g = cv2.resize(g, None, fx=k, fy=k, interpolation=cv2.INTER_AREA).astype(np.float32)
    g = cv2.GaussianBlur(g, (0, 0), 1.0)
    gx, gy = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=5), cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=5)
    m = np.hypot(gx, gy)
    strong = m > np.percentile(m, 80)
    h, _ = np.histogram((np.degrees(np.arctan2(gy, gx)) % 90)[strong], 90, (0, 90))
    h = np.convolve(np.r_[h[-1:], h, h[:1]].astype(float), np.ones(3) / 3, "same")[1:-1]
    return h / (h.mean() + 1e-9)


def _grids(img: np.ndarray, tile: int = 512, min_peak: float = 1.3) -> list[int]:
    """The street grids' angles in an archive photo, most common first. A
    town can have several (an old centre and a newer grid at an angle), and
    water, fields and woods have none, so only tiles with a clear grid count."""
    votes = np.zeros(90)
    H, W = img.shape[:2]
    for y in range(0, max(1, H - tile // 2), tile):
        for x in range(0, max(1, W - tile // 2), tile):
            h = _grid(img[y : y + tile, x : x + tile])
            if h.max() >= min_peak:
                votes += np.clip(h - 1, 0, None)
    votes = np.convolve(np.r_[votes[-2:], votes, votes[:2]], np.ones(5), "same")[2:-2]  # 0 and 89 are one grid
    tops = sorted((d for d in range(90) if votes[d] >= votes[d - 1] and votes[d] >= votes[(d + 1) % 90]), key=lambda d: -votes[d])
    out: list[int] = []
    for d in tops:
        if votes[d] < 0.1 * votes[tops[0]]:
            break
        if all(not _near(d, o, 8) for o in out):
            out.append(int(d))
    return out


def angles(after: np.ndarray, archive: np.ndarray, n: int = 3) -> list[int]:
    """The turns (degrees counter-clockwise, 0 to 89) that line the photo's
    street grid up with each of the archive's, most common grid first; the
    quarter turns on top are left to the matching. A north-up photo gets
    about 0; one taken at a slant gets its slant, to within a few degrees."""
    h = _grid(after)
    p = int(np.argmax(h))
    return [(p - g) % 90 for g in _grids(archive)[:n]]


def _near(a: float, b: float, tol: float = 4.0) -> bool:
    d = abs(a - b) % 90
    return min(d, 90 - d) <= tol


def slant_tries(after: np.ndarray, archive: np.ndarray, n: int = 4) -> list[int]:
    """North-up first (most photos from the air are, or a quarter turn
    off), then the slants that would line the photo's grid up with each of
    the archive's, most common grid first."""
    out = [0]
    for d in angles(after, archive, n):
        if all(not _near(d, o) for o in out):
            out.append(d)
    return out


def search(after: np.ndarray, archive: np.ndarray, m_per_px: float, min_m: float = 60.0, max_m: float = 1000.0, step: float = 1.04, min_z: float = 5.0, n: int = 3, slants: list[int] | None = None) -> list[dict]:
    """Where the after photo was taken, anywhere in an archive photo of a
    whole town, when nobody has said where or how much ground it shows.

    Tries the photo north-up and turned to line its street grid up with the
    town's (`angles`; or just `slants`), each with quarter turns on top, at ground widths from
    `min_m` to `max_m`, 4% apart, matching edge directions on a coarse
    archive photo (`m_per_px` metres a pixel). Returns up to `n` likely
    spots, best first, each with its turn (`angle`, degrees counter-
    clockwise) and the turned photo's placement in archive pixels; `locate`
    on sharp photos then says which, if any, is really it."""
    import cv2

    if slants is None:
        slants = slant_tries(after, archive)
    feats: dict[float, list[np.ndarray]] = {}
    tries = []
    for slant in slants:
        if slant:
            # matched on the middle of the turned photo, clear of its blank
            # corners; placed as the whole turned photo
            whole, ok = turn(after, slant)
            x0, y0, x1, y1 = _middle(ok)
            base = whole[y0:y1, x0:x1]
        else:
            whole, base, (x0, y0) = after, after, (0, 0)
        for rot in range(4):
            img = np.ascontiguousarray(np.rot90(base, rot))
            # where the middle sits in the whole turned photo, turned the same way
            Ho, Wo = whole.shape[:2]
            ch, cw = base.shape[:2]
            ox, oy = [(x0, y0), (y0, Wo - x0 - cw), (Wo - x0 - cw, Ho - y0 - ch), (Ho - y0 - ch, x0)][rot]
            H, W = (Ho, Wo) if rot % 2 == 0 else (Wo, Ho)
            aspect = img.shape[0] / img.shape[1]
            for metres in np.geomspace(min_m, max_m, int(np.log(max_m / min_m) / np.log(step)) + 1):
                w = int(metres / m_per_px * img.shape[1] / W)  # the whole photo spans `metres`
                h = int(w * aspect)
                k = min(0.5, max(0.125, 2 ** round(np.log2(90 / w)))) if w else 0.5  # templates about 90 px across
                if w * k < 24 or w >= archive.shape[1] - 2 or h >= archive.shape[0] - 2:
                    continue
                if k not in feats:
                    feats[k] = _directions(archive, k)
                T = _directions(cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA), k)
                r = sum(cv2.matchTemplate(a, t, cv2.TM_CCOEFF_NORMED) for a, t in zip(feats[k], T)) / 2
                _, score, _, (x, y) = cv2.minMaxLoc(r)
                z = (score - float(r.mean())) / (float(r.std()) + 1e-6)
                f = w / img.shape[1]  # archive pixels per photo pixel
                tries.append(dict(score=float(score), z=z, rot=rot, slant=slant, angle=(slant + 90 * rot) % 360,
                                  x=int(x / k - ox * f), y=int(y / k - oy * f), w=int(W * f), h=int(H * f)))
    # a small template matches something somewhere by chance; rank each try
    # by how far its peak stands above that size's own spread of scores
    tries.sort(key=lambda t: -t["z"])
    found: list[dict] = []
    for t in tries:
        if t["z"] < min_z:
            break
        cx, cy = t["x"] + t["w"] / 2, t["y"] + t["h"] / 2
        if all(math.hypot(f["x"] + f["w"] / 2 - cx, f["y"] + f["h"] / 2 - cy) > 0.5 * max(f["w"], t["w"]) for f in found):
            found.append(t)
            if len(found) == n:
                break
    return found


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

    shown = d / "upload_after_ok.png"  # a photo turned to north-up: where it has picture
    post, post_ok = place(after, np.asarray(Image.open(shown)) > 0 if shown.exists() else None)
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
    # where the camera says it was: only to search near it, never kept in the photo
    at = gps(raw) if side == "after" else None
    return {"id": uid, "side": side, "width": w, "height": h, **({"gps": list(at)} if at else {})}


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
