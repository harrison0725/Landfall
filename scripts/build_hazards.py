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
satellite photo (deck nailing, roof-to-wall connection, shutters, garage,
secondary water barrier). hazards.py assigns them with the NHERI SimCenter
rulesets from year built and the site's ASCE 7 design wind speed.

Run: pip install simcenter-dlml && python scripts/build_hazards.py
"""

from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "landfall" / "history"


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

# Hazus wind models are picked per building by the NHERI SimCenter asset
# representation rulesets (landfall/hazards.py, ported from
# github.com/NHERI-SimCenter/AssetRepresentationRulesets). They only reach
# these configurations, so only these are stored: single-family wood and
# masonry (wood-truss roof, reinforced), manufactured homes, on suburban or
# light-tree terrain, at the gust speeds Landfall's categories use.
TERRAIN = {"suburban": "35", "light trees": "70"}


def wind_table() -> dict:
    from landfall.hazards import SCENARIOS

    speeds = list(range(50, 230, 10))  # gust grid, mph; interpolated in between
    d = dlml_dir() / "hurricane" / "building" / "portfolio" / "Hazus v5.1 original" / "fragility.csv"
    losses = {}
    for r in csv.DictReader((d.parent / "loss_repair.csv").open()):
        losses[r["ID"].removesuffix("-Cost")] = r["LossFunction-Theta_0"]
    models = {}
    for r in csv.DictReader(d.open()):
        t = r["ID"].split(".")
        keep = (
            (t[:2] == ["W", "SF"] and t[-1] in TERRAIN.values())
            or (t[:2] == ["M", "SF"] and t[5] == "trs" and t[10] in ("1", "null") and t[-1] in TERRAIN.values())
            or (t[0] == "MH" and t[-1] in TERRAIN.values())
        )
        if keep:  # chance of damage state 2+, 3+, and expected repair cost / building value
            lf = losses[r["ID"]]
            lf = "|".join(reversed(lf.split("|")))  # loss functions list ratios first, speeds second
            models[r["ID"]] = [[round(multilinear(r[f"LS{k}-Theta_0"], v), 4) for v in speeds] for k in (2, 3)] + [
                [round(multilinear(lf, v), 4) for v in speeds]]
    # engineered low-rise concrete and steel residential buildings: no
    # ruleset in hand, so every roof, window and debris option is averaged
    rows = list(csv.DictReader(d.open()))
    for mat, fam in (("concrete", "C.ERB.L"), ("steel", "S.ERB.L")):
        for tname, tcode in TERRAIN.items():
            ms = [r for r in rows if r["ID"].startswith(fam + ".") and r["ID"].endswith("." + tcode)]
            models[f"{mat}|{tname}"] = [[round(statistics.fmean(multilinear(m[f"LS{k}-Theta_0"], v) for m in ms), 4) for v in speeds] for k in (2, 3)] + [
                [round(statistics.fmean(multilinear("|".join(reversed(losses[m["ID"]].split("|"))), v) for m in ms), 4) for v in speeds]]
    combine = [list(map(float, line.split(","))) for line in (d.parent / "combine_wind_flood.csv").read_text().split()]
    return {"speeds_mph": speeds, "models": models, "combine_wind_flood": combine,
            "combine_source": "Hazus Hurricane v5.1 combined wind and flood loss table (rows wind loss 0-1, columns flood loss 0-1, step 0.1)",
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
    cons = {}
    for r in csv.DictReader((d.parent / "consequence_repair.csv").open()):
        if r["ID"] == "LF.RES1-Cost":
            cons = [float(r[f"DS{k}-Theta_0"]) for k in range(1, 5)]
    return {"models": out, "loss_ratio_by_damage_state": cons,
            "loss_source": "Hazus v6.1 repair cost ratios for single-family homes (LF.RES1): slight, moderate, extensive, complete",
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
    half, curves = {}, {}
    for r in csv.DictReader(d.open()):
        for key, name in (("structural.105.", "one_floor"), ("structural.107.", "two_floors"),
                          ("structural.113.", "one_floor_v"), ("structural.115.", "two_floors_v")):
            if r["ID"].startswith(key):
                ps, xs = (list(map(float, p.split(","))) for p in r["LossFunction-Theta_0"].split("|"))
                curves[name] = [xs, ps]  # ft of water above the first floor -> share of building value
                if not name.endswith("_v"):
                    half[name] = next(x for x, p in zip(xs, ps) if p >= 0.5)
    return {"first_floor_ft": floors, "half_value_ft_above_floor": half, "fia_structure_loss": curves,
            "water_damage": water_damage(),
            "source_water_damage": "Hurricane Helene (2024) at Cedar Key, DesignSafe PRJ-6245: share of surveyed buildings with wall/floor or "
                                   "foundation damage state 2+ (damaged) and 4+ (severe), by water above the first floor (surface 10.9 ft NAVD88 "
                                   "from USGS high-water marks and NOAA gauge 8727520), V zones apart from the rest",
            "source_floors": "first-floor heights of surveyed Cedar Key buildings, DesignSafe PRJ-6245 (Helene, 2024)",
            "source_half": "Hazus Flood v6.1 FIA depth-damage curves for single-family homes (A zone, no basement)"}


HELENE_SURFACE_FT = 10.9  # NAVD88: USGS high-water mark 10.96 ft and NOAA Cedar Key gauge peak 10.84 ft
WATER_BINS = [(-99.0, 0.0), (0.0, 3.0), (3.0, 6.0), (6.0, 99.0)]  # ft of water above the first floor


def _increasing(rates: list[float], ns: list[int]) -> list[float]:
    """Pool neighbours that go down (pool-adjacent-violators), so deeper
    water never gives lower odds just from a small sample."""
    blocks = [[r * n, n, 1] for r, n in zip(rates, ns)]
    i = 0
    while i < len(blocks) - 1:
        a, b = blocks[i], blocks[i + 1]
        if a[0] / max(a[1], 1) > b[0] / max(b[1], 1):
            blocks[i:i + 2] = [[a[0] + b[0], a[1] + b[1], a[2] + b[2]]]
            i = max(i - 1, 0)
        else:
            i += 1
    out = []
    for tot, n, k in blocks:
        out += [round(tot / max(n, 1), 3)] * k
    return out


def water_damage(rows: list[dict] | None = None) -> dict:
    """Measured damage to the building itself (walls, floors, foundation;
    not the roof, not water inside) by how deep the water was above the
    first floor, from the Helene survey at Cedar Key."""
    import math

    def num(x):
        try:
            v = float(x)
            return None if math.isnan(v) else v
        except (TypeError, ValueError):
            return None

    out = {}
    if rows is None:
        rows = list(csv.DictReader((ROOT / "eval" / "field" / "cedar_key_buildings.csv").open()))
    for zone in ("V", "other"):
        ns, dmg, sev = [], [], []
        for lo, hi in WATER_BINS:
            hits = []
            for r in rows:
                ffe = num(r["FFE_NAVD88_ft"])
                ds = [v for v in (num(r["DS_walls/floors"]), num(r["DS_foundation"])) if v is not None]
                if ffe is None or not ds or ((r["firmzone"] or "").upper().startswith("V")) != (zone == "V"):
                    continue
                if lo <= HELENE_SURFACE_FT - ffe < hi:
                    hits.append(max(ds))
            ns.append(len(hits))
            dmg.append(sum(d >= 2 for d in hits) / max(len(hits), 1))
            sev.append(sum(d >= 4 for d in hits) / max(len(hits), 1))
        out[zone] = {"bins_ft": WATER_BINS, "buildings": ns, "damaged": _increasing(dmg, ns), "severe": _increasing(sev, ns)}
    return out


def coast_table() -> dict:
    """US Gulf and Atlantic coastline (Natural Earth 1:10m, public domain),
    simplified to about 500 m, for the distance a hurricane travels inland."""
    import requests
    from shapely.geometry import box, shape

    url = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_coastline.geojson"
    clip = box(-98, 17, -65, 46)
    lines = []
    for f in requests.get(url, timeout=120).json()["features"]:
        g = shape(f["geometry"]).intersection(clip)
        if g.is_empty:
            continue
        for line in getattr(g.simplify(0.005), "geoms", [g.simplify(0.005)]):
            if line.geom_type == "LineString" and len(line.coords) > 1:
                lines.append([[round(x, 3), round(y, 3)] for x, y in line.coords])
    return {"lines": lines, "source": "Natural Earth 1:10m coastline (public domain), Gulf and Atlantic, simplified to ~500 m"}


if __name__ == "__main__":
    for name, fn in (("hazus_wind.json", wind_table), ("hazus_quake.json", quake_table), ("flood_floors.json", flood_table), ("coast.json", coast_table)):
        (OUT / name).write_text(json.dumps(fn(), separators=(",", ":")))
        print(name, (OUT / name).stat().st_size, "bytes")
