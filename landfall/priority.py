"""Who needs help first: households likely out of their homes, and the order
to get to them.

A building is "likely displaced" when the drafts say Major or Destroyed, or
when the records put its first floor low enough that the surveyed water
stood 18 inches or more inside even at the highest plausible floor (the PDA
Guide's line for Major). "Possibly displaced" uses the lowest plausible
floor instead. Households are the parcel's residential units (1 for a home
with none recorded); buildings that aren't homes count no households.

The urgency score orders buildings for the first teams: displacement first,
then severity, then how deep the water was inside, then whether the draft
needs a field check anyway.
"""

from __future__ import annotations

from .cost import SLAB_FT

ORDER = ["none", "affected", "minor", "major", "destroyed"]
RESIDENTIAL = {"000", "001", "002", "003", "004", "005", "006", "007", "008", "009"}  # 000 vacant (often a destroyed home)
LIVING_MAJOR_FT = 1.5
# Florida DOR land-use codes seen on the coast, for reasons a person can read
LAND_USE = {
    "010": "vacant commercial", "011": "store", "012": "shop with living space", "016": "shopping center",
    "017": "office", "021": "restaurant", "023": "bank", "026": "service station", "027": "auto service",
    "033": "bar", "039": "hotel or motel", "048": "warehouse", "071": "church", "083": "school",
    "086": "county property", "089": "city property", "091": "utility",
}


def households(parcel: dict | None) -> int:
    if not parcel:
        return 1  # no record: count it as one household rather than none
    if parcel.get("land_use") and parcel["land_use"] not in RESIDENTIAL:
        return 0
    return max(1, parcel.get("units") or 0)


def assess_need(draft: dict, flood_ft: float | None, parcel: dict | None) -> dict:
    cat = draft.get("category", "none")
    sev = ORDER.index(cat) if cat in ORDER else 0
    p = parcel or {}
    f_low = p.get("floor_low_ft") if p.get("floor_low_ft") is not None else SLAB_FT
    f_high = p.get("floor_high_ft")
    inside_max = (flood_ft or 0) - f_low  # water inside if the floor is as low as it could be
    inside_min = (flood_ft or 0) - f_high if f_high is not None else 0.0
    reasons = []
    likely = cat in ("major", "destroyed") and not draft.get("demolished")
    if cat in ("major", "destroyed"):
        reasons.append(f"drafted {cat}")
    if inside_min >= LIVING_MAJOR_FT:
        likely = True
        reasons.append(f"about {inside_min:.1f} ft or more of water inside ({p.get('basis', 'floor from records')})")
    possibly = likely or inside_max >= LIVING_MAJOR_FT
    if possibly and not likely:
        reasons.append(f"up to {inside_max:.1f} ft of water inside if the floor is low")
    hh = 0 if draft.get("demolished") else households(parcel)
    if parcel is None and hh:
        reasons.append("no property record: counted as one household")
    if hh == 0 and not draft.get("demolished"):
        use = LAND_USE.get(p.get("land_use"), f"land use {p.get('land_use')}")
        reasons.append(f"{use} on the tax roll, not a home (counted as a business or lodging, not households)")
    score = (4 if likely else 2 if possibly else 0) + sev + max(0.0, min(inside_max, 6.0)) / 2 + (0.5 if draft.get("needs_human") else 0)
    return dict(
        id=draft["id"],
        category=cat,
        households=hh,
        likely_displaced=likely and hh > 0,
        possibly_displaced=possibly and hh > 0,
        business_or_lodging_hit=likely and hh == 0 and not draft.get("demolished"),
        land_use=p.get("land_use"),
        water_inside_ft=[round(max(0.0, inside_min), 1), round(max(0.0, inside_max), 1)],
        reasons=reasons,
        score=round(score, 2),
    )


def rank(drafts: list[dict], floods: dict, parcels: dict) -> dict:
    rows = [assess_need(a, floods.get(a["id"]), parcels.get(a["id"])) for a in drafts if "category" in a]
    rows.sort(key=lambda r: -r["score"])
    return {
        "households_likely_displaced": sum(r["households"] for r in rows if r["likely_displaced"]),
        "households_possibly_displaced": sum(r["households"] for r in rows if r["possibly_displaced"]),
        # motels, shops, restaurants: no households on the roll, but jobs and rooms lost
        "businesses_or_lodging_hit": [r["id"] for r in rows if r["business_or_lodging_hit"]],
        "buildings": rows,
    }
