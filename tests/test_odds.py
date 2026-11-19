from landfall import odds


def test_lookup_drops_factors_until_enough_look_alikes():
    past = [{"year_built": 1960, "material": "wood frame", "foundation": "slab", "green": "mostly trees", "damaged": 1, "severe": 0}] * 5
    past += [{"year_built": 1965, "material": "wood frame", "foundation": "piers", "green": "mostly trees", "damaged": 0, "severe": 0}] * 20
    member = {"year_built": 1950, "material": "wood frame", "foundation": "slab", "green": "mostly trees"}
    got = odds.lookup(member, past, "FL", "hurricane")
    # only 5 match on everything, so foundation and green drop off: age + material match all 25
    assert got["look_alikes"] == 25 and got["matched_on"] == ["age", "material"]
    assert got["chance_damaged"] == 0.2
    lo, hi = got["chance_damaged_90pct"]
    assert lo < 0.2 < hi


def test_wilson_bounds():
    assert odds.wilson(0, 0) == (0.0, 1.0)
    lo, hi = odds.wilson(10, 100)
    assert 0.05 < lo < 0.1 < hi < 0.17


def test_green_label():
    assert odds.green_label(0.5, 0.0) == "mostly trees"
    assert odds.green_label(0.05, 0.05).startswith("little green")
    assert odds.green_label(0.2, 0.1) == "some trees or lawn"


def test_history_files_load():
    assert len(odds.history("hurricane")) == 771
    assert len(odds.history("wildfire")) > 30000
    assert odds.history("earthquake") == []
