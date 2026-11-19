from fastapi.testclient import TestClient

import landfall.wayback as wb
from landfall.app import app


def test_around_says_if_photo_is_after(monkeypatch):
    rel = wb.Release(43291, "2026-10-29", "https://example.invalid/meta")
    monkeypatch.setattr(wb, "releases", lambda cache: [rel])
    monkeypatch.setattr(wb, "captured", lambda r, lon, lat, zoom=19: ("2025-11-11", "Vantor"))
    c = TestClient(app)
    a = c.get("/api/around", params=dict(lon=-82.33, lat=29.65, date="2024-09-26")).json()
    assert a["after"] is True and a["captured"] == "2025-11-11" and "/43291/" in a["tiles"]
    b = c.get("/api/around", params=dict(lon=-82.33, lat=29.65, date="2026-01-01")).json()
    assert b["after"] is False


def test_around_without_photo(monkeypatch):
    monkeypatch.setattr(wb, "releases", lambda cache: [wb.Release(1, "2026-10-29", "x")])
    monkeypatch.setattr(wb, "captured", lambda r, lon, lat, zoom=19: None)
    assert TestClient(app).get("/api/around", params=dict(lon=0, lat=0, date="2024-01-01")).status_code == 404
