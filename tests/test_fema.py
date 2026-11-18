from landfall.fema import Category, Measures, floor_from_measures, verify


def m(roof=0.0, tarp=0.0, debris=0.0, flood=None, aligned=True):
    return Measures(roof, tarp, debris, flood, aligned)


def test_intact_dry_house():
    assert floor_from_measures(m()) is Category.NONE


def test_tarp_means_at_least_minor():
    assert floor_from_measures(m(tarp=0.1)) is Category.MINOR
    assert floor_from_measures(m(tarp=0.02)) is Category.NONE  # mask noise


def test_flood_never_sets_the_category():
    # Flooding is stated beside the category, not counted as damage.
    slab = Measures(0.0, 0.0, 0.0, 4.2, True, first_floor_ft=1.0)
    assert floor_from_measures(slab) is Category.NONE
    assert floor_from_measures(m(flood=6.0)) is Category.NONE
    assert floor_from_measures(Measures(0.4, 0.0, 0.0, 6.0, True, first_floor_ft=0.0)) is Category.MAJOR  # roof still counts


def test_flood_status_states_water_inside():
    from landfall.fema import flood_status

    inside = flood_status(Measures(0.0, 0.0, 0.0, 4.2, True, first_floor_ft=1.0))
    assert inside["flooded"] and inside["living_ft"] == 3.2 and "inside the living space" in inside["label"]
    stilts = flood_status(Measures(0.0, 0.0, 0.0, 4.2, True, first_floor_ft=9.0))
    assert stilts["living_ft"] == 0.0 and "below the living floor" in stilts["label"]
    unknown = flood_status(m(flood=6.0))
    assert unknown["living_ft"] is None and "unknown" in unknown["label"]
    assert flood_status(m(flood=0.2)) is None and flood_status(m()) is None


def test_verifier_rejects_undercall():
    v = verify(Category.MINOR, m(roof=0.7))
    assert not v.ok and "at least Destroyed" in v.problems[0]


def test_verifier_allows_one_step_up():
    assert verify(Category.MINOR, m(debris=0.05)).ok


def test_verifier_rejects_big_overcall():
    v = verify(Category.DESTROYED, m(tarp=0.1))
    assert not v.ok


def test_unaligned_goes_to_review():
    v = verify(Category.MINOR, m(tarp=0.1, aligned=False))
    assert not v.ok and "human review" in v.problems[0]


def test_verifier_does_not_raise_for_flood():
    assert verify(Category.NONE, m(flood=3.0)).ok
    v = verify(Category.MAJOR, m(flood=6.0))
    assert not v.ok  # two steps above what the building shows
