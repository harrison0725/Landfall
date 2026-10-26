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
