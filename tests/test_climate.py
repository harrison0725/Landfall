"""Over the years: past storms replayed past a square (landfall/climate.py)."""

from landfall import climate, hazards


def test_known_storms_show_up_where_they_hit():
    ck, years = climate.storms_here(-83.035, 29.138)
    names = {(s["name"], s["year"]) for s in ck}
    assert ("Helene", 2024) in names and ("Easy", 1950) in names
    assert years >= 75
    homestead, _ = climate.storms_here(-80.48, 25.47)
    assert homestead[0]["name"] == "Andrew" and homestead[0]["gust_mph"] > 120


def test_surge_only_near_the_coast_and_grows_with_wind():
    assert climate.surge_level(20, 13.8) == 0.0
    assert climate.surge_level(120, 13.8) > climate.surge_level(70, 13.8) > 0
    assert climate.surge_level(120, 0.0) == 0.0


def test_outlook_grows_with_the_years():
    b = {"lon": -83.035, "lat": 29.138, "material": "wood frame", "foundation": "slab", "stories": 1, "year_built": 1970,
         "flood_zone": "AE", "v_ult": 140, "green": None, "surge": (6, 9, 12, 15, 18)}
    rows = [dict(b) for _ in range(20)] + [dict(b, surge=(0, 0, 0, 0, 0), flood_zone="X", year_built=2010) for _ in range(20)]
    place = {"surge_checked": True}
    classes, index = hazards.odds_table(rows, "FL", place)
    o = climate.outlook(rows, classes, index, "FL", place, -83.035, 29.138, 0.38, sims=800)
    p = o["periods"]
    assert p["10"]["expected_damaged"] <= p["30"]["expected_damaged"] <= p["50"]["expected_damaged"] <= 40
    assert p["30"]["damaged_range_90"][0] <= p["30"]["expected_damaged"] <= p["30"]["damaged_range_90"][1]
    assert 0 < p["30"]["chance"]["hurricane_gusts"] < 1
    assert o["strongest_here"] and o["storms_counted"] > 10
