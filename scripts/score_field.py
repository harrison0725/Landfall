"""Score the agent's drafts against field-validated damage surveys.

    python scripts/score_field.py --area cedar_key [--drafts demo/cedar_key/assessments.json]

Ground truth is in eval/field/ (see eval/field/README.md):

* the Oregon State / URI virtual damage assessment of 771 Cedar Key buildings
  after Hurricane Helene (DesignSafe PRJ-6245), rated per component (roof,
  walls and elevated floors, foundation, openings, attached structures) on
  the Tomiczek DS0-DS6 scale from street-level and aerial photos, and
* the subset of those buildings that StEER engineers rated in the field
  (DS 0-4), which the virtual assessment was validated against.

Each survey point is matched to the agent's building outline it falls in
(or the nearest one within 3 m). When several points land on one outline
(units of a condo or motel), the worst rating counts, as for the building's
overall rating.

The scale has more steps than FEMA's categories, so the comparison maps
DS ratings to FEMA categories with an explicit table (MAPPING below), and
reports a second table where partial collapse (DS5) counts as Destroyed.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

ORDER = ["none", "affected", "minor", "major", "destroyed"]

# Tomiczek et al. DS (overall = worst component) -> FEMA PDA category.
# DS0 no damage; DS1 minor non-structural (cosmetic); DS2-3 moderate to
# severe non-structural, repairable; DS4 structural damage; DS5 partial
# collapse; DS6 collapse / washed away.
MAPPING = {0: "none", 1: "affected", 2: "minor", 3: "minor", 4: "major", 5: "major", 6: "destroyed"}
MAPPING_DS5_DESTROYED = {**MAPPING, 5: "destroyed"}

# StEER field rating (0-4), as published with the validation set:
# 0 negligible to slight, 1 moderate, 2 substantial to heavy,
# 3 very heavy, 4 destruction. FEMA categories that fit each step:
FIELD_FITS = {0: {"none", "affected"}, 1: {"minor"}, 2: {"major"}, 3: {"major", "destroyed"}, 4: {"destroyed"}}

COMPONENTS = {
    "DS_roof": "roof",
    "DS_walls/floors": "walls / elevated floors",
    "DS_foundation": "foundation",
    "DS_openings": "windows and doors",
    "DS_attached/detached_structures": "decks, porches, sheds",
}
MATCH_M = 3.0
M_PER_DEG = 105_000  # rough, at 29 N; only used for the 3 m match radius


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", default="cedar_key")
    ap.add_argument("--field", type=Path, default=Path("eval/field"))
    ap.add_argument("--buildings", type=Path, default=None, help="default demo/<area>/buildings.geojson")
    ap.add_argument("--drafts", type=Path, action="append", default=None,
                    help="assessments.json to score (repeatable; default demo/<area>/assessments.json)")
    ap.add_argument("--json", type=Path, default=None, help="also write the matched table here")
    args = ap.parse_args()

    buildings = args.buildings or Path("demo") / args.area / "buildings.geojson"
    survey = load_survey(args.field)
    matched = match(survey, buildings)
    print("Field-validated survey vs. Landfall drafts, Cedar Key, Hurricane Helene")
    print(f"survey: {len(survey)} rated buildings; {len(matched)} of the agent's outlines have a rating within {MATCH_M:.0f} m")
    multi = sum(1 for m in matched.values() if len(m["fids"]) > 1)
    print(f"{multi} outlines hold more than one rated unit (worst rating used)")
    print("survey ratings on the matched outlines (overall DS): " + ", ".join(
        f"DS{k}: {v}" for k, v in sorted(Counter(m["DS_overall"] for m in matched.values()).items())))

    for path in args.drafts or [Path("demo") / args.area / "assessments.json"]:
        drafts = {a["id"]: a for a in json.loads(path.read_text())}
        print()
        print("#" * 72)
        print(f"# drafts: {path}")
        print("#" * 72)
        score(matched, drafts, MAPPING, "overall DS -> FEMA (DS5 partial collapse = Major)")
        score(matched, drafts, MAPPING, "same, with No visible damage and Affected merged (the survey rates the "
              "structure; Affected is mostly water around the house, which it doesn't record)", merge=True)
        score(matched, drafts, MAPPING_DS5_DESTROYED, "overall DS -> FEMA (DS5 partial collapse = Destroyed)", brief=True)
        roof_only(matched, drafts)
        misses(matched, drafts)
        field_subset(matched, drafts)
        flood_report(matched, drafts)

    if args.json:
        args.json.write_text(json.dumps(matched, indent=1))


def load_survey(field: Path) -> list[dict]:
    vda = list(csv.DictReader((field / "HxHelene_VDA_CedarKey.csv").open(encoding="utf-8-sig")))
    steer = {}
    with (field / "HBCK_field_vda_comp.csv").open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            r = {k.strip(): v.strip() for k, v in r.items() if k}
            if r.get("HB_CK") == "CK":
                steer[r["NSI_fd_id"]] = {"address": r["Address"], "DS_field": int(r["DS_field"])}
    rows = []
    for r in vda:
        ds = {c: num(r.get(c)) for c in ["DS_overall", *COMPONENTS]}
        if ds["DS_overall"] < 0:
            continue
        rows.append({
            "fid": r["FID"], "fd_id": r["fd_id"], "lon": float(r["x"]), "lat": float(r["y"]),
            "visible_street": r["building visible in streetview? (yes/no)"].strip().lower() == "yes",
            "elevated": r["FFE_elevation_description"], "foundation": r["foundation_type"],
            "note": r["notes"], **ds, "steer": steer.get(r["fd_id"]),
            "first_floor_ft": first_floor(r),
        })
    return rows


def first_floor(r: dict) -> float | None:
    """The survey's first-floor height above grade (ft), measured on street photos."""
    try:
        ffe, grade = float(r["FFE_NAVD88_ft"]), float(r["grd_el_ft"])
    except (TypeError, ValueError, KeyError):
        return None
    if ffe != ffe or grade != grade:
        return None
    return max(0.0, ffe - grade)


