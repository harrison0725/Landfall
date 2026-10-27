from landfall.fema import Category, Measures, floor_from_measures, verify


def m(roof=0.0, tarp=0.0, debris=0.0, flood=None, aligned=True):
    return Measures(roof, tarp, debris, flood, aligned)


def test_intact_dry_house():
    assert floor_from_measures(m()) is Category.NONE


def test_tarp_means_at_least_minor():
    assert floor_from_measures(m(tarp=0.1)) is Category.MINOR
    assert floor_from_measures(m(tarp=0.02)) is Category.NONE  # mask noise


def test_flood_raises_intact_roof():
    # The case roof-only models miss: roof fine, water high in a slab house.
    slab = Measures(0.0, 0.0, 0.0, 4.2, True, first_floor_ft=1.0)
    assert floor_from_measures(slab) is Category.MAJOR


def test_flood_under_raised_house_is_affected():
    stilts = Measures(0.0, 0.0, 0.0, 4.2, True, first_floor_ft=9.0)
    assert floor_from_measures(stilts) is Category.AFFECTED


def test_flood_with_unknown_floor_height_only_proves_affected():
    assert floor_from_measures(m(flood=6.0)) is Category.AFFECTED
    assert floor_from_measures(m(flood=0.2)) is Category.NONE


def test_shallow_living_space_water_is_minor():
    assert floor_from_measures(Measures(0.0, 0.0, 0.0, 2.0, True, first_floor_ft=1.0)) is Category.MINOR


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


def test_flood_objection_is_separate_from_mask_noise():
    from landfall.fema import flood_floor

    assert flood_floor(m(roof=0.5, flood=3.0)) is Category.AFFECTED
    v = verify(Category.NONE, m(roof=0.0, flood=3.0))
    assert not v.ok and "surveyed flood water" in v.problems[0]
