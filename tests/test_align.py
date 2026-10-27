import numpy as np
import pytest

from landfall.align import apply_offset, find_offset


def scene(h=96, w=96, seed=0):
    """A fake aerial crop: a few bright 'roofs' and a 'road' on noise."""
    rng = np.random.default_rng(seed)
    img = rng.normal(100, 5, (h, w))
    img[30:55, 25:60] = 200  # house
    img[60:70, 10:40] = 170  # shed
    img[:, 75:82] = 60  # road
    return img


def shifted(img, dx, dy):
    """post[y + dy, x + dx] = pre[y, x]"""
    return np.roll(np.roll(img, dy, axis=0), dx, axis=1)


@pytest.mark.parametrize("dx,dy", [(0, 0), (3, -2), (-7, 5), (10, 10)])
def test_recovers_known_shift(dx, dy):
    pre = scene()
    post = shifted(pre, dx, dy)
    al = find_offset(pre, post, max_shift=12)
    assert (al.dx, al.dy) == (dx, dy)
    assert al.confident


def test_survives_lighting_change():
    pre = scene()
    post = shifted(pre, 4, -3) * 0.6 + 40  # darker, lower contrast flight
    al = find_offset(pre, post)
    assert (al.dx, al.dy) == (4, -3)


def test_flat_crop_is_not_confident():
    pre = scene()
    post = np.full_like(pre, 120.0)  # e.g. everything washed out
    assert not find_offset(pre, post).confident


def test_apply_offset_overlays_pre():
    pre = scene()
    post = shifted(pre, 5, -4)
    back = apply_offset(post, find_offset(pre, post))
    core = (slice(12, -12), slice(12, -12))
    assert np.allclose(back[core], pre[core])


def test_shape_mismatch_rejected():
    with pytest.raises(ValueError):
        find_offset(np.zeros((50, 50)), np.zeros((50, 60)))


# --- match_building / place_buildings ---------------------------------------

from landfall.align import Alignment, match_building, place_buildings  # noqa: E402


def town(h=160, w=160, seed=1):
    rng = np.random.default_rng(seed)
    img = rng.normal(90, 6, (h, w))
    img[60:95, 55:105] = 210  # the house we match
    img[20:40, 10:50] = 180
    img[110:140, 120:150] = 190
    img[:, 140:146] = 50  # road
    return np.clip(img, 0, 255).astype(np.uint8)


def test_match_building_finds_roof_shift_despite_exposure():
    pre = town()
    post = np.clip(shifted(pre.astype(float), 9, -6) * 0.4 + 10, 0, 255).astype(np.uint8)  # dark flight
    al = match_building(pre, post, box=(47, 52, 113, 103), max_shift=28)
    assert (al.dx, al.dy) == (9, -6)
    assert al.score > 0.5


def test_match_building_box_must_fit():
    pre = town()
    with pytest.raises(ValueError):
        match_building(pre, pre, box=(5, 5, 60, 60), max_shift=28)


def _al(dx, dy, score=0.5, margin=0.1):
    return Alignment(dx, dy, score, margin)


def test_place_buildings_trusts_agreeing_neighbors():
    centers = [(0, 0), (10, 0), (20, 0), (30, 0)]
    matches = [_al(5, 2), _al(6, 2), _al(5, 3), _al(6, 3, score=0.21, margin=0.0)]
    places = place_buildings(matches, centers)
    assert [p.status for p in places] == ["matched"] * 4


def test_place_buildings_falls_back_to_neighbors_for_lost_roof():
    # building 3's roof is gone: weak, far-off peak -> placed by its neighbors
    centers = [(0, 0), (10, 0), (20, 0), (30, 0)]
    matches = [_al(5, 2), _al(6, 2), _al(5, 3), _al(-20, 17, score=0.19, margin=0.01)]
    p = place_buildings(matches, centers)[3]
    assert p.status == "neighbors" and not p.aligned
    assert (p.dx, p.dy) == (5, 2)


def test_place_buildings_keeps_a_clear_peak_on_a_tall_building():
    # a tall house leans further than its neighbors, but its own peak is clear
    centers = [(0, 0), (10, 0), (20, 0), (30, 0)]
    matches = [_al(5, 2), _al(6, 2), _al(5, 3), _al(14, 10, score=0.45, margin=0.14)]
    p = place_buildings(matches, centers)[3]
    assert p.status == "matched" and (p.dx, p.dy) == (14, 10)