def num(x: str | None) -> int:
    """A DS rating, or -1 where the surveyors could not rate that part."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return -1
    return -1 if v != v else int(v)


def match(survey: list[dict], buildings_path: Path) -> dict:
    from shapely.geometry import Point, shape
    from shapely.strtree import STRtree

    feats = json.loads(buildings_path.read_text())["features"]
    polys = [shape(f["geometry"]) for f in feats]
    ids = [f["properties"]["id"] for f in feats]
    tree = STRtree(polys)
    out: dict[str, dict] = {}
    for s in survey:
        pt = Point(s["lon"], s["lat"])
        i = tree.nearest(pt)
        if polys[i].distance(pt) * M_PER_DEG > MATCH_M:
            continue
        bid = ids[i]
        cur = out.get(bid)
        if cur is None:
            out[bid] = {**s, "fids": [s["fid"]]}
            continue
        cur["fids"].append(s["fid"])
        for c in ["DS_overall", *COMPONENTS]:
            cur[c] = max(cur[c], s[c])
        cur["visible_street"] = cur["visible_street"] or s["visible_street"]
        if s["first_floor_ft"] is not None:
            # the lowest living floor among the units takes water first
            cur["first_floor_ft"] = min(s["first_floor_ft"], cur["first_floor_ft"] if cur["first_floor_ft"] is not None else 99)
        cur["steer"] = cur["steer"] or s["steer"]
    return out


def qwk(pairs: list[tuple[int, int]], k: int = 5) -> float:
    """Quadratic-weighted Cohen's kappa on ordinal indices 0..k-1."""
    n = len(pairs)
    if not n:
        return float("nan")
    obs = [[0] * k for _ in range(k)]
    for a, b in pairs:
        obs[a][b] += 1
    ra = [sum(r) for r in obs]
    cb = [sum(obs[i][j] for i in range(k)) for j in range(k)]
    num = den = 0.0
    for i in range(k):
        for j in range(k):
            w = (i - j) ** 2 / (k - 1) ** 2
            num += w * obs[i][j]
            den += w * ra[i] * cb[j] / n
    return 1 - num / den if den else 1.0


