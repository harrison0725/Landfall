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
    monkeypatch.setattr(wonder, "muse_chat", lambda *a, **k: ('Sure. {"usable": false, "view": "from the ground", "shows": "a house from the street", "reason": "This was taken from the street."}', wonder.MODEL))
    got = wonder.look(_jpeg())
    assert got["usable"] is False and got["view"] == "from the ground" and got["model"] == "Muse Wonder"


def test_usable(monkeypatch):
    monkeypatch.setattr(wonder, "muse_chat", lambda *a, **k: ('{"usable": true, "view": "straight down", "shows": "houses and streets", "reason": ""}', wonder.OPEN_MODEL))
    assert wonder.look(_jpeg())["usable"] is True


@pytest.mark.parametrize("reply", ["", "no json here", '{"usable": "maybe"}'])
def test_unclear_answer_is_no_check(monkeypatch, reply):
    monkeypatch.setattr(wonder, "muse_chat", lambda *a, **k: (reply, wonder.MODEL))
    assert wonder.look(_jpeg()) is None


def test_failure_is_no_check(monkeypatch):
    def boom(*a, **k):
        raise TimeoutError

    monkeypatch.setattr(wonder, "muse_chat", boom)
    assert wonder.look(_jpeg()) is None


def test_open_model_stands_in_when_meta_wont_serve_wonder(monkeypatch):
    import requests

    from landfall import agent

    def fake_chat(url, key, model, *a, **k):
        if url == agent.SPARK_URL:
            r = requests.Response()
            r.status_code = 404
            raise requests.HTTPError(response=r)
        return '{"usable": true, "view": "straight down", "shows": "roofs", "reason": ""}'

    monkeypatch.setenv("BASETEN_API_KEY", "test")
    monkeypatch.setattr(agent, "_chat", fake_chat)
    got = wonder.look(_jpeg())
    assert got["usable"] is True and got["model"] == "Muse Spark Open"
