from landfall.cost import damage_share, flood_share, stories
from landfall.parcels import first_floor


def test_flood_bands_follow_p784():
    assert flood_share(None) == (0.0, 0.0)
    assert flood_share(-0.5) == (0.0, 0.25)
    assert flood_share(0.3) == (0.25, 0.50)
    assert flood_share(2.0) == (0.50, 0.75)
    assert flood_share(4.0) == (0.75, 1.0)


def test_unknown_floor_spans_raised_to_low():
    low, high, why = damage_share("affected", 6.0)
    assert low == 0.0 and high == 1.0 and why == "flood if built low"


def test_known_floor_narrows_the_range():
    # floor at the BFE, 2 ft above the water: dry inside
    low, high, _ = damage_share("none", 6.0, (8.0, 8.0))
    assert (low, high) == (0.0, 0.25)


def test_visible_damage_sets_the_floor_of_the_range():
    low, high, why = damage_share("destroyed", None)
    assert (low, high, why) == (0.75, 1.0, "visible damage")


def test_taller_buildings_lose_less_to_flood():
    assert damage_share("none", 6.0, (1.0, 3.0), n_stories=2)[1] == 0.5


def test_first_floor_from_records():
    assert first_floor(1995, "AE", 12.0, 4.0)["floor_low_ft"] == 8.0
    assert first_floor(1950, "AE", 12.0, 4.0)["floor_high_ft"] == 3.0
    assert first_floor(None, "AE", 12.0, 4.0)["floor_high_ft"] is None
    assert stories(7.0) == 2 and stories(None) == 1


def test_country_rate_from_jrc_table_and_aliases():
    import pytest

    from landfall.cost import country_rate

    jm = country_rate("Jamaica")
    assert jm["country"] == "Jamaica" and 20 < jm["usd_per_sqft"] < 40
    assert country_rate("TheBahamas")["country"] == "Bahamas, The"
    with pytest.raises(LookupError):
        country_rate("Mayotte")


def test_no_fema_50_percent_rule_abroad():
    from landfall.cost import estimate

    feats = {"b1": {"properties": {"area_m2": 100.0, "height": 3.0}}}
    out = estimate([{"id": "b1", "category": "destroyed"}], feats, {}, {"usd_per_sqft": 28.0}, {}, us=False)
    assert out["substantial_damage_likely"] == [] and out["total_high_usd"] > 0
