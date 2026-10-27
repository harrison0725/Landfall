import json

from landfall.area import Area
from landfall.packet import render
from landfall.priority import assess_need, households, rank

PRE_FIRM = {"floor_low_ft": 1.0, "floor_high_ft": 3.0, "basis": "built 1950", "land_use": "001", "units": 1}
RAISED = {"floor_low_ft": 8.0, "floor_high_ft": 8.0, "basis": "built 2005", "land_use": "001", "units": 1}
MOTEL = {"floor_low_ft": 1.0, "floor_high_ft": 3.0, "basis": "built 1960", "land_use": "039", "units": 0}


def test_households_from_the_roll():
    assert households(None) == 1
    assert households({"land_use": "008", "units": 4}) == 4
    assert households({"land_use": "001", "units": 0}) == 1
    assert households(MOTEL) == 0


def test_deep_water_in_a_low_house_is_likely_displaced():
    r = assess_need({"id": "a", "category": "affected"}, 6.0, PRE_FIRM)
    assert r["likely_displaced"] and r["water_inside_ft"] == [3.0, 5.0]


def test_raised_house_stays_dry():
    r = assess_need({"id": "a", "category": "affected"}, 6.0, RAISED)
    assert not r["possibly_displaced"] and r["water_inside_ft"] == [0.0, 0.0]


def test_destroyed_motel_is_a_business_not_households():
    r = assess_need({"id": "m", "category": "destroyed"}, 6.0, MOTEL)
    assert r["households"] == 0 and r["business_or_lodging_hit"] and not r["likely_displaced"]
    assert any("hotel or motel" in x for x in r["reasons"])


def test_demolished_lot_needs_nobody():
    r = assess_need({"id": "d", "category": "none", "demolished": True}, 6.0, PRE_FIRM)
    assert r["households"] == 0 and not r["business_or_lodging_hit"]


def test_rank_puts_destroyed_and_flooded_first():
    drafts = [{"id": "dry", "category": "none"}, {"id": "gone", "category": "destroyed"}, {"id": "wet", "category": "affected"}]
    out = rank(drafts, {"dry": 0.0, "gone": 6.0, "wet": 6.0}, {"dry": RAISED, "gone": PRE_FIRM, "wet": PRE_FIRM})
    assert [r["id"] for r in out["buildings"]] == ["gone", "wet", "dry"]
    assert out["households_likely_displaced"] == 2


def _town(tmp_path):
    d = tmp_path / "town"
    d.mkdir()
    feats = [{"type": "Feature", "properties": {"id": "b001", "centroid": [-83.03, 29.13], "area_m2": 100}, "geometry": None}]
    drafts = [dict(id="b001", category="affected", confidence=0.6, evidence="roof intact, water <deep>", needs_human=True, flood_ft=6.0, trace=[{"model": "flood rule", "raised_from": "NONE", "to": "AFFECTED"}])]
    (d / "buildings.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    (d / "assessments.json").write_text(json.dumps(drafts))
    (d / "flood.json").write_text(json.dumps({"surface_ft_navd88": 10.9, "observations": [{"source": "USGS HWM 1", "elev_ft": 10.96, "note": "seed line"}], "buildings": [{"id": "b001", "ground_ft": 4.9, "flood_ft": 6.0}]}))
    (d / "alignment.json").write_text(json.dumps({"summary": {"noaa_flight": "f", "naip_date": "2023-02-15"}}))
    (d / "parcels.json").write_text(json.dumps({"b001": {**PRE_FIRM, "year_built": 1950, "flood_zone": "AE", "bfe_ft": 12.0}}))
    (d / "costs.json").write_text(json.dumps({"rate": {"source": "roll", "roll_year": 2026, "usd_per_sqft": 190, "homes": 50}, "total_low_usd": 1e5, "total_high_usd": 2e5,
        "substantial_damage_likely": ["b001"], "substantial_damage_possible": ["b001"], "buildings": [dict(id="b001", sqft=1076, stories=1, value_usd=204400, share_low=0.5, share_high=1.0, cost_low_usd=102200, cost_high_usd=204400, driver="flood", floor_basis="built 1950", substantial_damage_likely=True, substantial_damage_possible=True)]}))
    (d / "needs.json").write_text(json.dumps(rank(drafts, {"b001": 6.0}, {"b001": PRE_FIRM})))
    return d


def test_area_tools_report_costs_and_needs(tmp_path):
    a = Area.load(tmp_path, _town(tmp_path).name)
    assert a.call("repair_costs", {})["substantially_damaged_likely"] == ["b001"]
    n = a.call("urgent_needs", {})
    assert n["households_likely_displaced"] == 1 and n["most_urgent_first"][0]["lat_lon"] == "29.13000, -83.03000"
    assert a.get_building("b001")["displaced"] == "likely"


def test_packet_has_the_evidence_and_escapes_text(tmp_path):
    page = render(_town(tmp_path), "b001", b"png")
    assert "data:image/png;base64,cG5n" in page
    assert "USGS HWM 1" in page and "$102,200 to $204,400" in page and "likely substantially damaged" in page
    assert "water &lt;deep&gt;" in page and "<deep>" not in page
    assert "not an insurance adjuster" in page
