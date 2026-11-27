from landfall import hazards
from landfall.cost import CATEGORY_SHARE


def test_unknown_year_weights_come_from_census_and_sum_to_one():
    fl = hazards.era_weights("FL")
    assert abs(sum(w for _y, w in fl) - 1) < 1e-3
    assert abs(sum(w for _y, w in hazards.year_weights("FL")) - 1) < 1e-3
    # Florida's homes are newer than the even thirds the old rule assumed
    assert dict(fl)[1960] < 0.33


def test_unknown_year_is_not_treated_as_old():
    old = hazards._recal([0, 1, -0.5, -0.3], 0.3, 1960)
    unknown = hazards._recal([0, 1, -0.5, -0.3], 0.3, None, "FL")
    assert unknown < old


def test_category_cost_ranges_meet_between_hazus_ratios():
    assert CATEGORY_SHARE["affected"][1] == CATEGORY_SHARE["minor"][0] == 0.045
    assert CATEGORY_SHARE["major"] == (0.211, 0.669)


def test_roof_cover_moves_odds_only_when_known():
    from landfall import hazards
    b = {"material": "wood frame", "year_built": 1990, "stories": 1, "v_ult": 140}
    d0 = hazards.wind(120, b, "FL")[0]
    assert hazards.wind(120, b | {"roof_cover": "tile"}, "FL")[0] < d0 < hazards.wind(120, b | {"roof_cover": "3-tab shingles"}, "FL")[0]
    assert hazards.wind(120, b | {"roof_cover": "thatch"}, "FL")[0] == d0
