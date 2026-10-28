import json

from landfall import wayback


def test_before_picks_the_newest_photo_taken_before_the_date(tmp_path, monkeypatch):
    # releases every month of 2024; the spot's photo is from 2023-06 until a
    # new one, taken 2024-07-01, arrives in the 2024-08 release
    cfg = {
        str(100 + m): {"itemTitle": f"World Imagery (Wayback 2024-{m:02d}-15)", "metadataLayerUrl": f"https://meta/{m}"}
        for m in range(1, 13)
    }
    (tmp_path / "wayback").mkdir()
    (tmp_path / "wayback" / "config.json").write_text(json.dumps(cfg))
    asked = []

    def fake_get(url):
        m = int(url.split("https://meta/")[1].split("/")[0])
        asked.append(m)
        date = 20240701 if m >= 8 else 20230601
        return json.dumps({"results": [{"attributes": {"SRC_DATE": date, "NICE_DESC": "Maxar", "MinMapLevel": 12, "MaxMapLevel": 19}}]}).encode(), url

    monkeypatch.setattr(wayback, "_get", fake_get)
    p = wayback.before(0.0, 0.0, "2024-09-01", tmp_path)
    assert (p.captured, p.release.date, p.source) == ("2024-07-01", "2024-12-15", "Maxar")
    p = wayback.before(0.0, 0.0, "2024-06-01", tmp_path)
    assert (p.captured, p.release.num) == ("2023-06-01", 107)  # the last release before the new photo
    assert len(asked) <= 10  # a binary search, not a walk through every release
    assert wayback.before(0.0, 0.0, "2023-01-01", tmp_path) is None
