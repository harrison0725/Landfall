import json

from landfall.app import _seed


def test_seed_refreshes_a_demo_area_already_on_disk(tmp_path):
    demo, root = tmp_path / "demo", tmp_path / "data"
    (demo / "town" / "crops").mkdir(parents=True)
    (demo / "town" / "assessments.json").write_text(json.dumps([{"id": "b1", "category": "none"}]))
    (demo / "town" / "crops" / "b1_post.png").write_bytes(b"new")
    (root / "town" / "crops").mkdir(parents=True)
    (root / "town" / "assessments.json").write_text(json.dumps([{"id": "b1", "category": "affected"}]))
    (root / "town" / "crops" / "b1_post.png").write_bytes(b"old")
    (root / "town" / "cards").mkdir()
    (root / "drawn").mkdir()  # an area someone drew is left alone
    (root / "drawn" / "assessments.json").write_text("[]")
    _seed(root, demo)
    assert json.loads((root / "town" / "assessments.json").read_text())[0]["category"] == "none"
    assert (root / "town" / "crops" / "b1_post.png").read_bytes() == b"new"
    assert not (root / "town" / "cards").exists()
    assert (root / "drawn" / "assessments.json").read_text() == "[]"
