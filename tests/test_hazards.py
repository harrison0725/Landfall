import csv
import math
from pathlib import Path

from landfall import hazards


def wood_old():
    return {"year_built": 1960, "material": "wood frame", "foundation": "slab", "green": "some trees or lawn", "stories": 1}


def test_every_scenario_has_a_default_and_unique_ids():
    ids = hazards.scenario_ids()
    assert len(ids) == len(set(ids))
    for s in hazards.SCENARIOS.values():
        assert any(c[0] == s["default"] for c in s["categories"])
    assert hazards.default_scenario("hurricane") == "hurricane:cat3"
    assert hazards.default_scenario("wildfire") == "wildfire:la2025"


def test_category_intensities_follow_the_official_scales():
    # Saffir-Simpson midpoints x 1.22 gust factor; USGS ShakeMap MMI -> PGA
    assert [c[2] for c in hazards.SCENARIOS["hurricane"]["categories"]] == [104, 126, 146, 174, 201]
    assert [c[2] for c in hazards.SCENARIOS["earthquake"]["categories"]][2] == 0.4


def test_hazus_values_come_through_unchanged():
    # Hazus Earthquake v6.1, wood light frame, pre-code: moderate 0.29 g, extensive 0.51 g (beta 0.4)
    m = hazards._table("hazus_quake.json")["models"]["W1.PC"]
    assert m["ls2"] == [0.29, 0.4] and m["ls3"] == [0.51, 0.4]
    d, s = hazards.quake(0.29, {"material": "wood frame", "year_built": 1930}, "CA")
    assert abs(d - 0.5) < 1e-9 and s < 0.5
    # Hazus Flood FIA one-story home: half the building's value at 14 ft above the floor
    assert hazards._table("flood_floors.json")["half_value_ft_above_floor"]["one_floor"] == 14.0


def test_odds_rise_with_category():
    for t in ("hurricane", "tornado", "earthquake", "flood"):
        xs = [c[2] for c in hazards.SCENARIOS[t]["categories"]]
        d = [hazards.modeled(t, x, wood_old(), "FL")[0] for x in xs]
        assert d == sorted(d) and d[0] < d[-1], t
        for x in xs:
            dmg, sev = hazards.modeled(t, x, wood_old(), "FL")
            assert 0 <= sev <= dmg <= 1


def test_newer_rules_and_elevation_help():
    new = wood_old() | {"year_built": 2010}
    assert hazards.modeled("hurricane", 146, new, "FL")[0] < hazards.modeled("hurricane", 146, wood_old(), "FL")[0]
    assert hazards.modeled("earthquake", 0.4, new, "CA")[0] < hazards.modeled("earthquake", 0.4, wood_old(), "CA")[0]
    piles = wood_old() | {"foundation": "piles"}
    assert hazards.modeled("flood", 3, piles, "FL")[0] < hazards.modeled("flood", 3, wood_old(), "FL")[0]


def test_hazus_wind_against_helene_at_cedar_key():
    """NOAA station CKYF1 at Cedar Key measured a 84 mph peak gust in Helene.
    Hazus gives older one-story wood homes under 1% moderate damage there; the
    PRJ-6245 survey found about 2% of buildings with no surge damage to walls
    or foundation had roof damage state 2 or worse. Both say wind did little."""
    d, _ = hazards.wind(84, wood_old() | {"green": None}, "FL")
    assert d < 0.01
    rows = list(csv.DictReader((Path(__file__).resolve().parents[1] / "eval" / "field" / "cedar_key_buildings.csv").open()))

    def f(x):
        try:
            v = float(x)
            return None if math.isnan(v) else v
        except (TypeError, ValueError):
            return None

    wind_only = [r for r in rows if f(r["DS_roof"]) is not None and (f(r["DS_walls/floors"]) or 0) <= 1 and (f(r["DS_foundation"]) or 0) <= 1]
    share = sum(f(r["DS_roof"]) >= 2 for r in wind_only) / len(wind_only)
    assert 0.01 < share < 0.05


def test_table_shares_classes_and_covers_all_scenarios():
    bs = [wood_old(), wood_old(), wood_old() | {"year_built": 2010}]
    classes, index = hazards.odds_table(bs, "FL")
    assert index[0] == index[1] != index[2]
    assert set(classes[0]["odds"]) == set(hazards.scenario_ids())
    assert classes[0]["odds"]["surge:helene"][0] is not None and classes[0]["measured"]["surge:helene"][2] > 0
    cats = hazards.by_category(bs, classes, index, "hurricane")
    assert cats[0]["category"] == "Category 1" and cats[0]["average_chance_damaged"] < cats[-1]["average_chance_damaged"]