def table(rows: list[tuple[str, str]], label: str) -> None:
    print(f"confusion (rows = {label}, cols = agent):")
    print("            " + " ".join(f"{c[:5]:>6}" for c in ORDER))
    for t in ORDER:
        print(f"{t:>11} " + " ".join(f"{sum(1 for tt, p in rows if tt == t and p == c):>6}" for c in ORDER))


def score(matched: dict, drafts: dict, mapping: dict, title: str, brief: bool = False, merge: bool = False) -> None:
    rows = [(mapping[m["DS_overall"]], drafts[b]["category"]) for b, m in sorted(matched.items()) if b in drafts]
    if merge:
        rows = [tuple("none" if c == "affected" else c for c in r) for r in rows]
    n = len(rows)
    idx = [(ORDER.index(t), ORDER.index(p)) for t, p in rows]
    exact = sum(t == p for t, p in idx)
    near = sum(abs(t - p) <= 1 for t, p in idx)
    print()
    print(f"== {title}")
    print(f"{n} buildings: exact {exact}/{n} ({exact / n:.0%}); within one step {near}/{n} ({near / n:.0%}); "
          f"quadratic-weighted kappa {qwk(idx):.2f}")
    if brief:
        return
    table(rows, "survey")
    for name, lo in [("damaged (Minor or worse)", 2), ("severe (Major or worse)", 3), ("destroyed", 4)]:
        truth = {i for i, (t, _) in enumerate(idx) if t >= lo}
        pred = {i for i, (_, p) in enumerate(idx) if p >= lo}
        tp = len(truth & pred)
        rec = f"{tp}/{len(truth)} ({tp / len(truth):.0%})" if truth else "n/a"
        prec = f"{tp}/{len(pred)} ({tp / len(pred):.0%})" if pred else "n/a"
        print(f"  {name}: found {rec}; agent's calls right {prec}")
    under = sum(p < t for t, p in idx)
    over = sum(p > t for t, p in idx)
    print(f"  agent lower than survey: {under}; higher: {over}")
    review = [b for b, m in matched.items() if b in drafts and drafts[b].get("needs_human")]
    wrong_reviewed = sum(1 for b in review if mapping[matched[b]["DS_overall"]] != drafts[b]["category"])
    print(f"  {len(review)} sent to human review, of which {wrong_reviewed} disagree with the survey")


def roof_only(matched: dict, drafts: dict) -> None:
    """The agent sees roofs. Score it against the survey's roof rating alone."""
    rows = [(MAPPING[m["DS_roof"]], drafts[b]["category"]) for b, m in sorted(matched.items())
            if b in drafts and m["DS_roof"] >= 0]
    idx = [(ORDER.index(t), ORDER.index(p)) for t, p in rows]
    n = len(rows)
    exact = sum(t == p for t, p in idx)
    near = sum(abs(t - p) <= 1 for t, p in idx)
    print()
    print("== roof rating only (what a photo from above can see), same mapping")
    print(f"{n} buildings: exact {exact}/{n} ({exact / n:.0%}); within one step {near}/{n} ({near / n:.0%}); "
          f"quadratic-weighted kappa {qwk(idx):.2f}")


