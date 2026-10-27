"""Repair cost ranges from the drafts, with local building values.

Building value: Florida's statewide tax roll (the Department of Revenue's
cadastral layer, filled by the 67 county property appraisers) gives each
parcel's just value, land value and living area. The median building value
per square foot of living area across the homes around the area is the
local rate; a building's value is that rate times its floor area (outline
area times stories from the footprint height). The roll in the service is
the current one, after the storm, so destroyed houses no longer carry a
building value of their own; the area median, dominated by houses still
standing, doesn't have that problem. Just value is a market value, not a
replacement cost, so this is a planning number, not an adjuster's.

Damage share: FEMA's Substantial Damage Estimator guidance (P-784 Appendix
E) grades each building element by where the water stood relative to the
first floor: below it 0-25%, just above it 25-50%, up to 3 ft above 50-75%,
over 3 ft 75-100%. Aerial photos don't show the first-floor height, so the
low end assumes a raised house (floor above the water) and the high end a
house built at grade (floor about 1 ft above the ground). Visible damage
gives a range by category (CATEGORY_SHARE, Landfall's planning ranges). A
building's range is the larger of the two at each end.

FEMA's 50% rule: a building in a flood zone whose repairs cost half its
value or more is "substantially damaged" and has to be brought up to
current flood rules (often elevated). Buildings whose high end reaches 50%
are flagged for the floodplain manager.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

CADASTRAL = "https://services9.arcgis.com/Gh9awoU677aKree0/arcgis/rest/services/Florida_Statewide_Cadastral/FeatureServer/0/query"
RESIDENTIAL = ("001", "002", "004", "005", "008")  # single family, mobile home, condo, co-op, multi-family <10
SQFT_PER_M2 = 10.7639
STORY_M = 3.2
SLAB_FT = 1.0  # first floor of a house built at grade, above the ground

# share of the building's value, (low, high), by drafted category
CATEGORY_SHARE = {
    "none": (0.0, 0.0),
    "affected": (0.0, 0.10),
    "minor": (0.10, 0.25),
    "major": (0.25, 0.75),
    "destroyed": (0.75, 1.0),
}


def flood_share(water_above_floor_ft: float | None) -> tuple[float, float]:
    """FEMA P-784 Appendix E bands, by water above the first floor."""
    if water_above_floor_ft is None:
        return (0.0, 0.0)
    if water_above_floor_ft <= 0:
        return (0.0, 0.25)  # water against the building, below the floor
    if water_above_floor_ft < 0.5:
        return (0.25, 0.50)
    if water_above_floor_ft <= 3.0:
        return (0.50, 0.75)
    return (0.75, 1.0)


def damage_share(
    category: str | None, flood_ft: float | None, floor: tuple[float | None, float | None] = (SLAB_FT, None), n_stories: int = 1
) -> tuple[float, float, str]:
    """(low, high, what drives the high end). floor is the first floor's
    (lowest, highest) plausible height above the ground; None as the highest
    means it may be raised clear of the water."""
    vis = CATEGORY_SHARE.get(category or "none", (0.0, 0.0))
    if flood_ft is not None and flood_ft > 0:
        f_low, f_high = floor
        high_band = flood_share(flood_ft - (f_low if f_low is not None else SLAB_FT))
        low_band = flood_share(flood_ft - f_high) if f_high is not None else (0.0, 0.0)
        # P-784's bands are for a one-story house; water reaches the ground
        # floor only, so a taller building loses about that floor's share.
        fl = (low_band[0] / n_stories, high_band[1] / n_stories)
        why = "flood" if f_high is not None else "flood if built low"
    else:
        fl, why = (0.0, 0.0), ""
    low, high = max(vis[0], fl[0]), max(vis[1], fl[1])
    driver = why if fl[1] > vis[1] else "visible damage"
    return low, high, driver


def local_rate(bbox: tuple[float, float, float, float], pad: float = 0.01) -> dict:
    """Median building value per sq ft of living area for homes around a box."""
    import requests

    w, s, e, n = bbox
    r = requests.get(
        CADASTRAL,
        params=dict(
            geometry=f"{w - pad},{s - pad},{e + pad},{n + pad}",
            geometryType="esriGeometryEnvelope",
            inSR=4326,
            spatialRel="esriSpatialRelIntersects",
            where="TOT_LVG_AR > 0",
            outFields="DOR_UC,JV,LND_VAL,TOT_LVG_AR,ASMNT_YR,CO_NO",
            returnGeometry="false",
            resultRecordCount=2000,
            f="json",
        ),
        timeout=90,
    )
    r.raise_for_status()
    rows = [f["attributes"] for f in r.json().get("features", [])]
    rates = [
        (x["JV"] - x["LND_VAL"]) / x["TOT_LVG_AR"]
        for x in rows
        if x.get("DOR_UC") in RESIDENTIAL and (x.get("JV") or 0) > (x.get("LND_VAL") or 0) and x["TOT_LVG_AR"] >= 300
    ]
    if len(rates) < 10:
        raise LookupError(f"only {len(rates)} homes with values near this area in the Florida tax roll")
    q = statistics.quantiles(rates, n=4)
    return {
        "usd_per_sqft": round(statistics.median(rates), 2),
        "p25": round(q[0], 2),
        "p75": round(q[2], 2),
        "homes": len(rates),
        "roll_year": max((x.get("ASMNT_YR") or 0) for x in rows),
        "source": "Florida Department of Revenue statewide cadastral (county property appraisers' tax roll)",
    }


def stories(height_m: float | None) -> int:
    if not height_m or height_m <= 0:
        return 1
    return max(1, min(4, round(height_m / STORY_M)))


def estimate(drafts: list[dict], feats: dict[str, dict], floods: dict[str, float | None], rate: dict, floors: dict[str, dict] | None = None) -> dict:
    """Per-building ranges and area totals."""
    out = []
    for a in drafts:
        if "category" not in a:
            continue
        p = feats[a["id"]]["properties"]
        st = stories(p.get("height"))
        sqft = p.get("area_m2", 0) * SQFT_PER_M2 * st
        value = sqft * rate["usd_per_sqft"]
        fl = (floors or {}).get(a["id"], {})
        low, high, driver = damage_share(a["category"], floods.get(a["id"]), (fl.get("floor_low_ft", SLAB_FT), fl.get("floor_high_ft")), st)
        if a.get("demolished"):
            low = high = 0.0
            driver = "gone before the storm"
        out.append(
            dict(
                id=a["id"],
                sqft=round(sqft),
                stories=st,
                value_usd=round(value, -2),
                share_low=low,
                share_high=high,
                cost_low_usd=round(value * low, -2),
                cost_high_usd=round(value * high, -2),
                driver=driver,
                floor_low_ft=fl.get("floor_low_ft"),
                floor_high_ft=fl.get("floor_high_ft"),
                floor_basis=fl.get("basis"),
                substantial_damage_possible=high >= 0.5,
                substantial_damage_likely=low >= 0.5,
            )
        )
    return {
        "rate": rate,
        "assumptions": {
            "category_share": CATEGORY_SHARE,
            "flood_bands": "FEMA P-784 Appendix E: water below first floor 0-25%, just above 25-50%, up to 3 ft 50-75%, over 3 ft 75-100%",
            "first_floor_at_grade_ft": SLAB_FT,
            "story_height_m": STORY_M,
        },
        "total_low_usd": round(sum(x["cost_low_usd"] for x in out), -3),
        "total_high_usd": round(sum(x["cost_high_usd"] for x in out), -3),
        "substantial_damage_likely": [x["id"] for x in out if x["substantial_damage_likely"]],
        "substantial_damage_possible": [x["id"] for x in out if x["substantial_damage_possible"]],
        "buildings": out,
    }


def load(d: Path) -> dict | None:
    p = d / "costs.json"
    return json.loads(p.read_text()) if p.exists() else None
