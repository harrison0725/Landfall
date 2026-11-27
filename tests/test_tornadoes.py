from landfall import tornadoes


def _square(lon, lat, n=200, material="wood frame"):
    import numpy as np

    rng = np.random.default_rng(0)
    return [{"lon": lon + (rng.random() - 0.5) / 55, "lat": lat + (rng.random() - 0.5) / 69, "material": material} for _ in range(n)]


def test_tornado_alley_is_riskier_than_the_florida_coast():
    moore = tornadoes.outlook(_square(-97.49, 35.34), -97.49, 35.34, sims=1000)
    cedar = tornadoes.outlook(_square(-83.03, 29.14), -83.03, 29.14, sims=1000)
    m, c = moore["periods"]["30"], cedar["periods"]["30"]
    assert m["chance_path_crosses"] > 5 * c["chance_path_crosses"] > 0
    assert moore["tornadoes_per_year"] > cedar["tornadoes_per_year"]
    assert moore["strongest_within_10mi"][0]["rating"] == 5  # Moore, 2013


def test_more_years_more_chance_and_ranges_hold_the_mean():
    o = tornadoes.outlook(_square(-97.49, 35.34), -97.49, 35.34, sims=1000)
    p = o["periods"]
    assert p["10"]["chance_path_crosses"] <= p["30"]["chance_path_crosses"] <= p["50"]["chance_path_crosses"] < 1
    lo, hi = p["30"]["damaged_range_90"]
    assert lo <= p["30"]["expected_damaged"] <= hi <= 200
    assert p["30"]["expected_badly_damaged"] <= p["30"]["expected_damaged"]


def test_manufactured_homes_fare_worse():
    h = tornadoes.outlook(_square(-97.49, 35.34), -97.49, 35.34, sims=1000)
    m = tornadoes.outlook(_square(-97.49, 35.34, material="manufactured home"), -97.49, 35.34, sims=1000)
    assert m["periods"]["30"]["expected_damaged"] >= h["periods"]["30"]["expected_damaged"]
