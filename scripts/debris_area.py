"""Look for storm debris around each building and blue tarps on its roof.

    python scripts/debris_area.py --area cedar_key

SAM 3.1 finds nothing for "debris" on a tight building crop; it needs the
yard around the house, and the phrase "pile of debris" (or "trash pile").
Even then it only fires on heavy debris (wrecked houses, piles at the curb),
not on scattered branches and shingles. So a debris hit is strong evidence
and a miss is not evidence of a clean yard.

Debris share is the part of a ring around the house (outline grown by about
8 m) covered by debris masks in the post-storm photo, minus whatever SAM
also calls debris there in the pre-storm photo (boat yards, junk piles).
Tarp share is the part of the outline covered by "blue tarp" masks.

Raw SAM lines are cached under data/<area>/sam/ like segment_area.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from landfall.geo import Window, outline_to_window_pixels  # noqa: E402
from landfall.measure import polygon_mask  # noqa: E402
from segment_area import stretch  # noqa: E402

from landfall import sam  # noqa: E402

PAD = 60  # px (~16 m) of yard around the building window
SCALE = 2
RING_PX = 30  # ~8 m around the outline counts as this building's yard
DEBRIS_PROMPTS = ["pile of debris", "trash pile"]
TARP_PROMPTS = ["blue tarp"]


def masks_for(txt: Path, shape: tuple[int, int]) -> np.ndarray:
    """Union of all masks in a cached SAM line, at crop resolution."""
    npz = txt.with_suffix(".npz")
    if npz.exists():
        return np.load(npz)["union"]
    union = np.zeros(shape, bool)
    for det in sam.parse(txt.read_text(), shape, SCALE):
        union |= det.mask
    np.savez_compressed(npz, union=union)
    return union


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", default="cedar_key")
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()
    d = args.out / args.area
    al = json.loads((d / "alignment.json").read_text())
    zoom = al["summary"]["zoom"]
    x0, y0 = al["summary"]["mosaic_origin"]
    feats = {f["properties"]["id"]: f for f in json.loads((d / "buildings.geojson").read_text())["features"]}
    mosaics = {s: np.asarray(Image.open(d / f"{s}.png").convert("RGB")) for s in ("pre", "post")}
    by_id = {r["id"]: r for r in al["buildings"]}

    def origin(bid: str, side: str) -> tuple[int, int]:
        """Top-left of the padded crop in mosaic pixels (may be negative)."""
        r = by_id[bid]
        w = Window(*r["window"])
        dx, dy = (r["dx"], r["dy"]) if side == "post" else (0, 0)
        return w.x0 - x0 + dx - PAD, w.y0 - y0 + dy - PAD

    def image(bid: str, side: str) -> np.ndarray:
        w = Window(*by_id[bid]["window"])
        h, wd = w.height + 2 * PAD, w.width + 2 * PAD
        xs, ys = origin(bid, side)
        m = mosaics[side]
        out = np.zeros((h, wd, 3), np.uint8)
        sy, sx = max(0, ys), max(0, xs)
        ey, ex = min(m.shape[0], ys + h), min(m.shape[1], xs + wd)
        out[sy - ys : ey - ys, sx - xs : ex - xs] = m[sy:ey, sx:ex]
        return stretch(out) if side == "post" else out

    def txt(job) -> Path:
        bid, side, p = job
        return d / "sam" / f"{bid}_{side}yard_{p.replace(' ', '_')}.txt"

    def fetch(job):
        bid, side, p = job
        img = image(bid, side)
        path = txt(job)
        if not path.exists():
            path.write_text(sam.request(img, p, SCALE))
        return img.shape[:2]

    jobs = [(r["id"], "post", p) for r in al["buildings"] for p in DEBRIS_PROMPTS + TARP_PROMPTS]
    jobs += [(r["id"], "pre", p) for r in al["buildings"] for p in DEBRIS_PROMPTS]
    with ThreadPoolExecutor(args.workers) as pool:
        shapes = list(pool.map(fetch, jobs))
    with ProcessPoolExecutor() as pool:
        unions = dict(zip(jobs, pool.map(masks_for, [txt(j) for j in jobs], shapes, chunksize=8)))
    print(f"{len(jobs)} SAM results ready (cached calls skipped)")

    out = []
    for r in al["buildings"]:
        bid = r["id"]
        w = Window(*r["window"])
        ring_pts = [tuple(p) for p in feats[bid]["geometry"]["coordinates"][0][:-1]]
        pts = [(x + PAD, y + PAD) for x, y in outline_to_window_pixels(ring_pts, zoom, w)]
        shape = (w.height + 2 * PAD, w.width + 2 * PAD)
        house = polygon_mask(pts, shape, dilate_px=2)
        yard = polygon_mask(pts, shape, dilate_px=RING_PX) & ~house
        post = np.zeros(shape, bool)
        pre = np.zeros(shape, bool)
        for p in DEBRIS_PROMPTS:
            post |= unions[(bid, "post", p)]
            pre |= unions[(bid, "pre", p)]
        # pre is in unshifted window pixels, post is shifted by the alignment;
        # the ring is drawn in window pixels and the post crop was cut so that
        # the house sits at the same place, so both line up with the ring.
        new_debris = post & ~pre
        tarp = np.zeros(shape, bool)
        for p in TARP_PROMPTS:
            tarp |= unions[(bid, "post", p)]
        out.append(
            dict(
                id=bid,
                debris_share=round(float((new_debris & (yard | house)).sum() / max(1, (yard | house).sum())), 3),
                debris_on_house=round(float((new_debris & house).sum() / max(1, house.sum())), 3),
                pre_debris=round(float((pre & yard).sum() / max(1, yard.sum())), 3),
                tarp_share=round(float((tarp & house).sum() / max(1, house.sum())), 3),
            )
        )
    (d / "debris.json").write_text(json.dumps(out, indent=1))
    hits = [x for x in out if x["debris_share"] > 0.02]
    print(f"debris around {len(hits)}/{len(out)} buildings:", [(x["id"], x["debris_share"]) for x in hits])
    print("tarps:", [(x["id"], x["tarp_share"]) for x in out if x["tarp_share"] > 0])


if __name__ == "__main__":
    main()
