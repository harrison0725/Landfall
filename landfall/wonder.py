"""Muse Wonder's first look at an uploaded photo.

Finding where a photo was taken takes a minute or more, and a photo that
can't be found (taken from the street, from a low angle, a screenshot) used
to cost the user that whole wait before being turned away. Muse Wonder looks
at the photo the moment it is uploaded and says whether it is one Landfall
can use: taken from above, looking down on buildings. It only screens the
photo; it never judges a building's damage, which stays with the per-building
agent and its measurements.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re

from .agent import SPARK_URL, _chat

MODEL = "muse-wonder-1.0"

PROMPT = """Someone uploaded this photo to a tool that finds the buildings in an aerial photo on a map and compares them with an older aerial photo of the same place.
That only works for a photo taken from above (a drone, plane or satellite) looking down on buildings, roads or land: straight down, or close to it.
Answer with one JSON object, nothing else:
{"usable": true or false,
 "view": "straight down" | "angled from above" | "from the ground" | "not a photo of a place",
 "shows": "what the photo shows, in at most 12 everyday words",
 "reason": "if not usable, one short sentence a member of the public understands"}
Mark it not usable only when you are sure: a photo from the ground or a street, from an angle so low that buildings are seen from the side, a map or screenshot, or no place at all. Cloud or smoke over part of it is still usable."""


def look(jpeg: bytes) -> dict | None:
    """Muse Wonder's read of a photo, or None when it can't be had (the
    check is a convenience: the search runs either way)."""
    key = os.environ.get("META_API_KEY")
    if not key:
        return None
    from PIL import Image

    im = Image.open(io.BytesIO(jpeg)).convert("RGB")
    im.thumbnail((1024, 1024))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    msg = [{"role": "user", "content": [{"type": "text", "text": PROMPT}, {"type": "image_url", "image_url": {"url": url}}]}]
    try:
        reply = _chat(SPARK_URL, key, MODEL, msg, max_tokens=4000)
        m = re.search(r"\{.*\}", reply, re.S)
        got = json.loads(m.group(0)) if m else None
    except Exception:  # noqa: BLE001 - any failure: no check, not a failed upload
        return None
    if not isinstance(got, dict) or not isinstance(got.get("usable"), bool):
        return None
    return {
        "usable": got["usable"],
        "view": str(got.get("view", ""))[:40],
        "shows": str(got.get("shows", ""))[:120],
        "reason": str(got.get("reason", ""))[:240],
        "model": MODEL,
    }
