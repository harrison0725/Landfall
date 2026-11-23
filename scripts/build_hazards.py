"""Build the damage tables in landfall/history/ that hazards.py reads.

Sources (all public):
- Wind (hurricane and tornado): the Hazus Hurricane Model v5.1 building
  fragility curves, as published in machine-readable form by the NHERI
  SimCenter Damage and Loss Model Library ("hurricane/building/portfolio/
  Hazus v5.1 original"; pip install simcenter-dlml). The demand is the peak
  3-second gust over open terrain at 10 m, in mph.
- Earthquake: the Hazus Earthquake Model v6.1 building fragility curves in
  equivalent peak ground acceleration ("seismic/building/portfolio/Hazus
  v6.1", the LF.* models), by building type and seismic design level.
- Flood: measured first-floor heights of 654 Cedar Key buildings from the
  DesignSafe PRJ-6245 survey (eval/field/cedar_key_buildings.csv), by
  foundation; and the Hazus Flood v6.1 FIA depth-damage curves for single
  family homes, for the depth above the floor at which repairs reach half
  of the building's value (FEMA's "substantial damage").

Hazus wind curves need roof and opening details Landfall can't see from a
satellite photo (roof shape, deck nailing, roof-to-wall connection,
shutters, garage, secondary water barrier). Each era gets the details its
building rules required (ASSUMED below, from Florida code history) and the
rest are averaged with equal weight.

Run: pip install simcenter-dlml && python scripts/build_hazards.py
"""

from __future__ import annotations

import csv
import itertools
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "landfall" / "history"
SPEEDS = list(range(50, 255, 5))  # mph gust grid the table is stored on


def dlml_dir() -> Path:
    import dlml

    return Path(dlml.__file__).resolve().parent / "data"


def multilinear(spec: str, x: float) -> float:
    xs, ps = (list(map(float, part.split(","))) for part in spec.split("|"))
    if x <= xs[0]:
        return ps[0]
    if x >= xs[-1]:
        return ps[-1]
    for k in range(1, len(xs)):
        if x <= xs[k]:
            t = (x - xs[k - 1]) / (xs[k] - xs[k - 1])
            return ps[k - 1] + t * (ps[k] - ps[k - 1])
    return ps[-1]


# ---------------------------------------------------------------- wind

# What each era's building rules required. ASSUMED from Florida code history:
# before the post-Andrew changes (1994) roofs were typically toe-nailed with
# 6d deck nails; from 1994 straps and 8d nails were common; the Florida
# Building Code (March 2002) required 8d nails at 6 in. spacing and straps,
# with shutters in the wind-borne debris region (taken as half the homes).
# Outside Florida "modern" (2000 or later, International Residential Code)
# is 8d nails and straps without shutters.
ERA_RULES = {
    "old": {"deck": ["6d"], "rwc": ["tnail"], "shutters": ["0"]},
    "mid": {"deck": ["8d"], "rwc": ["strap"], "shutters": ["0"]},
    "modern_fl": {"deck": ["8s"], "rwc": ["strap"], "shutters": ["0", "1"]},
    "modern": {"deck": ["8d"], "rwc": ["strap"], "shutters": ["0"]},
    "unknown": {},
}
# position of each field in the model id, per family (from the DLML mapping)
FIELDS = {
    "W.SF": ["h", "shape", "swr", "deck", "rwc", "garage", "shutters", "terrain"],
    "M.SF": ["h", "shape", "rwc", "system", "deck", "shutters", "swr", "garage", "reinf", "cover", "terrain"],
}
TERRAIN = {"open": "3", "light suburban": "15", "suburban": "35", "light trees": "70", "trees": "100"}


