import numpy as np
import pytest

from landfall.measure import coverage, polygon_mask, roof_loss


def test_polygon_mask_area():
    m = polygon_mask([(10, 10), (30, 10), (30, 20), (10, 20)], (40, 40))
    assert m.sum() == pytest.approx(21 * 11, abs=2)


def test_coverage_counts_only_inside_footprint():
    fp = np.zeros((20, 20), bool)
    fp[5:15, 5:15] = True
    roof = np.zeros_like(fp)
    roof[0:10, 0:20] = True  # half the house plus some neighbor
    assert coverage([roof], fp) == pytest.approx(0.5)
    assert coverage([], fp) == 0.0


def test_roof_loss():
    assert roof_loss(0.9, 0.9) == 0.0
    assert roof_loss(0.8, 0.2) == pytest.approx(0.75)
    assert roof_loss(0.8, 0.0) == 1.0
    assert roof_loss(0.1, 0.0) is None  # pre-storm roof not visible: no baseline


from landfall.measure import best_overlap, structure_mask  # noqa: E402


def test_structure_mask_drops_the_neighbor():
    fp = np.zeros((40, 40), bool)
    fp[10:30, 10:30] = True
    mine = np.zeros_like(fp)
    mine[12:28, 12:28] = True
    neighbor = np.zeros_like(fp)
    neighbor[0:12, 25:40] = True  # touches the outline at a corner only
    m = structure_mask([mine, neighbor], fp)
    assert m[20, 20] and not m[2, 38]


def test_best_overlap_finds_a_leaning_house():
    pre = np.zeros((40, 40), bool)
    pre[10:30, 12:28] = True
    pad = 10
    post_ctx = np.zeros((60, 60), bool)
    post_ctx[10 + pad + 6 : 30 + pad + 6, 12 + pad - 4 : 28 + pad - 4] = True  # same house, 6 down, 4 left
    share, dx, dy = best_overlap(pre, post_ctx, pad)
    assert share == pytest.approx(1.0)
    assert (dx, dy) == (-4, 6)


def test_best_overlap_gone():
    pre = np.zeros((40, 40), bool)
    pre[10:30, 12:28] = True
    assert best_overlap(pre, np.zeros((60, 60), bool), 10)[0] == 0.0


def test_tree_over_roof_is_not_roof_loss():
    import numpy as np

    from landfall.measure import canopy_mask, under_trees

    rng = np.random.default_rng(0)
    pre = np.zeros((40, 40), bool)
    pre[10:30, 10:30] = True
    post_ctx = np.zeros((60, 60), bool)  # post window padded by 10 px
    post_ctx[20:40, 20:40] = True
    post_ctx[20:40, 30:40] = False  # right half of the roof not found...
    photo = np.full((60, 60, 3), 120, np.uint8)
    leaves = rng.integers(0, 50, (20, 10))
    photo[20:40, 30:40] = np.stack([40 + leaves, 110 + leaves, 40 + leaves], -1)  # ...because leafy canopy is over it
    loss, trees = under_trees(pre, post_ctx, canopy_mask(photo), 0, 0, 10)
    assert trees > 0.4 and loss < 0.1

    # the same gap with bare ground over it is loss
    loss, trees = under_trees(pre, post_ctx, canopy_mask(np.full((60, 60, 3), 120, np.uint8)), 0, 0, 10)
    assert trees == 0 and loss > 0.4


def test_green_metal_roof_is_not_canopy():
    import numpy as np

    from landfall.measure import canopy_mask

    roof = np.zeros((40, 40, 3), np.uint8)
    roof[:] = (60, 130, 70)  # smooth green sheet
    assert canopy_mask(roof).mean() < 0.05
