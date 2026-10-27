from landfall.fema import INTERNATIONAL
from landfall.maxar import Pass, _resolve, _touches, pick
from landfall.pipeline import storms_between


def _p(date, cover=1.0, clouds=0.0, nadir=10.0):
    return Pass(date, date, "WV03", 0.3, nadir, clouds, cover, ())


def test_resolve_relative_links():
    base = "https://x/events/E/ard/acquisition_collections/"
    assert _resolve(base, "../17/1220/2022-10-13/A.json") == "https://x/events/E/ard/17/1220/2022-10-13/A.json"
    assert _resolve("https://x/a/b/", "./A-visual.tif") == "https://x/a/b/A-visual.tif"


def test_touches():
    assert _touches((0, 0, 1, 1), (0.5, 0.5, 2, 2))
    assert not _touches((0, 0, 1, 1), (1.5, 0, 2, 1))


def test_pick_after_takes_earliest_clear_full_pass():
    found = [_p("2025-11-02"), _p("2025-10-30", clouds=45), _p("2025-10-31"), _p("2025-10-29", cover=0.6), _p("2025-10-20")]
    assert [p.date for p in pick(found, "2025-10-28", True)] == ["2025-10-31", "2025-11-02", "2025-10-30", "2025-10-29"]


def test_pick_before_takes_newest_and_drops_cloudy():
    found = [_p("2022-10-13"), _p("2025-04-06"), _p("2025-10-20", clouds=80), _p("2025-10-29")]
    assert [p.date for p in pick(found, "2025-10-28", False)] == ["2025-04-06", "2022-10-13"]


def test_storms_between_only_counts_storms_in_the_same_place():
    assert storms_between("2022-10-13", "2025-10-28", "melissa") == []
    assert storms_between("2017-08-29", "2022-09-18", "fiona") == ["Hurricane Maria (2017-09-20)"]
    assert "Hurricane Idalia (2023-08-30)" in storms_between("2023-02-15", "2024-09-26", "helene")


def test_international_grades_cover_every_category():
    assert set(INTERNATIONAL) == {"none", "affected", "minor", "major", "destroyed"}
    assert INTERNATIONAL["major"] == "Damaged"
