"""Turn SAM masks into the numbers the FEMA verifier reads."""

from __future__ import annotations

import numpy as np


def polygon_mask(points: list[tuple[float, float]], shape: tuple[int, int], dilate_px: int = 0) -> np.ndarray:
    """Filled polygon (crop pixel coordinates) as a bool raster."""
    import cv2

    m = np.zeros(shape, np.uint8)
    cv2.fillPoly(m, [np.round(np.asarray(points)).astype(np.int32)], 1)
    if dilate_px:
        m = cv2.dilate(m, np.ones((2 * dilate_px + 1, 2 * dilate_px + 1), np.uint8))
    return m.astype(bool)


def coverage(masks: list[np.ndarray], footprint: np.ndarray) -> float:
    """Share of the footprint covered by the union of masks."""
    if not footprint.any():
        return 0.0
    union = np.zeros_like(footprint)
    for m in masks:
        union |= m
    return float((union & footprint).sum() / footprint.sum())


def roof_loss(pre_cover: float, post_cover: float, min_pre: float = 0.3) -> float | None:
    """Share of the pre-storm roof missing after the storm.

    None when the pre-storm roof itself was not found over the outline (trees
    over the house, a new build since the pre-storm flight): there is nothing
    reliable to compare against.
    """
    if pre_cover < min_pre:
        return None
    return float(min(1.0, max(0.0, 1.0 - post_cover / pre_cover)))


def structure_mask(masks: list[np.ndarray], footprint: np.ndarray, min_inside: float = 0.3) -> np.ndarray:
    """Union of the detections that belong to this building: those with at
    least `min_inside` of their own area on the (dilated) outline. Keeps the
    neighbor's house out of the comparison."""
    out = np.zeros_like(footprint)
    for m in masks:
        n = m.sum()
        if n and (m & footprint).sum() >= min_inside * n:
            out |= m
    return out


def canopy_mask(rgb: np.ndarray, min_texture: float = 6.0) -> np.ndarray:
    """Living tree canopy: green and leafy. Green alone would take in a green
    metal roof, so canopy must also be rough (leaves and their shadows vary
    from pixel to pixel; a roof sheet is smooth)."""
    import cv2

    px = rgb.astype(np.float32)
    r, g, b = px[..., 0], px[..., 1], px[..., 2]
    green = (g > r + 4) & (g > b + 4) & (px.max(2) < 200)
    gray = px.mean(2)
    mean = cv2.blur(gray, (5, 5))
    rough = np.sqrt(np.maximum(cv2.blur(gray * gray, (5, 5)) - mean * mean, 0)) >= min_texture
    m = (green & rough).astype(np.uint8)
    # fill the gaps between leaves, drop lone green specks
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    return cv2.morphologyEx(cv2.morphologyEx(m, cv2.MORPH_CLOSE, k), cv2.MORPH_OPEN, k).astype(bool)


def under_trees(pre: np.ndarray, post_ctx: np.ndarray, canopy_ctx: np.ndarray, dx: int, dy: int, pad: int, tolerance: int = 0) -> tuple[float, float]:
    """Split the pre-storm structure at the chosen shift into what reappears,
    what is hidden under tree canopy, and what is gone.

    Returns (structure loss counting only the part trees don't hide, share of
    the structure hidden by trees). A tree grown over a roof since the
    before photo hides it without removing it, so canopy over a missing
    patch is not counted as loss."""
    import cv2

    n = int(pre.sum())
    if n == 0:
        return 0.0, 0.0
    h, w = pre.shape
    y, x = dy + pad, dx + pad
    post = post_ctx.astype(np.uint8)
    if tolerance:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * tolerance + 1, 2 * tolerance + 1))
        post = cv2.dilate(post, k)
    seen = post[y : y + h, x : x + w].astype(bool) & pre
    trees = canopy_ctx[y : y + h, x : x + w] & pre & ~seen
    hidden = int(trees.sum())
    visible = n - hidden
    if visible == 0:
        return 0.0, 1.0
    return float(1 - seen.sum() / visible), hidden / n


def best_overlap(pre: np.ndarray, post_ctx: np.ndarray, pad: int, tolerance: int = 0) -> tuple[float, int, int]:
    """How much of the pre-storm structure mask reappears in the post-storm
    masks, allowing the post-storm house to sit up to `pad` px away.

    `post_ctx` is the post-storm mask raster for the same window grown by
    `pad` on every side. The search absorbs what pixel alignment can't: a
    house on pilings leaning differently in each flight. Returns
    (share of pre pixels matched at the best shift, dx, dy).
    """
    import cv2

    n = int(pre.sum())
    if n == 0:
        return 0.0, 0, 0
    if not post_ctx.any():
        return 0.0, 0, 0
    post = post_ctx.astype(np.uint8)
    if tolerance:
        # The two photos are taken from different angles: a roof's edges
        # shift a little and walls show on one side. Grow the post-storm
        # masks by `tolerance` px so that edge drift is not counted as loss.
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * tolerance + 1, 2 * tolerance + 1))
        post = cv2.dilate(post, k)
    r = cv2.matchTemplate(post.astype(np.float32), pre.astype(np.float32), cv2.TM_CCORR)
    _, best, _, (ix, iy) = cv2.minMaxLoc(r)
    return float(best / n), ix - pad, iy - pad
