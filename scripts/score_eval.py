"""Score the agent's drafts against the hand labels.

    python scripts/score_eval.py --area cedar_key
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ORDER = ["none", "affected", "minor", "major", "destroyed"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", default="cedar_key")
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--labels", type=Path, default=Path("eval"))
    args = ap.parse_args()
    labels = json.loads((args.labels / f"{args.area}_labels.json").read_text())["labels"]
    drafts = {a["id"]: a for a in json.loads((args.out / args.area / "assessments.json").read_text())}
    flood_path = args.out / args.area / "flood.json"
    flood = {b["id"]: b["flood_ft"] or 0 for b in json.loads(flood_path.read_text())["buildings"]} if flood_path.exists() else {}
    report(labels, drafts, "aerial labels")
    if flood:
        # The labels see only the roof and yard. Where surveyed water stood
        # 0.5 ft or more around the house, the PDA rule says at least
        # Affected even if the photo looks clean (water under or in the house).
        adjusted = {}
        for bid, lab in labels.items():
            cat = lab["category"]
            if flood.get(bid, 0) >= 0.5 and ORDER.index(cat) < ORDER.index("affected"):
                cat = "affected"
            adjusted[bid] = {**lab, "category": cat}
        print()
        report(adjusted, drafts, "aerial labels + flood rule (water >= 0.5 ft around the house -> at least Affected)")


def report(labels: dict, drafts: dict, title: str) -> None:
    print(f"== {title}")
    rows = []
    for bid, lab in sorted(labels.items()):
        a = drafts.get(bid, {})
        rows.append((bid, lab["category"], a.get("category", "missing"), a.get("needs_human", False), lab.get("unsure", False)))

    n = len(rows)
    exact = sum(t == p for _, t, p, _, _ in rows)
    near = sum(p in ORDER and abs(ORDER.index(t) - ORDER.index(p)) <= 1 for _, t, p, _, _ in rows)
    sure = [r for r in rows if not r[4]]
    print(f"{n} labeled buildings ({len(sure)} the labeler is sure of)")
    print(f"exact category: {exact}/{n}; within one step: {near}/{n}")
    print(f"exact on sure labels: {sum(t == p for _, t, p, _, _ in sure)}/{len(sure)}")
    td = {b for b, t, *_ in rows if t == "destroyed"}
    pd = {b for b, _, p, *_ in rows if p == "destroyed"}
    print(f"destroyed: {len(td & pd)}/{len(td)} found, {len(pd - td)} false")
    print("confusion (rows = label, cols = agent):")
    print("            " + " ".join(f"{c[:5]:>6}" for c in ORDER))
    for t in ORDER:
        print(f"{t:>11} " + " ".join(f"{sum(1 for _, tt, p, _, _ in rows if tt == t and p == c):>6}" for c in ORDER))
    print("disagreements:")
    for bid, t, p, review, unsure in rows:
        if t != p:
            print(f"  {bid}: label {t}{' (unsure)' if unsure else ''}, agent {p}{' [sent to review]' if review else ''}")
    flagged_wrong = sum(1 for _, t, p, review, _ in rows if t != p and review)
    print(f"{flagged_wrong} of {n - exact} disagreements were already sent to human review")



if __name__ == "__main__":
    main()
