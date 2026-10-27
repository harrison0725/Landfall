"""Contact sheets for eyeballing alignment: pre crop with the outline (left),
post crop placed by the aligner with the same outline (right).

    python scripts/review_sheet.py --area cedar_key
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from landfall.geo import lonlat_to_pixel  # noqa: E402

COLORS = {"matched": (40, 220, 90), "neighbors": (255, 170, 0), "unaligned": (255, 60, 60)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", default="cedar_key")
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--per-sheet", type=int, default=20)
    ap.add_argument("--cell", type=int, default=150)
    args = ap.parse_args()
    d = args.out / args.area
    al = json.loads((d / "alignment.json").read_text())
    zoom = al["summary"]["zoom"]
    feats = {f["properties"]["id"]: f for f in json.loads((d / "buildings.geojson").read_text())["features"]}
    cols, cs = 4, args.cell
    cells = []
    for r in al["buildings"]:
        x0, y0 = r["window"][:2]
        ring = feats[r["id"]]["geometry"]["coordinates"][0]
        pts = [(x - x0, y - y0) for x, y in (lonlat_to_pixel(lon, lat, zoom) for lon, lat in ring)]
        pre = Image.open(d / "crops" / f"{r['id']}_pre.png").convert("RGB")
        post = ImageOps.autocontrast(Image.open(d / "crops" / f"{r['id']}_post.png").convert("RGB"), cutoff=1)
        pair = []
        for im in (pre, post):
            s = cs / max(im.size)
            im = im.resize((round(im.width * s), round(im.height * s)))
            ImageDraw.Draw(im).polygon([(x * s, y * s) for x, y in pts], outline=(255, 255, 0))
            pair.append(im)
        cell = Image.new("RGB", (2 * cs + 4, cs + 16), (255, 255, 255))
        cell.paste(pair[0], (0, 16))
        cell.paste(pair[1], (cs + 4, 16))
        ImageDraw.Draw(cell).text((2, 2), f"{r['id']} {r['status']}", fill=COLORS[r["status"]] if r["status"] != "matched" else (0, 140, 40))
        cells.append(cell)
    sheets = d / "review"
    sheets.mkdir(exist_ok=True)
    cw, ch = cells[0].width + 6, cs + 22
    for n in range(0, len(cells), args.per_sheet):
        batch = cells[n : n + args.per_sheet]
        rows = (len(batch) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * cw, rows * ch), (255, 255, 255))
        for i, c in enumerate(batch):
            sheet.paste(c, ((i % cols) * cw, (i // cols) * ch))
        sheet.save(sheets / f"sheet_{n // args.per_sheet:02d}.png")
    print(f"wrote {(len(cells) + args.per_sheet - 1) // args.per_sheet} sheets to {sheets}")


if __name__ == "__main__":
    main()
