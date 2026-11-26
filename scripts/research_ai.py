"""How well Landfall's AI reads buildings, and what aerial photos miss, at
Cedar Key after Helene. Writes eval/research/ai_accuracy.json.

1. How the building is built. The agent's street-photo reads (walls,
   foundation, stories, first-floor height; demo/cedar_key/assessments.json)
   and the National Structure Inventory's (what Landfall uses without
   photos) against two references:
   * the PRJ-6245 assessors (post-storm imagery, field-validated), and
   * the University of Florida Envision Heritage survey of Cedar Key (2021,
     before the storm; walls, foundation, stories, laser-scanned first
     floors), an independent check that did not see the storm.
   Accuracy with 95% Wilson intervals and Cohen's kappa (agreement beyond
   chance).
2. What aerial photos miss. Of surveyed buildings that were damaged
   (walls/floors, foundation or roof at DS2+), how many had an undamaged
   roof, so that nothing shows from above; for Cedar Key, Horseshoe Beach and
   Fort Myers Beach. And how many damaged buildings the agent caught with
   aerial photos only vs aerial + street photos, split by whether the roof
   was damaged.

Run: python scripts/research_ai.py
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
OUT = ROOT / "eval" / "research"

MAT = {"W": "wood", "M": "masonry", "C": "concrete", "S": "steel", "H": "manufactured"}
FND = {"S": "slab", "C": "crawlspace", "P": "piers", "I": "piles", "F": "fill", "W": "solid wall", "B": "basement"}
NSI_MAT = {"wood frame": "wood", "masonry": "masonry", "concrete": "concrete", "steel": "steel", "manufactured (mobile) home": "manufactured"}
# foundation height groups: what matters for water is how high the floor sits
GROUP = {"slab": "at grade", "fill": "at grade", "basement": "at grade", "crawlspace": "short", "piers": "short", "piles": "tall", "solid wall": "tall"}
UF_MAT = {"wood frame": "wood", "masonry": "masonry"}
UF_FND = {"piers": "short", "slab on grade": "at grade", "slabe on grade": "at grade", "continuous": "short"}
AGENT_DAMAGED = {"minor", "major", "destroyed"}


def wilson(k: int, n: int) -> list[float]:
    if n == 0:
        return [float("nan")] * 2
    p, z = k / n, 1.96
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(c - h, 3), round(c + h, 3)]


def kappa(pairs: list[tuple]) -> float:
    n = len(pairs)
    if not n:
        return float("nan")
    po = sum(a == b for a, b in pairs) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(ca[k] * cb[k] for k in ca) / (n * n)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else float("nan")


def agree(pairs: list[tuple]) -> dict:
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    k = sum(a == b for a, b in pairs)
    return {"buildings": len(pairs), "right": k, "accuracy": round(k / max(len(pairs), 1), 3), "ci95": wilson(k, len(pairs)), "kappa": kappa(pairs),
            "most_common_mixups": [[a, b, n] for (a, b), n in Counter((a, b) for a, b in pairs if a != b).most_common(3)]}


def uf_lookup():
    from shapely.geometry import Point, shape
    from shapely.strtree import STRtree

    feats = json.loads((ROOT / "data" / "research_raw" / "uf_cedar_key_2021.json").read_text())["features"]
    feats = [f for f in feats if f["geometry"] and (f["properties"].get("Structural_Type") or f["properties"].get("Foundation_Type"))]
    geoms = [shape(f["geometry"]) for f in feats]
    tree = STRtree(geoms)

    def at(lon, lat):
        p = Point(lon, lat)
        hit = [i for i in tree.query(p) if geoms[i].contains(p)]
        return feats[hit[0]]["properties"] if hit else None

    return at


def built(matched: dict, agent: dict, nsi: dict, survey_rows: dict) -> dict:
    uf = uf_lookup()
    res = {}
    pairs = {(src, ref, k): [] for src in ("street AI", "structure inventory") for ref in ("assessors", "UF 2021") for k in ("walls", "foundation height", "stories")}
    floors = {"assessors": [], "UF 2021": []}
    for bid, s in matched.items():
        r = survey_rows[s["fids"][0]]
        a = (agent.get(bid) or {}).get("built_from_street") or {}
        n = nsi.get(bid) or {}
        srcs = {"street AI": {"walls": a.get("material"), "foundation height": GROUP.get(a.get("foundation")), "stories": a.get("stories")},
                "structure inventory": {"walls": NSI_MAT.get(n.get("material")), "foundation height": GROUP.get(n.get("foundation")), "stories": n.get("stories")}}
        try:
            st = int(float(r["num_stories"]))
        except (TypeError, ValueError):
            st = None
        refs = {"assessors": {"walls": MAT.get(r["building_type"].strip()), "foundation height": GROUP.get(FND.get(r["foundation_type"].strip())), "stories": st}}
        u = uf(s["lon"], s["lat"])
        if u:
            us = (u.get("Number_Stories") or "").strip()
            refs["UF 2021"] = {"walls": UF_MAT.get((u.get("Structural_Type") or "").strip(" :;").lower()),
                               "foundation height": UF_FND.get((u.get("Foundation_Type") or "").strip(" :;").lower()),
                               "stories": int(float(us)) if us.replace(".", "").isdigit() and float(us) == int(float(us)) else None}
        for src, v in srcs.items():
            for ref, w in refs.items():
                for k in ("walls", "foundation height", "stories"):
                    pairs[(src, ref, k)].append((v[k], w[k]))
        ff = (agent.get(bid) or {}).get("first_floor_ft")
        if ff is not None:
            if s.get("first_floor_ft") is not None:
                floors["assessors"].append(ff - s["first_floor_ft"])
            if u and u.get("FFE_Grade") not in (None, ""):
                try:
                    floors["UF 2021"].append(ff - float(u["FFE_Grade"]))
                except ValueError:
                    pass
    for (src, ref, k), pp in pairs.items():
        res.setdefault(src, {}).setdefault(ref, {})[k] = agree(pp)
    res["first_floor_ft_error"] = {ref: {"buildings": len(e), "median_abs_ft": round(float(np.median(np.abs(e))), 2) if e else None,
                                         "within_1ft": round(float(np.mean(np.abs(e) <= 1)), 3) if e else None,
                                         "mean_bias_ft": round(float(np.mean(e)), 2) if e else None} for ref, e in floors.items()}
    return res


def aerial_blind_spot() -> dict:
    t = pd.read_csv(OUT / "buildings.csv", low_memory=False)
    t = t[t["obs_roof"].notna() & t["event"].str.startswith(("Helene", "Ian"))]
    out = {}
    for town, g in t.groupby("town"):
        dmg = g[["obs_roof", "obs_walls", "obs_foundation"]].max(axis=1) >= 2
        hidden = dmg & (g["obs_roof"] < 2)
        bad = g[["obs_roof", "obs_walls", "obs_foundation"]].max(axis=1) >= 4
        bad_hidden = bad & (g["obs_roof"] < 2)
        out[town] = {"damaged": int(dmg.sum()), "damaged_roof_intact": int(hidden.sum()), "share_hidden_from_above": round(float(hidden.sum() / max(dmg.sum(), 1)), 3),
                     "ci95": wilson(int(hidden.sum()), int(dmg.sum())),
                     "badly_damaged": int(bad.sum()), "badly_damaged_roof_intact": int(bad_hidden.sum()),
                     "share_bad_hidden_from_above": round(float(bad_hidden.sum() / max(bad.sum(), 1)), 3)}
    return out


def agent_catch(matched: dict) -> dict:
    """Damaged = survey DS2+ anywhere. Caught = agent said minor or worse."""
    out = {}
    for name, f in (("aerial photos only", "eval/cedar_key_drafts_2026-11-18.json"), ("aerial + street photos", "eval/cedar_key_drafts_street_2026-11-18.json")):
        d = {a["id"]: a for a in json.loads((ROOT / f).read_text())}
        rows = {"roof damaged": [0, 0], "roof intact (walls, floors or foundation only)": [0, 0]}
        false = [0, 0]
        for bid, s in matched.items():
            if bid not in d:
                continue
            dmg = max(s["DS_roof"], s["DS_walls/floors"], s["DS_foundation"]) >= 2
            said = d[bid]["category"] in AGENT_DAMAGED
            if dmg:
                k = "roof damaged" if s["DS_roof"] >= 2 else "roof intact (walls, floors or foundation only)"
                rows[k][0] += said
                rows[k][1] += 1
            else:
                false[0] += said
                false[1] += 1
        out[name] = {k: {"caught": a, "of": b, "ci95": wilson(a, b)} for k, (a, b) in rows.items()} | {"false_alarms": {"said_damaged": false[0], "of_undamaged": false[1]}}
    return out


def main() -> None:
    import csv

    import score_field as sf

    survey = sf.load_survey(ROOT / "eval" / "field")
    matched = sf.match(survey, ROOT / "demo" / "cedar_key" / "buildings.geojson")
    rows = {r["FID"]: r for r in csv.DictReader((ROOT / "eval" / "field" / "HxHelene_VDA_CedarKey.csv").open(encoding="utf-8-sig"))}
    agent = {a["id"]: a for a in json.loads((ROOT / "demo" / "cedar_key" / "assessments.json").read_text())}
    nsi = json.loads((ROOT / "demo" / "cedar_key" / "structures.json").read_text())
    res = {"matched_buildings": len(matched), "how_built": built(matched, agent, nsi, rows), "hidden_from_above": aerial_blind_spot(),
           "agent_caught_damage": agent_catch(matched)}
    (OUT / "ai_accuracy.json").write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    main()
