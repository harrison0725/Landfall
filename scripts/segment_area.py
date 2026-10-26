"""Run SAM 3.1 concept prompts on every building's pre and post crop and
measure roof loss over the building outline.

    python scripts/segment_area.py --area cedar_key --prompts roof

Raw SAM output lines are cached under data/<area>/sam/ so reruns are free.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from landfall import sam  # noqa: E402
from landfall.geo import Window, outline_to_window_pixels  # noqa: E402
from landfall.measure import coverage, polygon_mask, roof_loss  # noqa: E402

SCALE = 4


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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", default="cedar_key")
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--prompts", nargs="+", default=["roof"])
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    d = args.out / args.area
    al = json.loads((d / "alignment.json").read_text())
    zoom = al["summary"]["zoom"]
    feats = {f["properties"]["id"]: f for f in json.loads((d / "buildings.geojson").read_text())["features"]}
    (d / "sam").mkdir(exist_ok=True)

    jobs = []
    for r in al["buildings"]:
        for side in ("pre", "post"):
            for p in args.prompts:
                jobs.append((r["id"], side, p))

    def run(job):
        bid, side, p = job
        img = stretch(np.asarray(Image.open(d / "crops" / f"{bid}_{side}.png").convert("RGB")))
        line = cached_line(d / "sam" / f"{bid}_{side}_{p.replace(' ', '_')}_s.txt", img, p)
        return job, sam.parse(line, img.shape[:2], SCALE)

    with ThreadPoolExecutor(args.workers) as pool:
        dets = dict(pool.map(run, jobs))
    print(f"{len(jobs)} SAM calls done (cached ones skipped)")

    out = []
    for r in al["buildings"]:
        bid = r["id"]
        w = Window(*r["window"])
        ring = [tuple(p) for p in feats[bid]["geometry"]["coordinates"][0][:-1]]
        fp = polygon_mask(outline_to_window_pixels(ring, zoom, w), (w.height, w.width), dilate_px=2)
        rec = dict(id=bid, status=r["status"])
        for p in args.prompts:
            pre = coverage([x.mask for x in dets[(bid, "pre", p)]], fp)
            post = coverage([x.mask for x in dets[(bid, "post", p)]], fp)
            rec[f"{p}_pre"] = round(pre, 3)
            rec[f"{p}_post"] = round(post, 3)
        if "roof" in args.prompts:
            loss = roof_loss(rec["roof_pre"], rec["roof_post"])
            rec["roof_loss"] = None if loss is None else round(loss, 3)
        out.append(rec)
    (d / "roof.json").write_text(json.dumps(out, indent=1))
    losses = [x["roof_loss"] for x in out if x.get("roof_loss") is not None]
    print(f"roof loss measured for {len(losses)}/{len(out)} buildings")
    print("buildings with >=25% roof loss:", [x["id"] for x in out if (x.get("roof_loss") or 0) >= 0.25])


if __name__ == "__main__":
    main()
