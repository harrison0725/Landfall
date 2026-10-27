import json

from landfall.area import Area, unknown_ids


def make_area(tmp_path):
    d = tmp_path / "town"
    d.mkdir()
    feats = []
    drafts = []
    rows = [
        ("b001", "destroyed", False, 6.0, 0.95, -83.0300),
        ("b002", "affected", True, 2.0, 0.0, -83.0310),
        ("b003", "none", False, 0.0, 0.0, -83.0305),
        ("b004", "minor", True, 0.8, 0.2, -83.0301),
    ]
    for bid, cat, review, flood, loss, lon in rows:
        feats.append({"type": "Feature", "properties": {"id": bid, "centroid": [lon, 29.13], "area_m2": 100}, "geometry": None})
        drafts.append(dict(id=bid, category=cat, confidence=0.8, evidence=f"{cat} evidence", needs_human=review, structure_loss=loss, flood_ft=flood, trace=[]))
    (d / "buildings.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    (d / "assessments.json").write_text(json.dumps(drafts))
    (d / "flood.json").write_text(json.dumps({"surface_ft_navd88": 10.9, "observations": [], "buildings": []}))
    (d / "alignment.json").write_text(json.dumps({"summary": {"noaa_flight": "f", "naip_date": "2023-02-15"}}))
    return Area.load(tmp_path, "town")


def test_overview_counts(tmp_path):
    o = make_area(tmp_path).area_overview()
    assert o["by_category"] == {"none": 1, "affected": 1, "minor": 1, "destroyed": 1}
    assert o["sent_to_human_review"] == 2
    assert o["buildings_with_water_around_them"] == 3


def test_find_at_least_sorted_by_severity(tmp_path):
    r = make_area(tmp_path).find_buildings(at_least="affected")
    assert [x["id"] for x in r["shown"]] == ["b001", "b004", "b002"]


def test_plan_skips_clean_houses_and_walks_nearest_first(tmp_path):
    p = make_area(tmp_path).plan_inspections()
    ids = [s["id"] for s in p["stops"]]
    assert ids[0] == "b001" and "b003" not in ids
    assert ids == ["b001", "b004", "b002"]  # b004 is 10 m from b001, b002 is 100 m
    assert p["walk_m"] > 0


def test_bad_tool_args_come_back_as_errors(tmp_path):
    a = make_area(tmp_path)
    assert "error" in a.call("find_buildings", {"colour": "red"})
    assert "error" in a.call("get_building", {"building_id": "b999"})


def test_unknown_ids(tmp_path):
    assert unknown_ids(make_area(tmp_path), "b001 and b777 and b002") == ["b777"]
