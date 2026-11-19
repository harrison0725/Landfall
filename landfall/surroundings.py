"""What surrounds each building in the before photo: trees, lawn or grass,
or pavement, bare ground and other roofs.

Read from the before-storm crop Landfall already cuts for each building
(about 10 m of ground on every side), outside the building's own outline.
Trees are green and leafy (measure.canopy_mask: green and rough, so a green
roof doesn't count); lawn is green to straw-green and smooth. The rest is
pavement, bare ground, sand, water and neighbors' roofs. Deep shadow is left
out of the shares. Written to surroundings.json and read by the area agent.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .geo import Window, outline_to_window_pixels
from .measure import canopy_mask, polygon_mask


def classify(rgb: np.ndarray, ring: np.ndarray) -> dict:
    """Shares of the ring (bool mask) that are trees, lawn or grass, and
    other ground. Deep shadow is left out: it hides what's under it."""
    import cv2

    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV).astype(np.int16)  # hue 0-179
    h, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    shade = val < 55
    trees = canopy_mask(rgb) & ~shade
    # lawn and grass, green to the straw-green of a winter lawn, not leafy
    lawn = (h >= 22) & (h <= 50) & (sat >= 40) & (val >= 70) & ~trees & ~shade
    seen = ring & ~shade
    n = max(1, int(seen.sum()))
    share = {k: round(float((m & seen).sum()) / n, 3) for k, m in (("trees", trees), ("lawn", lawn))}
    share["paved_bare_other"] = round(max(0.0, 1 - share["trees"] - share["lawn"]), 3)
    share["shade"] = round(float((shade & ring).sum()) / max(1, int(ring.sum())), 3)
    return share


def label(s: dict) -> str:
    """One plain group name for a building's surroundings."""
    if s["trees"] + s["lawn"] < 0.2:
        return "little green (pavement, sand or bare ground)"
    return "mostly trees" if s["trees"] >= s["lawn"] else "mostly lawn or grass"


def compute(d: Path) -> dict:
    """surroundings.json for an area: {building id: shares + label}."""
    from PIL import Image

    al = json.loads((d / "alignment.json").read_text())
    feats = {f["properties"]["id"]: f for f in json.loads((d / "buildings.geojson").read_text())["features"]}
    zoom = al["summary"]["zoom"]
    out = {}
    for rec in al["buildings"]:
        bid = rec["id"]
        crop = d / "crops" / f"{bid}_pre.png"
        if bid not in feats or not crop.exists():
            continue
        rgb = np.asarray(Image.open(crop).convert("RGB"))
        w = Window(*rec["window"])
        pts = outline_to_window_pixels([tuple(p) for p in feats[bid]["geometry"]["coordinates"][0][:-1]], zoom, w)
        house = polygon_mask(pts, rgb.shape[:2], dilate_px=2)
        ring = ~house
        if ring.sum() < 50:
            continue
        s = classify(rgb, ring)
        out[bid] = s | {"label": label(s)}
    (d / "surroundings.json").write_text(json.dumps(out, indent=1))
    return out


def load(d: Path) -> dict:
    """surroundings.json, made on first use where the crops are there."""
    p = d / "surroundings.json"
    if p.exists():
        return json.loads(p.read_text())
    try:
        return compute(d)
    except (OSError, KeyError, ValueError):
        return {}
