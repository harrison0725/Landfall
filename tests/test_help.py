"""The help search keeps the last answer and only https sources."""

from landfall import help as H


class _Resp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def test_answer_and_sources(monkeypatch):
    monkeypatch.setenv("META_API_KEY", "test")
    out = [
        {"type": "message", "content": [{"type": "output_text", "text": "I'll search FEMA.", "annotations": []}]},
        {"type": "web_search_call"},
        {"type": "message", "content": [{"type": "output_text", "text": "Apply at DisasterAssistance.gov.", "annotations": [
            {"url": "https://www.fema.gov/disaster/4828", "title": "FEMA"},
            {"url": "https://WWW.fema.gov/disaster/4828", "title": "dup"},
            {"url": "javascript:alert(1)", "title": "bad"},
        ]}]},
    ]
    monkeypatch.setattr(H.http, "post", lambda *a, **k: _Resp({"output": out}))
    got = H.find_help("Cedar Key, Florida", "Hurricane Helene (2024)", "where do I apply?")
    assert got["answer"] == "Apply at DisasterAssistance.gov."
    assert got["sources"] == [{"title": "FEMA", "url": "https://www.fema.gov/disaster/4828"}]


def test_failure_is_an_error_not_a_crash(monkeypatch):
    monkeypatch.setenv("META_API_KEY", "test")

    def boom(*a, **k):
        raise TimeoutError

    monkeypatch.setattr(H.http, "post", boom)
    assert "error" in H.find_help("x", "y", "z")
