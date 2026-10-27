from landfall.pipeline import storms_between
from landfall.session import Session, _name


def test_pipeline_tools_need_a_box_first(tmp_path):
    s = Session(tmp_path)
    assert "error" in s.call("locate_imagery", {"storm": "helene"})
    assert "error" in s.call("prepare_area", {})
    assert "error" in s.call("area_overview", {})


def test_box_names_are_safe_folder_names():
    n = _name((-82.77, 27.76, -82.76, 27.77))
    assert n == "box_27_7650_82_7650"


def test_storms_between_photo_dates():
    assert storms_between("2023-02-15", "2024-09-26", "helene") == ["Hurricane Idalia (2023-08-30)"]
    assert storms_between("2024-01-01", "2024-09-26", "helene") == []
