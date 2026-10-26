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