def misses(matched: dict, drafts: dict) -> None:
    """Where the survey found Minor-or-worse damage and the agent did not: which part was damaged?"""
    missed = []
    for b, m in sorted(matched.items()):
        if b not in drafts:
            continue
        t = ORDER.index(MAPPING[m["DS_overall"]])
        p = ORDER.index(drafts[b]["category"])
        if t >= 2 and p < t:
            missed.append((b, m, drafts[b]))
    print()
    print(f"== what the agent rated lower than the survey, where the survey says Minor or worse: {len(missed)} buildings")
    drivers = Counter()
    roof_ok = 0
    for _, m, _ in missed:
        worst = m["DS_overall"]
        for c, name in COMPONENTS.items():
            if m[c] == worst:
                drivers[name] += 1
        if 0 <= m["DS_roof"] <= 1:
            roof_ok += 1
    print(f"  {roof_ok} of {len(missed)} have a roof the survey itself rates Affected or better: "
          "the damage is below the roofline")
    print("  component(s) carrying the worst rating (a building can count twice):")
    for name, k in drivers.most_common():
        print(f"    {name}: {k}")
    flooded = sum(1 for _, _, a in missed if (a.get("flood_ft") or 0) >= 0.5)
    print(f"  {flooded} of {len(missed)} had water 0.5 ft or more around them in the agent's flood estimate")
    no_street = sum(1 for _, m, _ in missed if not m["visible_street"])
    print(f"  {no_street} of {len(missed)} were not visible in street-level photos to the surveyors")
    print("  buildings (survey overall / roof / walls / foundation / openings / attached -> agent):")
    for b, m, a in missed:
        ds = "/".join(str(m[c]) for c in ["DS_overall", *COMPONENTS])
        print(f"    {b}: DS {ds} ({MAPPING[m['DS_overall']]}) -> agent {a['category']}"
              f"{' [review]' if a.get('needs_human') else ''}; flood {a.get('flood_ft') or 0:.1f} ft"
              f"; {m['note'][:110].strip()}")


def field_subset(matched: dict, drafts: dict) -> None:
    hits = [(b, m) for b, m in sorted(matched.items()) if m.get("steer") and b in drafts]
    print()
    print(f"== StEER field ratings (0-4) on the agent's outlines: {len(hits)} buildings")
    ok = 0
    for b, m in hits:
        f = m["steer"]["DS_field"]
        cat = drafts[b]["category"]
        fit = cat in FIELD_FITS[f]
        ok += fit
        print(f"  {b} {m['steer']['address']}: field {f} (fits {'/'.join(sorted(FIELD_FITS[f]))}), "
              f"virtual DS{m['DS_overall']}, agent {cat}{'' if fit else '  <- differs'}")
    if hits:
        print(f"  agent fits the field rating on {ok}/{len(hits)}")


def flood_report(matched: dict, drafts: dict) -> None:
    """How well the agent's flood note says whether water got inside.

    Flooding is stated beside the category, never counted in it, so it is
    scored on its own: the survey's first-floor heights (measured on street
    photos) with the agent's flood depth say whether water reached the living
    space; the agent's note says the same from its own first-floor reading.
    """
    import statistics

    rows = []
    for b, a in drafts.items():
        m = matched.get(b)
        ff = m.get("first_floor_ft") if m else None
        if ff is None or a.get("flood_ft") is None:
            continue
        truth = a["flood_ft"] - ff > 0
        note = a.get("flood") or {}
        said = None if note.get("living_ft") is None else note["living_ft"] > 0
        rows.append((b, truth, said, a.get("first_floor_ft"), ff))
    print()
    print("== flooding (stated beside the category): water in the living space, by the survey's first floors")
    inside = [r for r in rows if r[1]]
    print(f"{len(rows)} matched buildings with a surveyed first floor; water reached the living space at {len(inside)}")
    told = [r for r in rows if r[2] is not None]
    if not told:
        print("  the drafts have no first-floor reading (no street photos), so the note can't say whether water got inside")
        return
    hit = sum(r[1] and r[2] for r in told)
    false = sum(r[2] and not r[1] for r in told)
    print(f"  agent's note says inside on {sum(r[2] for r in told)}: {hit} right, {false} where the survey's floor is above the water")
    print(f"  of the {len(inside)} with water inside, the note says so on {hit}; "
          f"{sum(r[1] and r[2] is None for r in rows)} have no first-floor reading")
    errs = [r[3] - r[4] for r in rows if r[3] is not None]
    if errs:
        print(f"  agent's first-floor reading vs the survey's: median error {statistics.median(abs(e) for e in errs):.1f} ft, "
              f"{sum(abs(e) <= 1 for e in errs)} of {len(errs)} within 1 ft, mean {statistics.mean(errs):+.1f} ft")


if __name__ == "__main__":
    main()
