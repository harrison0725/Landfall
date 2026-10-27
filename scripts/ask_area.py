"""Ask the area agent a question, or have it draft the PDA summary.

    python scripts/ask_area.py --area cedar_key "Which houses should a team check first?"
    python scripts/ask_area.py --area cedar_key --pda      # writes data/<area>/pda_summary.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from landfall.area import PDA_REQUEST, Area, ask, unknown_ids  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="?")
    ap.add_argument("--area", default="cedar_key")
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--pda", action="store_true", help="draft the PDA summary")
    ap.add_argument("--spark", action="store_true", help="use Muse Spark instead of Muse Spark Open")
    args = ap.parse_args()
    if not args.question and not args.pda:
        ap.error("give a question or --pda")
    area = Area.load(args.out, args.area)
    a = ask(area, PDA_REQUEST if args.pda else args.question, use_spark=args.spark)
    for s in a.steps:
        print(f"[tool] {s['tool']}({json.dumps(s['args'])})", file=sys.stderr)
    print(a.text)
    bad = unknown_ids(area, a.text)
    if bad:
        print(f"\nWARNING: the answer names buildings that don't exist: {', '.join(bad)}", file=sys.stderr)
    if args.pda:
        path = args.out / args.area / "pda_summary.md"
        path.write_text(a.text + "\n")
        (args.out / args.area / "pda_summary.trace.json").write_text(json.dumps({"model": a.model, "steps": a.steps}, indent=1))
        print(f"\nwrote {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
