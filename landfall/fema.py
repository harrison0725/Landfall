"""FEMA damage categories and the deterministic checks the agent can't argue past.

Category names follow FEMA's Preliminary Damage Assessment (PDA) Guide for
Individual Assistance: Affected, Minor, Major, Destroyed.

The numeric thresholds below are Landfall's own aerial proxies, not FEMA's
definitions (FEMA defines categories from on-the-ground inspection, which
aerial photos can't fully see). They live in one place so they can be tuned
against the hand-labeled eval set. The flood-depth thresholds in particular
must be checked against the current PDA Guide before results are published.
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
    # Water depth above ground at the building, in feet.
    # TODO(verify): compare with the PDA Guide's flood criteria, which are
    # written as depth in the living space, not above ground.
    flood_minor_ft: float = 0.5
    flood_major_ft: float = 1.5
    flood_destroyed_ft: float = 6.0


@dataclass(frozen=True)
class Measures:
    roof_loss: float  # 0..1
    tarp_share: float  # 0..1 of roof covered by blue tarp
    debris_share: float  # 0..1 of the crop margin covered by debris
    flood_ft: float | None  # None when no estimate is available
    aligned: bool  # did pre/post alignment succeed with confidence


def floor_from_measures(m: Measures, t: Thresholds = Thresholds()) -> Category:
    """The lowest category the measurements alone justify."""
    cat = Category.NONE
    if m.roof_loss >= t.roof_loss_destroyed:
        cat = Category.DESTROYED
    elif m.roof_loss >= t.roof_loss_major:
        cat = Category.MAJOR
    elif m.roof_loss >= t.roof_loss_minor or m.tarp_share > 0:
        cat = Category.MINOR
    elif m.debris_share > 0.02:
        cat = Category.AFFECTED

    if m.flood_ft is not None:
        if m.flood_ft >= t.flood_destroyed_ft:
            cat = max(cat, Category.DESTROYED)
        elif m.flood_ft >= t.flood_major_ft:
            cat = max(cat, Category.MAJOR)
        elif m.flood_ft >= t.flood_minor_ft:
            cat = max(cat, Category.MINOR)
        elif m.flood_ft > 0:
            cat = max(cat, Category.AFFECTED)
    return cat


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
