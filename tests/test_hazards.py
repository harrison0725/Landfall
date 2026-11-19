from landfall import hazards


def wood_old():
    return {"year_built": 1960, "material": "wood frame", "foundation": "slab", "green": "some trees or lawn", "stories": 1}


def test_every_scenario_has_a_default_and_unique_ids():
    ids = hazards.scenario_ids()
    assert len(ids) == len(set(ids))
    for t, s in hazards.SCENARIOS.items():
        assert any(c[0] == s["default"] for c in s["categories"])
    assert hazards.default_scenario("hurricane") == "hurricane:cat3"
    assert hazards.default_scenario("wildfire") == "wildfire:la2025"


def test_odds_rise_with_category():
    for t in ("hurricane", "tornado", "earthquake", "flood"):
        xs = [c[2] for c in hazards.SCENARIOS[t]["categories"]]
        d = [hazards.modeled(t, x, wood_old(), "FL")[0] for x in xs]
        assert d == sorted(d) and d[0] < d[-1], t
        for x in xs:
            dmg, sev = hazards.modeled(t, x, wood_old(), "FL")
            assert 0 <= sev <= dmg <= 1


def test_newer_code_and_elevation_help():
    new = wood_old() | {"year_built": 2010}
    assert hazards.modeled("hurricane", 145, new, "FL")[0] < hazards.modeled("hurricane", 145, wood_old(), "FL")[0]
    assert hazards.modeled("earthquake", 0.4, new, "CA")[0] < hazards.modeled("earthquake", 0.4, wood_old(), "CA")[0]
    piles = wood_old() | {"foundation": "piles"}
    assert hazards.modeled("flood", 3, piles, "FL")[0] < hazards.modeled("flood", 3, wood_old(), "FL")[0]
    mobile = wood_old() | {"material": "manufactured (mobile) home"}
    assert hazards.modeled("hurricane", 125, mobile, "FL")[0] > hazards.modeled("hurricane", 125, wood_old(), "FL")[0]


def test_table_shares_classes_and_covers_all_scenarios():
    bs = [wood_old(), wood_old(), wood_old() | {"year_built": 2010}]
    classes, index = hazards.odds_table(bs, "FL")
    assert index[0] == index[1] != index[2]
    assert set(classes[0]["odds"]) == set(hazards.scenario_ids())
    surge = classes[0]["odds"]["surge:helene"]
    assert surge[0] is not None and classes[0]["measured"]["surge:helene"][2] > 0
    cats = hazards.by_category(bs, classes, index, "hurricane")
    assert [c["category"] for c in cats][0] == "Category 1" and cats[0]["average_chance_damaged"] < cats[-1]["average_chance_damaged"]
