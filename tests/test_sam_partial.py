import pytest

from landfall import pipeline


def test_unanswered_images_are_left_out(monkeypatch):
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)
    logs = []

    def fetch(job):
        if job == ("b2", "post", "roof"):
            raise RuntimeError("404 model_not_found")

    jobs = [(b, side, "roof") for b in ("b1", "b2") for side in ("pre", "post")]
    missing = pipeline._sam_all(jobs, fetch, 4, lambda kind, **kw: logs.append(kind))
    assert missing == {("b2", "post", "roof")}
    assert "warning" in logs


def test_all_unanswered_raises(monkeypatch):
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)

    def fetch(job):
        raise RuntimeError("404 model_not_found")

    with pytest.raises(RuntimeError, match="did not answer"):
        pipeline._sam_all([("b1", "pre", "roof")], fetch, 4, lambda kind, **kw: None)
