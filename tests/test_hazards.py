import csv
import math
from pathlib import Path

from landfall import hazards


class uncalibrated:
    """Landfall without landfall/history/calibration.json (Cedar Key floors
    and water rates only), for tests of the underlying tables."""

    def __enter__(self):
        hazards.USE_CALIBRATION = False

    def __exit__(self, *a):
        hazards.USE_CALIBRATION = True


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
    d, s, _ = hazards.quake(0.29, {"material": "wood frame", "year_built": 1930}, "CA")
    assert abs(d - 0.5) < 1e-9 and s < 0.5
    # Hazus Flood FIA one-story home: half the building's value at 14 ft above the floor
    assert hazards._table("flood_floors.json")["half_value_ft_above_floor"]["one_floor"] == 14.0


def test_rulesets_pick_stronger_roofs_for_newer_homes_in_high_wind_zones():
    # Florida coast (ASCE 7-10 design wind 150 mph, flood zone AE): after 2000
    # the SimCenter rulesets give 8d @ 6"/6" deck nails, straps and shutters
    old = wood_old() | {"v_ult": 150, "flood_zone": "AE"}
    new = old | {"year_built": 2005}
    assert all("6d.tnail" in m for m, _ in hazards.wind_configs("wood", 1960, 1, 150, "AE", "35"))
    assert all(".8s.strap.std.0." in m for m, _ in hazards.wind_configs("wood", 2005, 1, 150, "AE", "35"))
    assert hazards.wind(146, new, "FL")[0] < hazards.wind(146, old, "FL")[0] - 0.1
    assert abs(sum(w for _, w in hazards.wind_configs("masonry", 1990, 1, 150, "AE", "35")) - 1) < 1e-9


def test_tornado_odds_follow_the_ef_scale():
    # EF scale DI 2: roof deck uplift is expected at 97 mph, so half the houses
    d, s = hazards.tornado(97, wood_old())
    assert abs(d - 0.5) < 1e-9 and s < 0.05
    mobile = wood_old() | {"material": "manufactured (mobile) home"}
    assert hazards.tornado(98, mobile)[0] > hazards.tornado(98, wood_old())[0]


def test_odds_rise_with_category():
    for t in ("hurricane", "tornado", "earthquake", "flood"):
        xs = [c[2] for c in hazards.SCENARIOS[t]["categories"]]
        d = [hazards.modeled(t, x, wood_old(), "FL")[0] for x in xs]
        assert d == sorted(d) and d[0] < d[-1], t
        for x in xs:
            dmg, sev, loss, wet = hazards.modeled(t, x, wood_old(), "FL")
            assert 0 <= sev <= dmg <= 1
            assert (loss is None) == (t == "tornado") and (loss is None or 0 <= loss <= 1)
            assert (wet is None) == (t in ("tornado", "earthquake"))


def test_newer_rules_and_elevation_help():
    new = wood_old() | {"year_built": 2010}
    assert hazards.modeled("hurricane", 146, new, "FL")[0] < hazards.modeled("hurricane", 146, wood_old(), "FL")[0]
    assert hazards.modeled("earthquake", 0.4, new, "CA")[0] < hazards.modeled("earthquake", 0.4, wood_old(), "CA")[0]
    piles = wood_old() | {"foundation": "piles"}
    assert hazards.modeled("flood", 3, piles, "FL")[0] < hazards.modeled("flood", 3, wood_old(), "FL")[0]


def test_hazus_wind_against_helene_at_cedar_key():
    """NOAA station CKYF1 at Cedar Key measured a 84 mph peak gust in Helene.
    Raw Hazus gives older one-story wood homes under 1% moderate damage there;
    the PRJ-6245 survey found about 2% of buildings with no surge damage to
    walls or foundation had roof damage state 2 or worse. The calibration to
    six storms' surveys (eval/research) raises it, because other storms found
    10-20% of roofs damaged at 70-100 mph; Cedar Key is its known low miss."""
    hazards.USE_CALIBRATION = False
    try:
        d, _, _ = hazards.wind(84, wood_old() | {"green": None}, "FL")
    finally:
        hazards.USE_CALIBRATION = True
    assert d < 0.01
    d, _, _ = hazards.wind(84, wood_old() | {"green": None}, "FL")
    assert 0.02 < d < 0.15
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


