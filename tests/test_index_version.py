from fastapi.testclient import TestClient

from landfall.app import app


def test_index_versions_scripts():
    r = TestClient(app).get("/")
    assert r.status_code == 200
    assert "no-cache" in r.headers["cache-control"]
    assert '/static/app.js?v=' in r.text
    assert '/static/style.css?v=' in r.text
