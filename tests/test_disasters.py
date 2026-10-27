from landfall import agent
from landfall.fema import Category, Measures, verify
from landfall.flood import WaterLevel, local_surface
from landfall.pipeline import storms_between
from landfall.storms import STORMS


def test_every_disaster_has_a_prompt():
    for s in STORMS.values():
        text = agent.system(s.kind)
        assert "Answer with JSON only" in text
        assert ("after a hurricane" in text) == (s.kind == "hurricane")


def test_hurricane_prompt_is_the_default():
    assert agent.SYSTEM == agent.system("hurricane") == agent.system("unknown")


def test_earthquake_call_may_exceed_roof_measure():
    # a pancaked building can keep a whole roof slab
    m = dict(roof_loss=0.0, tarp_share=0.0, debris_share=0.0, flood_ft=None, aligned=True)
    assert not verify(Category.DESTROYED, Measures(**m)).ok
    assert verify(Category.DESTROYED, Measures(**m, kind="earthquake")).ok


def test_river_surface_follows_nearest_marks():
    marks = [WaterLevel("up", -99.30, 30.07, 1720.0), WaterLevel("down", -99.29, 30.07, 1700.0)]
    up = local_surface(marks, -99.2995, 30.07)
    down = local_surface(marks, -99.2905, 30.07)
    assert up > 1712 and down < 1708
    assert local_surface(marks, -99.0, 30.07) is None  # no mark within 1 km


def test_other_coasts_dont_warn_about_florida_hurricanes():
    assert storms_between("2022-05-01", "2025-01-07", "la_fires") == []
    assert storms_between("2023-02-15", "2024-09-26", "helene") == ["Hurricane Idalia (2023-08-30)"]
