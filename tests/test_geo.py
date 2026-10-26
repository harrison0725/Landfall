import pytest

from landfall.geo import building_window, lonlat_to_pixel, meters_per_pixel, pixel_to_lonlat

CEDAR_KEY = (-83.0349, 29.1386)


def test_round_trip():
    x, y = lonlat_to_pixel(*CEDAR_KEY, 19)
    lon, lat = pixel_to_lonlat(x, y, 19)
    assert lon == pytest.approx(CEDAR_KEY[0], abs=1e-9)
    assert lat == pytest.approx(CEDAR_KEY[1], abs=1e-9)


def test_resolution_at_cedar_key():
    # zoom 19 is about 0.26 m per pixel at this latitude
    assert meters_per_pixel(CEDAR_KEY[1], 19) == pytest.approx(0.26, abs=0.01)


def test_building_window_padding():
    lon, lat = CEDAR_KEY
    d = 0.0001  # roughly a 10 m house
    outline = [(lon, lat), (lon + d, lat), (lon + d, lat + d), (lon, lat + d)]
    tight = building_window(outline, 19, margin_m=0)
    padded = building_window(outline, 19, margin_m=8)
    pad_px = 8 / meters_per_pixel(lat, 19)
    assert padded.width - tight.width == pytest.approx(2 * pad_px, abs=2)
    assert padded.tiles()  # touches at least one tile
