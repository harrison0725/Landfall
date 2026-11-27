"""FEMA damage categories and the deterministic checks the agent can't argue past.

Category names follow FEMA's Preliminary Damage Assessment (PDA) Guide for
Individual Assistance: Affected, Minor, Major, Destroyed.

The numeric thresholds below are Landfall's own aerial proxies, not FEMA's
definitions (FEMA defines categories from on-the-ground inspection, which
aerial photos can't fully see). They live in one place so they can be tuned
against the hand-labeled eval set. The 18-inch flood line is FEMA's own: the
Preliminary Damage Assessment Guide (FEMA, 2021) calls a home Major when the
waterline is above 18 inches (or above the electrical outlets) in an
essential living space, and 3 to 18 inches may be Minor or Major.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class Category(IntEnum):
    NONE = 0
    AFFECTED = 1
    MINOR = 2
    MAJOR = 3
    DESTROYED = 4

    @property
    def label(self) -> str:
        return "No visible damage" if self is Category.NONE else self.name.title()


# Outside the US, responders map damage on the four grades the Copernicus
# Emergency Management Service uses. This crosswalk is Landfall's, not an
# official one: Minor and Major both show damage to the structure, so both
# are "Damaged"; Affected is mostly cosmetic or water around the building,
# closest to "Possibly damaged".
INTERNATIONAL = {
    "none": "No visible damage",
    "affected": "Possibly damaged",
    "minor": "Damaged",
    "major": "Damaged",
    "destroyed": "Destroyed",
}


@dataclass(frozen=True)
class Thresholds:
    # Share of the pre-storm roof area missing in the post-storm photo.
    # Masks of undamaged Cedar Key houses differ by up to ~15% between the
    # two flights (shadows, trees, lean), so anything below that is noise.
    roof_loss_minor: float = 0.15
    roof_loss_major: float = 0.25
    roof_loss_destroyed: float = 0.60
    # SAM's "blue tarp" and "pile of debris" masks also land on blue-cast
    # roofs and dark tree canopy now and then; small shares are noise.
    tarp_minor: float = 0.05
    debris_affected: float = 0.05
    # Flood. Water never sets the category (see flood_status): it is stated
    # beside it. The PDA Guide's line for water in the living space is 18
    # inches; the depth above ground needs the first-floor height to say how
    # much got inside.
    flood_living_major_ft: float = 1.5
    flood_affected_ft: float = 0.5  # above ground, first floor unknown


@dataclass(frozen=True)
class Measures:
    roof_loss: float  # 0..1
    tarp_share: float  # 0..1 of roof covered by blue tarp
    debris_share: float  # 0..1 of the yard around the house covered by new debris
    flood_ft: float | None  # water depth above ground at the building; None when unknown
    aligned: bool  # did pre/post alignment succeed with confidence
    first_floor_ft: float | None = None  # living floor height above ground; None when unknown
    kind: str = "hurricane"  # the disaster; an earthquake can flatten a building under a whole roof

    @property
    def flood_living_ft(self) -> float | None:
        if self.flood_ft is None or self.first_floor_ft is None:
            return None
        return self.flood_ft - self.first_floor_ft


def floor_from_measures(m: Measures, t: Thresholds = Thresholds()) -> Category:
    """The lowest category the measurements alone justify."""
    cat = Category.NONE
    if m.roof_loss >= t.roof_loss_destroyed:
        cat = Category.DESTROYED
    elif m.roof_loss >= t.roof_loss_major:
        cat = Category.MAJOR
    elif m.roof_loss >= t.roof_loss_minor or m.tarp_share >= t.tarp_minor:
        cat = Category.MINOR
    elif m.debris_share >= t.debris_affected:
        cat = Category.AFFECTED
    return cat


def flood_status(m: Measures, t: Thresholds = Thresholds()) -> dict | None:
    """Whether the building flooded, stated next to the category, never in it.

    Landfall's category rates damage to the building itself. Water around or
    inside a house is reported here instead (Seokhwan's rule, Nov 18, 2026):
    the field survey it is checked against rates the structure, and a flooded
    house with sound walls is not a damaged one. FEMA's PDA Guide does count
    18 inches in the living space as Major, so an assessor applying it can
    read the depth off this.
    """
    if m.flood_ft is None or m.flood_ft < t.flood_affected_ft:
        return None
    living = m.flood_living_ft
    out = {"flooded": True, "around_ft": round(m.flood_ft, 1), "first_floor_ft": m.first_floor_ft,
           "living_ft": None if living is None else round(max(0.0, living), 1)}
    if living is None:
        out["label"] = f"Flooded: water stood about {m.flood_ft:.1f} ft deep around it; first floor height unknown"
    elif living > 0:
        out["label"] = f"Flooded: about {living:.1f} ft of water inside the living space ({m.flood_ft:.1f} ft around it)"
    else:
        out["label"] = f"Flooded: water stood {m.flood_ft:.1f} ft deep around it but stayed below the living floor"
    return out


@dataclass
class Verdict:
    ok: bool
    problems: list[str] = field(default_factory=list)


def verify(call: Category, m: Measures, t: Thresholds = Thresholds()) -> Verdict:
    """Check a model's category against the measurements.

    The model may go higher than the measurement floor (it can see damage the
    masks miss), but only by one step, and never lower. Every problem comes
    back as a sentence the agent reads when it revises.
    """
    problems = []
    floor = floor_from_measures(m, t)
    if not m.aligned:
        problems.append(
            "Pre and post photos did not line up with confidence; roof loss is "
            "unreliable. Mark for human review instead of trusting the numbers."
        )
    if call < floor:
        problems.append(
            f"Called {call.label}, but the measurements alone justify at least "
            f"{floor.label} (roof loss {m.roof_loss:.0%})."
        )
    if call - floor > 1 and m.kind != "earthquake":
        problems.append(
            f"Called {call.label}, two or more steps above what the "
            f"measurements show ({floor.label}). Point to the visible damage "
            "that justifies it, or lower the call."
        )
    return Verdict(ok=not problems, problems=problems)
