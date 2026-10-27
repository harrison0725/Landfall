import pytest

from landfall.flood import WaterLevel, depth_above_ground, water_surface


def test_water_surface_is_median():
    obs = [WaterLevel("a", 0, 0, 10.96), WaterLevel("b", 0, 0, 10.84), WaterLevel("c", 0, 0, 14.0)]
    assert water_surface(obs) == pytest.approx(10.96)
    assert water_surface(obs[:2]) == pytest.approx(10.90)


def test_depth_above_ground():
    assert depth_above_ground(10.9, 4.6) == pytest.approx(6.3)
    assert depth_above_ground(10.9, 12.0) == 0.0  # high ground stayed dry
    assert depth_above_ground(10.9, None) is None
