from landfall.fema import Category, Measures, floor_from_measures, verify


def m(roof=0.0, tarp=0.0, debris=0.0, flood=None, aligned=True):
    return Measures(roof, tarp, debris, flood, aligned)


def test_intact_dry_house():
    assert floor_from_measures(m()) is Category.NONE


def test_tarp_means_at_least_minor():
    assert floor_from_measures(m(tarp=0.1)) is Category.MINOR


def test_flood_raises_intact_roof():
    # The case roof-only models miss: roof fine, water high.
    assert floor_from_measures(m(roof=0.0, flood=4.2)) is Category.MAJOR


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
