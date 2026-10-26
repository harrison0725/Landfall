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
