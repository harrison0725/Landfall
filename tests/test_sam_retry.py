import numpy as np
import requests

from landfall import sam


class _Resp:
    def __init__(self, code, text, payload=None):
        self.status_code, self.text, self._payload = code, text, payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def test_model_not_found_is_retried(monkeypatch):
    answers = [_Resp(404, '{"error":{"code":"model_not_found"}}')] * 3 + [
        _Resp(200, "", {"output": [{"content": [{"type": "output_text", "text": "ok"}]}]})
    ]
    monkeypatch.setattr(sam.http, "post", lambda *a, **k: answers.pop(0))
    monkeypatch.setattr(sam.time, "sleep", lambda s: None)
    assert sam.request(np.zeros((8, 8, 3), np.uint8), "roof", api_key="k") == "ok"
    assert answers == []


def test_hung_request_is_retried(monkeypatch):
    calls = []

    def post(*a, **k):
        calls.append(k["timeout"])
        if len(calls) == 1:
            raise requests.Timeout("read timed out")
        return _Resp(200, "", {"output": [{"content": [{"type": "output_text", "text": "ok"}]}]})

    monkeypatch.setattr(sam.http, "post", post)
    assert sam.request(np.zeros((8, 8, 3), np.uint8), "roof", api_key="k") == "ok"
    assert len(calls) == 2 and calls[0][1] == sam.TIMEOUT_S
