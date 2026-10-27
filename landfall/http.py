"""One shared HTTPS connection pool for the model APIs.

A plain requests.post opens a new connection each time: a TLS handshake and
a reload of the CA bundle, about 70 ms of CPU per call. An area makes a few
hundred calls, which on a host with a tenth of a CPU is most of a minute.
"""

from __future__ import annotations

import threading

_LOCK = threading.Lock()
_SESSION = None


def post(url: str, **kw):
    global _SESSION
    if _SESSION is None:
        import requests
        from requests.adapters import HTTPAdapter

        with _LOCK:
            if _SESSION is None:
                s = requests.Session()
                adapter = HTTPAdapter(pool_connections=8, pool_maxsize=64)
                s.mount("https://", adapter)
                _SESSION = s
    return _SESSION.post(url, **kw)
