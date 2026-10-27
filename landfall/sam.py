"""SAM 3.1 on Meta Model API: concept masks for a building crop.

One request per concept ("roof", "blue tarp", "debris", ...). The model
returns every match as a box and a mask in a special-token text line, which
`meta_sam_parser` decodes. Masks come back cropped to their box; we paste
them into a full-size raster at the crop's own resolution.

Crops are upsampled before sending: a house is only ~50 px across at zoom 19,
and SAM finds roof edges more reliably at a few hundred pixels.
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import random
import time
from dataclasses import dataclass

import numpy as np

from . import http

API_URL = "https://api.meta.ai/v1/responses"
MODEL = "sam-3.1"
MISS_RETRIES = 12
TIMEOUT_S = 15


@dataclass(frozen=True)
class Detection:
    object_id: int
    box: tuple[int, int, int, int]  # x1, y1, x2, y2 inclusive, crop pixels
    confidence: float | None
    mask: np.ndarray  # bool, crop-sized


def _png_data_url(img: np.ndarray, scale: int) -> str:
    from PIL import Image

    im = Image.fromarray(np.asarray(img, dtype=np.uint8))
    if scale != 1:
        im = im.resize((im.width * scale, im.height * scale), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG", compress_level=1)  # lossless either way; level 6 costs ~10x the CPU
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def request(img: np.ndarray, prompt: str, scale: int = 4, api_key: str | None = None, retries: int = 4) -> str:
    """Send one concept prompt; return the raw output_text line."""
    import requests

    key = api_key or os.environ["META_API_KEY"]
    body = {
        "model": MODEL,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": _png_data_url(img, scale)},
                ],
            }
        ],
        "metadata": {"include_confidence": "true", "mask_encoding": "one_bit"},
    }
    attempt = misses = 0
    while True:
        try:
            # A normal answer takes 1-3 s. Now and then a request hangs, and
            # one hung image used to hold up the whole area for minutes, so
            # give up on it quickly and ask again.
            r = http.post(API_URL, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=(5, TIMEOUT_S))
        except (requests.Timeout, requests.ConnectionError):
            attempt += 1
            if attempt < retries:
                continue
            raise
        # The API sometimes routes a request to a server without SAM and answers
        # 404 model_not_found; asking again a moment later usually works.
        if r.status_code == 404 and "model_not_found" in r.text and misses < MISS_RETRIES:
            misses += 1
            time.sleep(0.3 + 0.5 * random.random())
            continue
        if r.status_code == 429 or r.status_code >= 500:
            attempt += 1
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
        r.raise_for_status()
        out = r.json().get("output", [])
        return "".join(c.get("text", "") for o in out for c in o.get("content", []) if c.get("type") == "output_text")


_NATIVE = None


def _decoder():
    """The mask decoder: the C port in landfall/native when it is built,
    else the parser package's own pure-Python one (about 1,000x slower)."""
    global _NATIVE
    if _NATIVE is None:
        import ctypes
        from pathlib import Path

        so = Path(__file__).parent / "native" / "maskdec.so"
        try:
            lib = ctypes.CDLL(str(so))
            lib.decode_raster.restype = ctypes.c_int
            _NATIVE = lib
        except OSError:
            _NATIVE = False
    return _NATIVE


def decode_mask(mask) -> bytes:
    """One mask to a 0/1 byte raster. The parser's decode_mask_to_raster
    also re-encodes every mask to check it is canonical, which doubles its
    already slow decode; we trust the API's output and decode once."""
    from meta_sam_parser import _mask_codec as mc

    lib = _decoder()
    if not lib:
        return mc._decode_raster(mask.payload, mask.width, mask.height)
    import ctypes

    data = mc._unpack(mask.payload[1:])
    out = ctypes.create_string_buffer(mask.width * mask.height)
    if lib.decode_raster(data, ctypes.c_long(len(data)), mask.width, mask.height, out) != 0:
        raise ValueError("malformed SAM mask payload")
    return out.raw


def _fast_parser() -> None:
    """Point the parser's own validation decode at ours, once."""
    from meta_sam_parser import _segmentation

    if getattr(_segmentation, "_landfall_fast", False):
        return
    _segmentation.decode_mask_to_raster = decode_mask
    _segmentation._landfall_fast = True


def parse(text: str, shape: tuple[int, int], scale: int = 4) -> list[Detection]:
    """Decode one image line into detections at crop resolution."""
    if not text.strip():
        return []
    import meta_sam_parser as msp

    _fast_parser()

    async def events():
        lane = {"item_id": "msg_0", "output_index": 0, "content_index": 0}
        yield {"type": "response.output_text.delta", "delta": text, **lane}
        yield {"type": "response.output_text.done", "text": text, **lane}
        yield {"type": "response.completed", "response": {"status": "completed"}}

    async def run():
        parsed = msp.parse_responses_stream(events(), msp.image_segmentation_format())
        return await parsed.final_result()

    result = asyncio.run(run())
    boxes = {r.object_id: r for r in result.records if r.kind == "box"}
    h, w = shape
    dets = []
    for rec in result.records:
        if rec.kind != "mask":
            continue
        raster = decode_mask(rec.mask)
        mh, mw = rec.mask.height, rec.mask.width
        small = np.frombuffer(raster, dtype=np.uint8).reshape(mh, mw).astype(bool)
        b = boxes[rec.object_id]
        full = np.zeros((h * scale, w * scale), bool)
        y0, x0 = int(b.top), int(b.left)
        hh, ww = min(mh, full.shape[0] - y0), min(mw, full.shape[1] - x0)
        full[y0 : y0 + hh, x0 : x0 + ww] = small[:hh, :ww]
        # back to crop resolution: a crop pixel is set when most of its block is
        mask = full.reshape(h, scale, w, scale).mean(axis=(1, 3)) >= 0.5
        dets.append(
            Detection(
                object_id=rec.object_id,
                box=(int(b.left) // scale, int(b.top) // scale, int(b.right) // scale, int(b.bottom) // scale),
                confidence=rec.confidence,
                mask=mask,
            )
        )
    return dets


def segment(img: np.ndarray, prompt: str, scale: int = 4) -> list[Detection]:
    return parse(request(img, prompt, scale), img.shape[:2], scale)
