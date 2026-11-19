import json

import numpy as np
from PIL import Image

from landfall import agent, street
from landfall.agent import parse_answer
from landfall.fema import Category, Measures
from landfall.street import Shot, pick, view_toward

HOUSE = (-83.0350, 29.1370)
M = 1 / 111_320  # about a metre of latitude, in degrees


def shot(i, dlat_m, heading, pano=False, seq="s1"):
    return Shot(str(i), "2024-10-05", HOUSE[0], HOUSE[1] - dlat_m * M, heading, pano, f"https://x/{i}", seq, "someone")


def test_pick_keeps_photos_that_look_at_the_house():
    shots = [
        shot(1, 15, 0),  # 15 m south, looking north: at the house
        shot(2, 15, 180, seq="s2"),  # same spot, looking away
        shot(3, 80, 0, seq="s3"),  # too far
        shot(4, 25, 90, pano=True, seq="s4"),  # panorama: any heading works
        shot(5, 20, 50, seq="s5"),  # glancing, 50 degrees off
    ]
    got = pick(shots, *HOUSE)
    assert [p["image_id"] for p in got] == ["1", "4"]
    assert got[0]["bearing"] == 0 and got[0]["offset_deg"] == 0
    assert abs(got[0]["distance_m"] - 15) < 0.5


def test_pick_prefers_different_passes():
    shots = [shot(1, 10, 0, seq="a"), shot(2, 11, 0, seq="a"), shot(3, 12, 0, seq="a"), shot(4, 14, 0, seq="b")]
    got = pick(shots, *HOUSE, per_building=2)
    assert [p["image_id"] for p in got] == ["1", "4"]


def test_view_toward_cuts_the_panorama_toward_the_building():
    w, h = 360, 100
    a = np.zeros((h, w, 3), np.uint8)
    a[:, 270:, 0] = 255  # mark the part of the panorama 90 to 180 degrees right of its heading
    pano = Image.fromarray(a)
    # camera heads north (0); the building is east-southeast (135): middle column + 135 deg
    out = np.asarray(view_toward(pano, 0, 135, True, fov=90))
    assert out.shape[1] == 90
    assert out[:, :, 0].mean() > 250  # the view is all the marked part
    # across the seam: the building is right behind (180)
    out = np.asarray(view_toward(pano, 0, 180, True, fov=90))
    assert out.shape[1] == 90
    assert out[:, :45, 0].mean() > 250 and out[:, 45:, 0].mean() < 5
    flat = Image.fromarray(a)
    assert view_toward(flat, 0, 135, False) is flat


def test_fetch_does_nothing_without_a_token(tmp_path, monkeypatch):
    monkeypatch.delenv("MAPILLARY_ACCESS_TOKEN", raising=False)

    class Spec:
        name = "x"
        landfall = "2024-09-26"

    assert street.fetch(Spec, tmp_path) == {"skipped": "no token"}
    assert street.load(tmp_path / "x") == {}


def test_parse_answer_reads_first_floor():
    a = parse_answer('{"category": "minor", "confidence": 0.7, "first_floor_ft": "6.5", "street_evidence": "raised on pilings"}')
    assert a["first_floor_ft"] == 6.5 and a["street_evidence"] == "raised on pilings"
    assert parse_answer('{"category": "minor", "first_floor_ft": null}')["first_floor_ft"] is None
    assert parse_answer('{"category": "minor", "first_floor_ft": 400}')["first_floor_ft"] is None


def _fake_chat(reply: dict):
    calls = []

    def chat(url, key, model, messages, max_tokens=8000, schema=None):
        calls.append(messages)
        return json.dumps(reply)

    return chat, calls


def _run(monkeypatch, reply, flood_ft, photos):
    monkeypatch.setenv("BASETEN_API_KEY", "k")
    monkeypatch.setenv("META_API_KEY", "k")
    chat, calls = _fake_chat(reply)
    monkeypatch.setattr(agent, "_chat", chat)
    img = np.full((40, 40, 3), 120, np.uint8)
    m = Measures(0.0, 0.0, 0.0, flood_ft, True)
    a = agent.assess("b1", img, img, [(5, 5), (30, 5), (30, 30), (5, 30)], m, {"structure_loss": 0.0, "status": "matched"},
                     escalate=False, street=photos)
    return a, calls


PHOTO = {"captured": "2024-10-05", "distance_m": 14.0, "bearing": 10, "is_pano": False, "data_url": "data:image/jpeg;base64,AAAA"}


def test_street_photos_go_to_the_model_and_their_floor_feeds_the_flood_note(monkeypatch):
    reply = {"category": "affected", "confidence": 0.8, "evidence": "roof intact", "needs_human": False,
             "first_floor_ft": 1.0, "street_evidence": "slab house, waterline on the siding"}
    a, calls = _run(monkeypatch, reply, flood_ft=4.0, photos=[PHOTO])
    user = calls[0][1]["content"]
    assert sum(p["type"] == "image_url" for p in user) == 2  # aerial pair + one street photo
    assert "Street photo 1" in json.dumps(user) and "first_floor_ft" in calls[0][0]["content"]
    # 4 ft of water, floor 1 ft up: 3 ft in the living space, stated as flooding, not damage
    assert a.category is Category.AFFECTED and a.first_floor_ft == 1.0 and a.street_photos == 1
    assert a.flood["living_ft"] == 3.0 and "inside the living space" in a.flood["label"]
    assert "waterline" in a.evidence


def test_raised_house_stays_affected(monkeypatch):
    reply = {"category": "affected", "confidence": 0.8, "evidence": "roof intact", "needs_human": False, "first_floor_ft": 9.0}
    a, _ = _run(monkeypatch, reply, flood_ft=4.0, photos=[PHOTO])
    assert a.category is Category.AFFECTED and not a.needs_human
    assert "below the living floor" in a.flood["label"]


def test_without_street_photos_nothing_changes(monkeypatch):
    reply = {"category": "none", "confidence": 0.8, "evidence": "roof intact", "needs_human": False, "first_floor_ft": 0.0}
    a, calls = _run(monkeypatch, reply, flood_ft=4.0, photos=None)
    assert "Street" not in calls[0][0]["content"]
    assert a.category is Category.NONE  # flooding doesn't raise the category
    assert a.flood["flooded"] and a.flood["living_ft"] is None
    assert a.first_floor_ft is None and a.street_photos == 0
