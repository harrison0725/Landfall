import json

from landfall import app


def _area(root, name, storm, bbox, tiles=None):
    d = root / name
    d.mkdir()
    (d / "spec.json").write_text(json.dumps({"name": name, "storm": storm, "bbox": bbox}))
    if tiles is not None:
        (d / "tiles.json").write_text(json.dumps(tiles))
    return d


def test_new_box_reuses_tiles_of_same_storm(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "ROOT", tmp_path)
    sharp = {"url": "/s/{z}", "max_native_zoom": 18, "bounds": [[18.45, -77.93], [18.47, -77.90]]}
    wide = {"url": "/w/{z}", "max_native_zoom": 15, "bounds": [[18.35, -78.1], [18.55, -77.7]]}
    _area(tmp_path, "montego_bay", "melissa", [-77.92, 18.46, -77.91, 18.47], [wide, sharp])
    _area(tmp_path, "elsewhere", "helene", [-77.92, 18.46, -77.91, 18.47], [{**wide, "url": "/other/{z}"}])

    inside = _area(tmp_path, "box_a", "melissa", [-77.9193, 18.4685, -77.9185, 18.4695])
    assert [t["url"] for t in app._shared_tiles(inside)] == ["/w/{z}", "/s/{z}"]

    outside_sharp = _area(tmp_path, "box_b", "melissa", [-77.80, 18.40, -77.79, 18.41])
    assert [t["url"] for t in app._shared_tiles(outside_sharp)] == ["/w/{z}"]

    far = _area(tmp_path, "box_c", "melissa", [-76.80, 18.00, -76.79, 18.01])
    assert app._shared_tiles(far) == []