def test_wind_weakens_inland_kaplan_demaria():
    # at the coast only the sea-to-land drop (R = 0.9); about 4 hours in, about a third less
    assert hazards.inland_gust(146, 0) == round(((146 / 1.22 / 1.15078) * 0.9) * 1.15078 * 1.22)
    g50 = hazards.inland_gust(146, 50)
    assert 95 < g50 < 110 and hazards.inland_gust(146, 200) < g50
    # Cedar Key is on the Gulf; Gainesville is about 50 miles inland
    assert hazards.coast_miles(-83.035, 29.135) < 2
    assert 35 < hazards.coast_miles(-82.33, 29.65) < 65


def test_surge_and_wind_combine_by_the_hazus_table():
    b = wood_old()
    wd, ws, wl = hazards.wind(126, b, "FL")
    d, s, loss, wet = hazards.hurricane(126, 7, b, "FL")  # 7 ft of water at a slab house
    assert d > wd and s >= ws and loss > wl and wet > 0.5
    assert hazards.combine_losses(0.0, 0.3) == 0.3 and hazards.combine_losses(1.0, 0.5) == 1.0
    # Hazus table: 0.2 wind + 0.2 flood is 0.36, not 0.4
    assert abs(hazards.combine_losses(0.2, 0.2) - 0.36) < 1e-9
    assert hazards.hurricane(126, 0, b, "FL") == (wd, ws, wl, 0.0)


def test_expected_repair_cost_follows_hazus():
    # earthquake: all-complete damage costs the whole house (LF.RES1 complete = 1.0)
    assert hazards.quake(20, wood_old(), "CA")[2] > 0.99
    # flood: FIA one-story curve, 14 ft above a slab floor is half the value
    with uncalibrated():
        assert abs(hazards.flood_at([14.0], wood_old() | {"foundation": "slab"})[2] - 0.5) < 0.06
    assert hazards.flood(1, wood_old() | {"foundation": "piles"})[2] < hazards.flood(1, wood_old())[2]


def test_flooding_alone_is_not_damage():
    """Water inside is reported as flooding; damage is the walls, floors and
    foundation, at the rates measured after Helene (Harry, Nov 18)."""
    t = hazards._table("flood_floors.json")["water_damage"]
    assert t["other"]["damaged"] == sorted(t["other"]["damaged"]) and t["V"]["damaged"][-1] > t["other"]["damaged"][-1]
    with uncalibrated():
        d, s, loss, wet = hazards.flood(3, wood_old())  # slab house, 3 ft of water: inside
    assert wet > 0.9 and d < 0.5 and loss > 0.1
    d, s, loss, wet = hazards.flood(3, wood_old())  # calibrated: floors of houses listed as slab vary more
    assert wet > d and d < 0.5
    beach = wood_old() | {"flood_zone": "VE", "foundation": "piles"}
    assert hazards.flood_at([6], beach, surge=True)[0] > hazards.flood_at([6], beach)[0]  # waves


def test_surge_depth_in_feet_stays_inside_the_live_band():
    # Cedar Key downtown: the national data has a depth in whole feet
    raw = hazards.surge_depth("cat3", -83.035, 29.135, None)
    assert raw > 0
    assert 9 <= hazards.surge_depth("cat3", -83.035, 29.135, 4) <= 40
    assert hazards.surge_depth("cat3", -83.035, 29.135, 1) in (1, 2, 3)
    assert hazards.surge_depth("cat3", -83.035, 29.135, 0) == 0
    assert hazards.surge_depth("cat1", -82.33, 29.65, None) == 0  # Gainesville


def test_calibration_keeps_order_and_fades_out():
    """Calibrated wind odds still rise with wind, are lower for houses built
    under the Florida Building Code (2002+), and are zero below 40 mph."""
    old, new = wood_old() | {"green": None}, wood_old() | {"green": None, "year_built": 2010}
    ds = [hazards.wind(g, old, "FL")[0] for g in (30, 60, 90, 120, 150, 180)]
    assert ds == sorted(ds) and ds[0] == 0.0
    assert hazards.wind(120, new, "FL")[0] < hazards.wind(120, old, "FL")[0]
    assert hazards.wind(120, old, "FL")[1] <= hazards.wind(120, old, "FL")[0]


def test_water_damage_grows_with_depth_and_waves():
    slab = {"foundation": "slab", "flood_zone": "AE", "stories": 1}
    v = {"foundation": "piles", "flood_zone": "VE", "stories": 1}
    a = [hazards.flood_at([x], slab, surge=True)[0] for x in (2, 5, 8, 12)]
    assert a == sorted(a)
    assert hazards.flood_at([8], v, surge=True)[0] > hazards.flood_at([8], v | {"flood_zone": "AE"}, surge=True)[0]


