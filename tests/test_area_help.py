"""Help search wiring for Area."""
import json

import landfall.help as help_module
from landfall.area import Area


def make_area(tmp_path, label="Cedar Key, Florida", spec=None):
    d = tmp_path / "town"
    d.mkdir(exist_ok=True)
    feats = [{"type": "Feature", "properties": {"id": "b001", "centroid": [-83.03, 29.13], "area_m2": 100}, "geometry": None}]
    drafts = [dict(id="b001", category="minor", confidence=0.8, evidence="minor evidence", needs_human=False, structure_loss=0.2, flood_ft=0.8, trace=[])]
    (d / "buildings.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    (d / "assessments.json").write_text(json.dumps(drafts))
    (d / "alignment.json").write_text(json.dumps({"summary": {"noaa_flight": "f", "naip_date": "2023-02-15"}}))
    (d / "label.txt").write_text(label + "\n")
    if spec is not None:
        (d / "spec.json").write_text(json.dumps(spec))
    return Area.load(tmp_path, "town")


def test_load_reads_place_and_storm_disaster(tmp_path):
    a = make_area(tmp_path, spec={"storm": "helene", "landfall": "2024-09-26"})
    assert a.meta["place"] == "Cedar Key, Florida"
    assert a.meta["disaster"] == "Hurricane Helene (2024)"


def test_load_falls_back_to_event_name(tmp_path):
    a = make_area(tmp_path, label="Mokwa, Nigeria", spec={"storm": "nope", "event_name": "Mokwa flood 2025"})
    assert a.meta["place"] == "Mokwa, Nigeria"
    assert a.meta["disaster"] == "Mokwa flood 2025"


def test_find_help_calls_search_with_place_and_disaster(tmp_path, monkeypatch):
    a = make_area(tmp_path, spec={"storm": "helene", "landfall": "2024-09-26"})
    seen = {}

    def fake(place, disaster, question):
        seen["args"] = (place, disaster, question)
        return {"answer": "fake help"}

    monkeypatch.setattr(help_module, "find_help", fake)
    out = a.call("find_help", {"question": "where is the shelter?"})
    assert out == {"answer": "fake help"}
    assert seen["args"] == ("Cedar Key, Florida", "Hurricane Helene (2024)", "where is the shelter?")
