"""Score the agent's drafts against the hand labels.

    python scripts/score_eval.py --area cedar_key

Scores against every labeler's file (eval/<area>_labels.json for Claude,
eval/<area>_labels_<name>.json for others), and reports how often the
labelers agree with each other, which is the ceiling the agent can reach.
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
    files = sorted(args.labels.glob(f"{args.area}_labels*.json"))
    sets = {f.stem.removeprefix(f"{args.area}_labels").lstrip("_") or "claude": json.loads(f.read_text())["labels"] for f in files}
    drafts = {a["id"]: a for a in json.loads((args.out / args.area / "assessments.json").read_text())}
    flood_path = args.out / args.area / "flood.json"
    flood = {b["id"]: b["flood_ft"] or 0 for b in json.loads(flood_path.read_text())["buildings"]} if flood_path.exists() else {}
    if len(sets) > 1:
        agreement(sets)
    for name, labels in sets.items():
        print()
        score(name, labels, drafts, flood)


def agreement(sets: dict) -> None:
    names = list(sets)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            ids = sorted(set(sets[a]) & set(sets[b]))
            pairs = [(sets[a][k]["category"], sets[b][k]["category"]) for k in ids]
            n = len(pairs)
            po = sum(x == y for x, y in pairs) / n
            pe = sum(sum(x == c for x, _ in pairs) * sum(y == c for _, y in pairs) for c in ORDER) / n**2
            kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0
            near = sum(abs(ORDER.index(x) - ORDER.index(y)) <= 1 for x, y in pairs)
            print(f"== labeler agreement, {a} vs {b}")
            print(f"exact: {round(po * n)}/{n} (Cohen's kappa {kappa:.2f}); within one step: {near}/{n}")
            for k, (x, y) in zip(ids, pairs):
                if x != y:
                    print(f"  {k}: {a} {x}, {b} {y}")


def score(name: str, labels: dict, drafts: dict, flood: dict) -> None:
    report(labels, drafts, f"{name}'s aerial labels")
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
        report(adjusted, drafts, f"{name}'s aerial labels + flood rule (water >= 0.5 ft around the house -> at least Affected)")


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
