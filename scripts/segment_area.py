"""Run SAM 3.1 concept prompts on every building's pre and post crop and
measure how much of the pre-storm structure is still there.

    python scripts/segment_area.py --area cedar_key

One prompt alone misses too much in the dark post-storm flight ("roof" missed
about one roof in six that "house" or "building" found), so the structure is
the union of all three. The post-storm side is segmented on a wider window
and matched by shape, which absorbs the few meters the alignment can be off.

Raw SAM output lines are cached under data/<area>/sam/ so reruns are free.
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

from landfall import sam  # noqa: E402
from landfall.geo import Window, outline_to_window_pixels  # noqa: E402
from landfall.measure import best_overlap, coverage, polygon_mask, structure_mask  # noqa: E402

SCALE = 4
PAD = 16  # px (~4 m) the post-storm house may sit from where alignment put it


def stretch(img: np.ndarray, low: float = 1.0, high: float = 99.0) -> np.ndarray:
    """Per-crop contrast stretch. The post-storm flight is underexposed, and
    SAM misses white roofs sitting in near-black surroundings without it."""
    lo, hi = np.percentile(img, [low, high])
    if hi <= lo:
        return img
    return np.clip((img.astype(np.float32) - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)


def cached_line(cache: Path, img: np.ndarray, prompt: str) -> str:
    if cache.exists():
        return cache.read_text()
    text = sam.request(img, prompt, SCALE)
    cache.write_text(text)
    return text


def cached_masks(txt: Path, shape: tuple[int, int]) -> list[np.ndarray]:
    """Decoded masks for a cached SAM line. Decoding is pure Python and slow
    (~1 s per line), so the result is stored next to the line."""
    npz = txt.with_suffix(".npz")
    if npz.exists():
        return list(np.load(npz)["masks"])
    masks = [x.mask for x in sam.parse(txt.read_text(), shape, SCALE)]
    np.savez_compressed(npz, masks=np.array(masks, dtype=bool).reshape(len(masks), *shape))
    return masks


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", default="cedar_key")
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--prompts", nargs="+", default=["roof", "house", "building"])
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    d = args.out / args.area
    al = json.loads((d / "alignment.json").read_text())
    zoom = al["summary"]["zoom"]
    feats = {f["properties"]["id"]: f for f in json.loads((d / "buildings.geojson").read_text())["features"]}
    (d / "sam").mkdir(exist_ok=True)

    x0, y0 = al["summary"]["mosaic_origin"]
    post_mosaic = np.asarray(Image.open(d / "post.png").convert("RGB"))

    def image(bid: str, side: str) -> np.ndarray:
        if side == "pre":
            return np.asarray(Image.open(d / "crops" / f"{bid}_pre.png").convert("RGB"))
        r = by_id[bid]
        w = Window(*r["window"])
        ys = w.y0 - y0 + r["dy"] - PAD
        xs = w.x0 - x0 + r["dx"] - PAD
        return post_mosaic[ys : ys + w.height + 2 * PAD, xs : xs + w.width + 2 * PAD]

    def txt(job) -> Path:
        bid, side, p = job
        name = "pre" if side == "pre" else "postctx"
        return d / "sam" / f"{bid}_{name}_{p.replace(' ', '_')}_s.txt"

    def fetch(job):
        bid, side, p = job
        img = stretch(image(bid, side))
        cached_line(txt(job), img, p)
        return img.shape[:2]

    by_id = {r["id"]: r for r in al["buildings"]}
    jobs = [(r["id"], side, p) for r in al["buildings"] for side in ("pre", "post") for p in args.prompts]
    with ThreadPoolExecutor(args.workers) as pool:
        shapes = list(pool.map(fetch, jobs))
    with ProcessPoolExecutor() as pool:
        masks = list(pool.map(cached_masks, [txt(j) for j in jobs], shapes, chunksize=8))
    dets = dict(zip(jobs, masks))
    print(f"{len(jobs)} SAM results ready (cached calls skipped)")

    out = []
    for r in al["buildings"]:
        bid = r["id"]
        w = Window(*r["window"])
        ring = [tuple(p) for p in feats[bid]["geometry"]["coordinates"][0][:-1]]
        pts = outline_to_window_pixels(ring, zoom, w)
        fp = polygon_mask(pts, (w.height, w.width), dilate_px=2)
        pre_masks = [m for p in args.prompts for m in dets[(bid, "pre", p)]]
        post_masks = [m for p in args.prompts for m in dets[(bid, "post", p)]]
        pre = structure_mask(pre_masks, fp) & polygon_mask(pts, fp.shape, dilate_px=6)
        post_ctx = np.zeros((w.height + 2 * PAD, w.width + 2 * PAD), bool)
        for m in post_masks:
            post_ctx |= m
        cover = coverage([pre], fp)
        kept, sdx, sdy = best_overlap(pre, post_ctx, PAD)
        rec = dict(id=bid, status=r["status"], pre_cover=round(cover, 3))
        if cover < 0.3:
            rec.update(structure_loss=None, note="pre-storm structure not found on the outline")
        else:
            rec.update(structure_loss=round(1 - kept, 3), shift=[sdx, sdy])
        out.append(rec)
    (d / "damage.json").write_text(json.dumps(out, indent=1))
    measured = [x for x in out if x["structure_loss"] is not None]
    print(f"structure loss measured for {len(measured)}/{len(out)} buildings")
    for lo, hi in ((0.6, 1.01), (0.25, 0.6)):
        print(f"loss {lo:.0%}+:" if hi > 1 else f"loss {lo:.0%}-{hi:.0%}:", [x["id"] for x in measured if lo <= x["structure_loss"] < hi])


if __name__ == "__main__":
    main()
