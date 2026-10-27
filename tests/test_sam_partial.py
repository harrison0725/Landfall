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


def test_outage_stops_calling(monkeypatch):
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)
    calls = []

    def fetch(job):
        calls.append(job)
        raise RuntimeError("404 model_not_found")

    jobs = [(f"b{i}", "pre", "roof") for i in range(200)]
    texts = []
    missing = pipeline._sam_all(jobs, fetch, 1, lambda kind, **kw: texts.append(kw.get("text", "")))
    assert missing == set(jobs)
    assert len(calls) == pipeline.OUTAGE_SAMPLE
    assert any("not answering" in t for t in texts)
