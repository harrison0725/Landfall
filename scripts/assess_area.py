"""Draft a FEMA category for every building with the agent loop.

    python scripts/assess_area.py --area cedar_key [--only b023,b094] [--limit 20]

Buildings are assessed worst-measured first, so a partial run still covers
the houses that matter most. Writes data/<area>/assessments.json.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from landfall.agent import assess  # noqa: E402
from landfall.fema import Measures  # noqa: E402
from landfall.geo import Window, outline_to_window_pixels  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", default="cedar_key")
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--no-escalate", action="store_true")
    args = ap.parse_args()
    d = args.out / args.area
    al = json.loads((d / "alignment.json").read_text())
    zoom = al["summary"]["zoom"]
    windows = {r["id"]: Window(*r["window"]) for r in al["buildings"]}
    feats = {f["properties"]["id"]: f for f in json.loads((d / "buildings.geojson").read_text())["features"]}
    damage = json.loads((d / "damage.json").read_text())
    flood_path = d / "flood.json"
    flood = {b["id"]: b["flood_ft"] for b in json.loads(flood_path.read_text())["buildings"]} if flood_path.exists() else {}

    order = sorted(damage, key=lambda r: -1 if r["structure_loss"] is None else -r["structure_loss"])
    if args.only:
        keep = set(args.only.split(","))
        order = [r for r in order if r["id"] in keep]
    if args.limit:
        order = order[: args.limit]

    def run(r: dict):
        bid = r["id"]
        loss = r["structure_loss"]
        m = Measures(roof_loss=loss or 0.0, tarp_share=0.0, debris_share=0.0, flood_ft=flood.get(bid), aligned=loss is not None)
        pre = np.asarray(Image.open(d / "crops" / f"{bid}_pre.png").convert("RGB"))
        post = np.asarray(Image.open(d / "crops" / f"{bid}_post.png").convert("RGB"))
        ring = [tuple(p) for p in feats[bid]["geometry"]["coordinates"][0][:-1]]
        outline = outline_to_window_pixels(ring, zoom, windows[bid])
        try:
            a = assess(bid, pre, post, outline, m, r, escalate=not args.no_escalate)
        except Exception as e:  # keep going; one bad building should not stop the area
            print(f"{bid}: failed: {e}")
            return dict(id=bid, error=str(e))
        print(f"{bid}: {a.category.label} ({a.confidence:.2f}, {a.decided_by}){' [review]' if a.needs_human else ''}: {a.evidence}")
        return dict(
            id=bid,
            category=a.category.name.lower(),
            confidence=a.confidence,
            evidence=a.evidence,
            needs_human=a.needs_human,
            decided_by=a.decided_by,
            verified=a.verified,
            structure_loss=loss,
            flood_ft=flood.get(bid),
            trace=a.trace,
        )

    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(run, order))
    (d / "assessments.json").write_text(json.dumps(results, indent=1))
    print(collections.Counter(r.get("category", "error") for r in results))


if __name__ == "__main__":
    main()
