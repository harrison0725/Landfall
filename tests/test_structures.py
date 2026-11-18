from landfall.structures import code_era, describe, match, merge


def _feat(bid, lon, lat, d=0.0001):
    ring = [[lon - d, lat - d], [lon + d, lat - d], [lon + d, lat + d], [lon - d, lat + d], [lon - d, lat - d]]
    return {"properties": {"id": bid, "centroid": [lon, lat]}, "geometry": {"type": "Polygon", "coordinates": [ring]}}


def test_match_prefers_a_point_inside_then_the_nearest_close_one():
    feats = [_feat("a", -83.0, 29.0), _feat("b", -83.001, 29.0), _feat("c", -83.01, 29.0)]
    pts = [{"x": -83.0, "y": 29.0, "sqft": 900, "fd_id": 1}, {"x": -83.00102, "y": 29.00012, "sqft": 500, "fd_id": 2}]
    m = match(feats, pts)
    assert m["a"]["fd_id"] == 1 and m["b"]["fd_id"] == 2 and m["c"] is None  # c is ~1 km away


def test_code_era_from_year():
    assert "pre-1975" in code_era(1960)
    assert "1975-2001" in code_era(1990)
    assert "Florida Building Code" in code_era(2005)
    assert code_era(None) is None
    assert "1975 or later" in code_era(1990, None)


def test_describe_and_street_photos_win_over_the_inventory():
    nsi = {"bldgtype": "M", "found_type": "I", "found_ht": 8, "num_story": 2, "sqft": 1200, "occtype": "RES1-2SNB", "fd_id": 7, "med_yr_blt": 1982}
    rec = describe(nsi, {"parcel": {"ACT_YR_BLT": 1968, "EFF_YR_BLT": 1985}})
    assert rec["year_built"] == 1968 and rec["material"] == "masonry" and "pre-1975" in rec["code_era"]
    out = merge(rec, {"material": "wood", "foundation": None, "stories": 1})
    assert out["material"] == "wood frame" and out["material_source"] == "street photos"
    assert out["foundation"] == "piles" and "Inventory" in out["foundation_source"]
    assert out["stories"] == 1 and "street photos" in out["sources"]


def test_without_a_tax_roll_year_the_block_median_is_marked_as_such():
    rec = describe({"bldgtype": "W", "med_yr_blt": 1989}, None, None)
    assert rec["year_built"] is None and rec["block_median_year_built"] == 1989 and rec["code_era"] is None


def test_code_era_depends_on_the_disaster():
    assert "pre-2008" in code_era(1950, "CA", "wildfire")
    assert "2008 or later" in code_era(2015, "CA", "wildfire")
    assert "pre-1976" in code_era(1960, "CA", "earthquake")
    assert "Myanmar" in code_era(2015, None, "earthquake")
    assert "flood-elevation" in code_era(1960, "TX", "flood")


def test_fire_inspection_gives_year_and_fire_features():
    dins = {"YEARBUILT": 1941, "ROOFCONSTR": "Asphalt", "EAVES": "Unenclosed", "VENTSCREEN": "Unknown",
            "EXTERIORSI": "Wood", "STRUCTURET": "Single Family Residence Single Story"}
    rec = describe({"bldgtype": "M", "num_story": 1}, None, "CA", "wildfire", dins)
    assert rec["year_built"] == 1941 and "inspection" in rec["year_source"] and "pre-2008" in rec["code_era"]
    assert rec["fire"] == {"roof": "Asphalt", "eaves": "Unenclosed", "siding": "Wood"}  # unknowns left out
    assert rec["material"] == "wood siding"
    out = merge(rec, {"material": "masonry", "foundation": None, "stories": 2})
    assert out["material"] == "wood siding" and out["stories"] == 2  # an inspector beats the photos
