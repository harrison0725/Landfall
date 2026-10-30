"""Muse Wonder's photo check: its answer is used only when it is well formed."""

import io

import pytest
from PIL import Image

from landfall import wonder


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (90, 120, 80)).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("META_API_KEY", "test")


def test_not_usable(monkeypatch):
    monkeypatch.setattr(wonder, "_chat", lambda *a, **k: 'Sure. {"usable": false, "view": "from the ground", "shows": "a house from the street", "reason": "This was taken from the street."}')
    got = wonder.look(_jpeg())
    assert got["usable"] is False and got["view"] == "from the ground" and got["model"] == wonder.MODEL


def test_usable(monkeypatch):
    monkeypatch.setattr(wonder, "_chat", lambda *a, **k: '{"usable": true, "view": "straight down", "shows": "houses and streets", "reason": ""}')
    assert wonder.look(_jpeg())["usable"] is True


@pytest.mark.parametrize("reply", ["", "no json here", '{"usable": "maybe"}'])
def test_unclear_answer_is_no_check(monkeypatch, reply):
    monkeypatch.setattr(wonder, "_chat", lambda *a, **k: reply)
    assert wonder.look(_jpeg()) is None


def test_failure_is_no_check(monkeypatch):
    def boom(*a, **k):
        raise TimeoutError

    monkeypatch.setattr(wonder, "_chat", boom)
    assert wonder.look(_jpeg()) is None
