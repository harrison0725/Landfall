"""Line up a building's pre-storm and post-storm crops.

The two photo sets come from different flights and are only registered to a
few meters, which at roof scale is enough to make an intact house look
damaged. We search a small window of integer shifts and keep the one where
the edge maps of the two crops agree best. Edges (gradient magnitude) are
used instead of raw brightness because the flights differ in sun angle,
season and color balance, while roof lines and streets stay put.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Alignment:
    dx: int  # shift to apply to the post crop, in pixels
    dy: int
    score: float  # normalized correlation of edge maps at the best shift, -1..1
    margin: float  # best score minus the best score outside a 1px neighborhood

    @property
    def confident(self) -> bool:
        """A clear, isolated peak. Low-margin results get flagged for review
        rather than trusted, e.g. a roof that was fully destroyed."""
        return self.score >= 0.3 and self.margin >= 0.05


def to_gray(img: np.ndarray) -> np.ndarray:
    img = np.asarray(img, dtype=np.float64)
    if img.ndim == 3:
        img = img[..., :3].mean(axis=2)
    return img


def edges(img: np.ndarray) -> np.ndarray:
    g = to_gray(img)
    gy, gx = np.gradient(g)
    return np.hypot(gx, gy)


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    denom = np.sqrt((a * a).sum() * (b * b).sum())
    if denom == 0:
        return 0.0
    return float((a * b).sum() / denom)


def find_offset(pre: np.ndarray, post: np.ndarray, max_shift: int = 12) -> Alignment:
    """Best integer shift (dx, dy) so that post[y + dy, x + dx] ~ pre[y, x].

    Both crops must be the same shape. Only the overlapping region is
    compared at each shift, so the crops should carry a margin at least
    max_shift pixels wide around the building.
    """
    if pre.shape[:2] != post.shape[:2]:
        raise ValueError(f"crop shapes differ: {pre.shape} vs {post.shape}")
    h, w = pre.shape[:2]
    if h <= 2 * max_shift or w <= 2 * max_shift:
        raise ValueError("crop too small for the shift search")

    ea, eb = edges(pre), edges(post)
    scores = np.full((2 * max_shift + 1, 2 * max_shift + 1), -np.inf)
    for dy in range(-max_shift, max_shift + 1):
        for dx in range(-max_shift, max_shift + 1):
            ya0, ya1 = max(0, -dy), min(h, h - dy)
            xa0, xa1 = max(0, -dx), min(w, w - dx)
            a = ea[ya0:ya1, xa0:xa1]
            b = eb[ya0 + dy : ya1 + dy, xa0 + dx : xa1 + dx]
            scores[dy + max_shift, dx + max_shift] = _ncc(a, b)

    iy, ix = np.unravel_index(np.argmax(scores), scores.shape)
    best = float(scores[iy, ix])
    others = scores.copy()
    others[max(0, iy - 1) : iy + 2, max(0, ix - 1) : ix + 2] = -np.inf
    runner_up = float(others.max()) if np.isfinite(others).any() else -1.0
    return Alignment(
        dx=int(ix - max_shift),
        dy=int(iy - max_shift),
        score=best,
        margin=best - runner_up,
    )


def apply_offset(post: np.ndarray, al: Alignment) -> np.ndarray:
    """Shift the post crop so it overlays the pre crop. Pixels shifted in
    from outside the crop are zero."""
    out = np.zeros_like(post)
    h, w = post.shape[:2]
    dy, dx = al.dy, al.dx
    ys, yd = (slice(dy, h), slice(0, h - dy)) if dy >= 0 else (slice(0, h + dy), slice(-dy, h))
    xs, xd = (slice(dx, w), slice(0, w - dx)) if dx >= 0 else (slice(0, w + dx), slice(-dx, w))
    out[yd, xd] = post[ys, xs]
    return out


# --- Per-building matching on real photo pairs --------------------------------
#
# On real NAIP/NOAA pairs the whole-crop search above is not enough: the two
# flights differ in sun angle (long shadows after the storm), exposure, and
# viewing angle, and Cedar Key's houses stand on pilings, so a roof is
# displaced from its ground footprint by a different amount in each photo.
# Ground features and roofs then disagree about the best shift. So we match
# the pre-storm roof (the outline's box plus a small pad) inside a larger
# post-storm search area, on histogram-equalized edges, and then check each
# building's shift against its neighbors, which were flown in the same frame.


def equalized_edges(img: np.ndarray, blur_sigma: float = 1.2) -> np.ndarray:
    """Gradient magnitude after histogram equalization, so a dark post-storm
    flight and a bright pre-storm one produce comparable edges."""
    import cv2

    g = np.asarray(img)
    if g.ndim == 3:
        g = cv2.cvtColor(g[..., :3].astype(np.uint8), cv2.COLOR_RGB2GRAY)
    g = cv2.equalizeHist(g.astype(np.uint8)).astype(np.float32)
    g = cv2.GaussianBlur(g, (0, 0), blur_sigma)
    return cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))


def match_building(
    pre: np.ndarray, post: np.ndarray, box: tuple[int, int, int, int], max_shift: int = 28
) -> Alignment:
    """Find where the pre-storm roof inside `box` (x0, y0, x1, y1) sits in the
    post-storm image.

    `pre` and `post` are same-shape context crops in the same pixel grid, and
    `box` grown by max_shift on every side must fit inside them. The margin is
    the drop from the peak to the best match more than 4 px away; edge peaks
    are a few pixels wide, so a 1 px neighborhood would understate it.
    """
    import cv2

    x0, y0, x1, y1 = box
    h, w = pre.shape[:2]
    if pre.shape[:2] != post.shape[:2]:
        raise ValueError(f"crop shapes differ: {pre.shape} vs {post.shape}")
    if x0 - max_shift < 0 or y0 - max_shift < 0 or x1 + max_shift > w or y1 + max_shift > h:
        raise ValueError("box plus the shift search does not fit in the crop")

    tmpl = equalized_edges(pre)[y0:y1, x0:x1]
    area = equalized_edges(post)[y0 - max_shift : y1 + max_shift, x0 - max_shift : x1 + max_shift]
    if tmpl.std() == 0 or area.std() == 0:
        return Alignment(0, 0, 0.0, 0.0)
    r = cv2.matchTemplate(area, tmpl, cv2.TM_CCOEFF_NORMED)
    _, best, _, (ix, iy) = cv2.minMaxLoc(r)
    others = r.copy()
    others[max(0, iy - 4) : iy + 5, max(0, ix - 4) : ix + 5] = -np.inf
    runner_up = float(others.max()) if np.isfinite(others).any() else -1.0
    return Alignment(dx=ix - max_shift, dy=iy - max_shift, score=float(best), margin=float(best) - runner_up)


@dataclass(frozen=True)
class Placement:
    """Where to read a building in the post-storm photo, and how we know."""

    dx: int
    dy: int
    status: str  # "matched", "neighbors" or "unaligned"
    own: Alignment
    neighbor_dx: float | None
    neighbor_dy: float | None

    @property
    def aligned(self) -> bool:
        return self.status == "matched"


def place_buildings(
    matches: list[Alignment],
    centers: list[tuple[float, float]],
    k: int = 8,
    tol: int = 4,
    min_score: float = 0.2,
    strong_score: float = 0.25,
    strong_margin: float = 0.05,
    sure_score: float = 0.35,
    sure_margin: float = 0.08,
) -> list[Placement]:
    """Decide each building's post-storm offset using its neighbors.

    A match is "strong" when its peak is high and isolated. The neighbor
    estimate for a building is the median shift of its k nearest strong
    matches (excluding itself). A building is "matched" when its own shift
    agrees with its neighbors within `tol` px (and the peak is not noise), or
    when its own peak is clear enough to stand alone (`sure_*`). The second
    case matters for tall buildings: a three-story house on pilings leans
    further in the photo than the one-story houses around it.

    Otherwise, if neighbors exist, it is placed at the neighbor shift with
    status "neighbors": we know where the house should be, but its roof did
    not show up there. That is either occlusion (trees, shadow) or the house
    is gone, and downstream steps must not read roof loss as certain.
    """
    centers_a = np.asarray(centers, dtype=np.float64)
    shifts = np.array([[m.dx, m.dy] for m in matches], dtype=np.float64)
    strong = np.array([m.score >= strong_score and m.margin >= strong_margin for m in matches])
    out = []
    for i, m in enumerate(matches):
        idx = np.flatnonzero(strong)
        idx = idx[idx != i]
        if len(idx) == 0:
            status = "matched" if strong[i] else "unaligned"
            out.append(Placement(m.dx, m.dy, status, m, None, None))
            continue
        dist = np.linalg.norm(centers_a[idx] - centers_a[i], axis=1)
        nn = idx[np.argsort(dist)[:k]]
        ndx, ndy = np.median(shifts[nn], axis=0)
        agrees = abs(m.dx - ndx) <= tol and abs(m.dy - ndy) <= tol
        sure = m.score >= sure_score and m.margin >= sure_margin
        if (agrees and m.score >= min_score) or sure:
            out.append(Placement(m.dx, m.dy, "matched", m, float(ndx), float(ndy)))
        else:
            out.append(Placement(int(round(ndx)), int(round(ndy)), "neighbors", m, float(ndx), float(ndy)))
    return out