def test_repair_cost_follows_calibrated_odds():
    """Repair cost goes with the recalibrated chance of damage: more than raw
    Hazus where surveys found more damage (100 mph), still rising with wind,
    and lower for houses built 2002 or later."""
    old, new = wood_old() | {"green": None}, wood_old() | {"green": None, "year_built": 2010}
    with uncalibrated():
        raw = hazards.wind(100, old, "FL")[2]
    assert hazards.wind(100, old, "FL")[2] > raw
    losses = [hazards.wind(g, old, "FL")[2] for g in (60, 90, 120, 150, 180)]
    assert losses == sorted(losses) and losses[-1] <= 1.0
    assert hazards.wind(130, new, "FL")[2] < hazards.wind(130, old, "FL")[2]


def test_newer_buildings_take_less_water_damage():
    """All three surveyed towns: at the same water, buildings from 2002 or
    later were damaged less often than older ones."""
    b = {"foundation": "slab", "flood_zone": "AE", "stories": 1}
    assert hazards.flood_at([6], b | {"year_built": 2010}, surge=True)[0] < hazards.flood_at([6], b | {"year_built": 1960}, surge=True)[0]


def _house(**kw):
    b = {"material": "wood frame", "year_built": 1985, "stories": 1, "v_ult": 150, "flood_zone": "AE",
         "foundation": "slab", "green": None, "surge": (0, 2, 4, 6, 8), "value": 250000}
    b.update(kw)
    return b


def test_each_what_if_switch_lowers_damage_where_it_should():
    houses = [_house(), _house(year_built=1960, material="masonry"), _house(year_built=None)]
    place = {"gusts": {"cat1": 90, "cat2": 105, "cat3": 120, "cat4": 140, "cat5": 160}}
    w = hazards.what_if(houses, "FL", place)
    t = w["totals"]
    ids = [s["id"] for s in w["switches"]]
    base = t["0"]["hurricane:cat3"]
    for k, sid in enumerate(ids):
        one = t[str(1 << k)]["hurricane:cat3"]
        assert one[0] <= base[0] + 1e-9, sid
    # roof switches change wind only, the raised floor changes water only
    assert t[str(1 << ids.index("raised"))]["hurricane:cat3"][3] < base[3]
    assert t[str(1 << ids.index("hip"))]["hurricane:cat3"][3] == base[3]
    assert t[str(1 << ids.index("hip"))]["flood:ft3"] == t["0"]["flood:ft3"]
    assert t[str(1 << ids.index("raised"))]["flood:ft3"][3] < t["0"]["flood:ft3"][3]
    # all switches together beat any one of them
    every = t[str((1 << len(ids)) - 1)]["hurricane:cat3"][0]
    assert every <= min(t[str(1 << k)]["hurricane:cat3"][0] for k in range(len(ids)))


def test_what_if_as_built_matches_the_square_odds():
    houses = [_house(), _house(year_built=2010, stories=2), _house(material="masonry", flood_zone="X", surge=())]
    place = {"gusts": {"cat1": 90, "cat2": 105, "cat3": 120, "cat4": 140, "cat5": 160}}
    classes, index = hazards.odds_table(houses, "FL", place)
    t = hazards.what_if(houses, "FL", place)["totals"]["0"]
    for sid in ("hurricane:cat1", "hurricane:cat4", "flood:ft3"):
        assert abs(t[sid][0] - sum(classes[i]["odds"][sid][0] for i in index)) < 0.05


def test_hip_roof_matches_the_surveyed_odds_against_gable():
    b = _house(year_built=1990)
    hip = hazards.wind(120, {**b, "roof_shape": "hip"}, "FL")[0]
    gab = hazards.wind(120, {**b, "roof_shape": "gable"}, "FL")[0]
    mix = hazards.wind(120, b, "FL")[0]
    assert hip < mix < gab
    ratio = (hip / (1 - hip)) / (gab / (1 - gab))
    assert abs(ratio - hazards.HIP_ODDS) < 0.01


def test_shutters_pick_shuttered_hazus_models():
    assert all(".sup.1." in m for m, _ in hazards.wind_configs("wood", 1990, 1, 150, "AE", "35", shutters=True))
    assert all(m.split(".")[7] == "1" for m, _ in hazards.wind_configs("masonry", 1990, 1, 150, "AE", "35", shutters=True))
