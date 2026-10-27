import pytest

from landfall.agent import parse_answer
from landfall.fema import Category


def test_parse_answer_inside_text():
    a = parse_answer('Sure.\n{"category": "Destroyed", "confidence": 0.9, "evidence": "empty slab", "needs_human": false}')
    assert a["category"] is Category.DESTROYED
    assert a["confidence"] == 0.9


def test_parse_answer_rejects_unknown_category():
    with pytest.raises(ValueError):
        parse_answer('{"category": "catastrophic", "confidence": 1}')
    with pytest.raises(ValueError):
        parse_answer("no json here")


def test_describe_mentions_debris_and_tarp_only_when_found():
    from landfall.agent import describe
    from landfall.fema import Measures

    seen = describe(Measures(0.0, 0.3, 0.12, None, True), {"structure_loss": 0.0, "debris_share": 0.12})
    assert "debris piles over 12%" in seen and "30% of the roof as a blue tarp" in seen
    clean = describe(Measures(0.0, 0.0, 0.0, None, True), {"structure_loss": 0.0, "debris_share": 0.0})
    assert "found no debris" in clean and "tarp" not in clean
