"""Speech to text with Muse Voice Transcribe on Meta Model API.

The batch endpoint takes a multipart form with a JSON `request` part and the
audio as 16-bit mono WAV; the browser records and encodes it that way.
"""

from __future__ import annotations

import json
import os

URL = "https://api.meta.ai/v1/asr/transcribe"
MODEL = "muse-voice-transcribe-1.0"


def transcribe(wav: bytes, sample_rate: int = 16000) -> str:
    import requests

    r = requests.post(
        URL,
        headers={"Authorization": f"Bearer {os.environ['META_API_KEY']}"},
        files={
            "request": (None, json.dumps({"model": MODEL, "audioEncoding": "WAV", "sampleRateHertz": sample_rate}), "application/json"),
            "audio": ("speech.wav", wav, "audio/wav"),
        },
        timeout=60,
    )
    r.raise_for_status()
    return (r.json().get("transcript") or "").strip()