def wind_table() -> dict:
    d = dlml_dir() / "hurricane" / "building" / "portfolio" / "Hazus v5.1 original" / "fragility.csv"
    rows = list(csv.DictReader(d.open()))
    by_family: dict[str, list[tuple[list[str], dict]]] = {}
    for r in rows:
        t = r["ID"].split(".")
        fam = "MH" if t[0] == "MH" else ".".join(t[:2])
        by_family.setdefault(fam, []).append((t, r))

    def curves(models: list[dict]) -> dict:
        """Average chance of damage state 2+ and 3+ at each gust speed."""
        out = {"damaged": [], "severe": []}
        for v in SPEEDS:
            out["damaged"].append(round(statistics.fmean(multilinear(m["LS2-Theta_0"], v) for m in models), 4))
            out["severe"].append(round(statistics.fmean(multilinear(m["LS3-Theta_0"], v) for m in models), 4))
        return out

    table = {}
    for mat, fam in (("wood", "W.SF"), ("masonry", "M.SF")):
        pos = {f: i + 2 for i, f in enumerate(FIELDS[fam])}
        for h, era, (tname, tcode) in itertools.product(("1", "2"), ERA_RULES, TERRAIN.items()):
            rule = ERA_RULES[era]
            ms = [r for t, r in by_family[fam]
                  if t[pos["h"]] == h and t[pos["terrain"]] == tcode
                  and (fam != "M.SF" or t[pos["system"]] == "trs")  # wood-truss roof on block walls
                  and all(t[pos[f]] in allowed for f, allowed in rule.items())]
            assert ms, (fam, h, era, tname)
            table[f"{mat}|{h}|{era}|{tname}"] = curves(ms) | {"models": len(ms)}
    # manufactured homes: by HUD standard (pre-1976, 1976, 1994 Wind Zone II
    # in Florida's coastal counties, Zone I elsewhere); tie-downs averaged, no shutters
    for era, cls in (("phud", "PHUD"), ("hud76", "76HUD"), ("hud94_1", "94HUDI"), ("hud94_2", "94HUDII"), ("unknown", None)):
        for tname, tcode in TERRAIN.items():
            ms = [r for t, r in by_family["MH"] if (cls is None or t[1] == cls) and t[2] == "0" and t[-1] == tcode]
            table[f"manufactured|1|{era}|{tname}"] = curves(ms) | {"models": len(ms)}
    # engineered low-rise concrete and steel residential buildings: all
    # roof cover, window and debris options averaged
    for mat, fam in (("concrete", "C.ERB"), ("steel", "S.ERB")):
        for tname, tcode in TERRAIN.items():
            ms = [r for t, r in by_family[fam] if t[2] == "L" and t[-1] == tcode]
            table[f"{mat}|1|any|{tname}"] = curves(ms) | {"models": len(ms)}
    return {"speeds_mph": SPEEDS, "curves": table,
            "source": "Hazus Hurricane Model v5.1 building fragilities (NHERI SimCenter DLML, 'Hazus v5.1 original')",
            "damaged": "Hazus damage state 2 (moderate) or worse: major roof cover loss, some windows broken",
            "severe": "Hazus damage state 3 (severe) or worse: major roof sheathing or window loss, or destroyed"}


# ---------------------------------------------------------------- earthquake


def quake_table() -> dict:
    d = dlml_dir() / "seismic" / "building" / "portfolio" / "Hazus v6.1" / "fragility.csv"
    out = {}
    for r in csv.DictReader(d.open()):
        if not r["ID"].startswith("LF."):
            continue
        out[r["ID"][3:]] = {  # e.g. W1.PC: [median g, beta] for slight, moderate, extensive, complete
            f"ls{k}": [float(r[f"LS{k}-Theta_0"]), float(r[f"LS{k}-Theta_1"])] for k in range(1, 5)}
    return {"models": out,
            "source": "Hazus Earthquake Model v6.1 building fragilities in equivalent peak ground acceleration (NHERI SimCenter DLML, LF models)",
            "damaged": "Hazus moderate structural damage or worse", "severe": "Hazus extensive structural damage or worse"}


# ---------------------------------------------------------------- flood


def flood_table() -> dict:
    floors: dict[str, list[float]] = {}
    for r in csv.DictReader((ROOT / "eval" / "field" / "cedar_key_buildings.csv").open()):
        try:
            f = float(r["first_floor_ft"])
        except (TypeError, ValueError):
            continue
        floors.setdefault(r["foundation"] or "unknown", []).append(round(f, 1))
    floors["any"] = sorted(x for v in floors.values() for x in v)
    floors = {k: sorted(v) for k, v in floors.items()}
    d = dlml_dir() / "flood" / "building" / "portfolio" / "Hazus v6.1" / "loss_repair.csv"
    half = {}
    for r in csv.DictReader(d.open()):
        for key, name in (("structural.105.", "one_floor"), ("structural.107.", "two_floors")):
            if r["ID"].startswith(key):
                ps, xs = (list(map(float, p.split(","))) for p in r["LossFunction-Theta_0"].split("|"))
                half[name] = next(x for x, p in zip(xs, ps) if p >= 0.5)
    return {"first_floor_ft": floors, "half_value_ft_above_floor": half,
            "source_floors": "first-floor heights of surveyed Cedar Key buildings, DesignSafe PRJ-6245 (Helene, 2024)",
            "source_half": "Hazus Flood v6.1 FIA depth-damage curves for single-family homes (A zone, no basement)"}


if __name__ == "__main__":
    for name, fn in (("hazus_wind.json", wind_table), ("hazus_quake.json", quake_table), ("flood_floors.json", flood_table)):
        (OUT / name).write_text(json.dumps(fn(), separators=(",", ":")))
        print(name, (OUT / name).stat().st_size, "bytes")
