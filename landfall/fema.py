"""FEMA damage categories and the deterministic checks the agent can't argue past.

Category names follow FEMA's Preliminary Damage Assessment (PDA) Guide for
Individual Assistance: Affected, Minor, Major, Destroyed.

The numeric thresholds below are Landfall's own aerial proxies, not FEMA's
definitions (FEMA defines categories from on-the-ground inspection, which
aerial photos can't fully see). They live in one place so they can be tuned
against the hand-labeled eval set. The 18-inch flood line comes from state
field guides derived from the PDA Guide; check it against the current PDA
Guide itself before results are published.
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
    # Flood. The PDA Guide judges water depth *in the living space*: 18
    # inches or more is Major, less is Minor (state field guides derived from
    # it agree on the 18-inch line). We measure depth above ground, so the
    # living-space depth needs the first-floor height, which aerial photos
    # don't give. Many Cedar Key houses stand on pilings. Without it, water
    # around the house only proves Affected.
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

    living = m.flood_living_ft
    if living is not None:
        if living >= t.flood_living_major_ft:
            cat = max(cat, Category.MAJOR)
        elif living > 0:
            cat = max(cat, Category.MINOR)
        elif m.flood_ft > 0:
            cat = max(cat, Category.AFFECTED)  # water under a raised house
    elif m.flood_ft is not None and m.flood_ft >= t.flood_affected_ft:
        cat = max(cat, Category.AFFECTED)
    return cat


def flood_floor(m: Measures, t: Thresholds = Thresholds()) -> Category:
    """The category the surveyed flood depth alone requires.

    Unlike roof loss, flood depth is not read off a mask: it comes from
    surveyed high-water marks, a tide gauge and lidar ground. The model can't
    see it in the photos either way, so it is a hard floor, not something to
    argue about.
    """
    return floor_from_measures(Measures(0.0, 0.0, 0.0, m.flood_ft, m.aligned, m.first_floor_ft), t)


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
    water = flood_floor(m, t)
    if call < water:
        problems.append(
            f"Called {call.label}, but surveyed flood water stood {m.flood_ft:.1f} ft around this "
            f"building, which makes it at least {water.label} under the PDA rule. This is not a photo "
            "measurement and can't be wrong because of shadows or trees: water damage doesn't show "
            "from the air. Keep what you see on the roof in the evidence, and raise the category."
        )
    elif call < floor:
        problems.append(
            f"Called {call.label}, but the measurements alone justify at least "
            f"{floor.label} (roof loss {m.roof_loss:.0%}"
            + (f", flood {m.flood_ft:.1f} ft" if m.flood_ft is not None else "")
            + ")."
        )
    if call - floor > 1:
        problems.append(
            f"Called {call.label}, two or more steps above what the "
            f"measurements show ({floor.label}). Point to the visible damage "
            "that justifies it, or lower the call."
        )
    return Verdict(ok=not problems, problems=problems)
