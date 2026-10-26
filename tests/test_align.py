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
