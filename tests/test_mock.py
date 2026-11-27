"""Mock mode: the app answers and replays a run with no API keys and no network."""

from pathlib import Path

import pytest

from landfall import mock
from landfall.session import Session

DEMO = Path(__file__).resolve().parents[1] / "demo"


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    monkeypatch.delenv("META_API_KEY", raising=False)
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    monkeypatch.setattr(mock, "PAUSE", 0)


def test_mock_is_on_without_keys():
    assert mock.enabled()


def test_question_is_answered_from_saved_results():
    s = Session(DEMO, "cedar_key")
    text = s.turn("Which buildings should be visited first?")
    assert "walking order" in text and "Mock mode" in text


def test_drawn_box_replays_the_nearest_example():
    events = []
    s = Session(DEMO)
    s.on_event = lambda kind, **f: events.append((kind, f))
    text = s.turn("Assess this area", box=(-83.036, 29.136, -83.032, 29.139), storm="helene")
    assert s.area == "cedar_key"
    assert "replays the recorded run of Cedar Key" in text
    tools = [f["name"] for k, f in events if k == "tool"]
    assert tools[:7] == mock.PIPELINE
    assert any(k == "progress" and f["event"] == "assessed" for k, f in events)


def test_uploaded_photos_are_declined_in_mock_mode():
    s = Session(DEMO)
    text = s.turn("Assess this", box=(-83.036, 29.136, -83.032, 29.139), upload="abc123")
    assert "can't check uploaded photos" in text


def test_square_event_carries_the_outlook_for_the_map_panel(monkeypatch):
    from landfall import odds

    fake = {"square": "Sec 1", "points": [], "scenario": "hurricane:cat3", "classes": [], "site": {}, "groups": [],
            "outlook": {"periods": {"30": {}}, "water": {"series": [1, 2]}, "tornado": None}}
    monkeypatch.setattr(odds, "estimate", lambda *a, **k: fake)
    events = []
    s = Session(DEMO)
    s.on_event = lambda kind, **f: events.append((kind, f))
    s.cell = {"polygon": [[0, 0], [1, 0], [1, 1], [0, 0]], "label": "Sec 1"}
    brief = s._square()
    groups = [f for k, f in events if k == "groups"][0]
    assert groups["outlook"] is fake["outlook"]
    assert "water" not in brief["outlook"] and "periods" in brief["outlook"]
