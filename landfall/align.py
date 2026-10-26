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
