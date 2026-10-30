"""Claim pages in the languages families in the storm areas speak.

Many households in Florida's storm areas speak Spanish or Haitian Creole at
home, and a claim page they can't read is no help to them. Muse Spark
translates the page's text (never its numbers, dates, ids, phone numbers or
the names of agencies and programs), and every translated page says it is a
machine translation and links the English original, which governs.

Translations are kept per area in translations.<lang>.json, keyed by the
English text: most sentences repeat from building to building, so after the
first page of an area only the new ones are sent.
"""

from __future__ import annotations

import html
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .agent import SPARK_MODEL, muse_chat

LANGS = {"es": "Spanish", "ht": "Haitian Creole"}
NOTICE = {
    "es": "Traducción automática hecha con Muse Spark (Meta). Si algo no coincide, vale la versión en inglés:",
    "ht": "Tradiksyon otomatik ak Muse Spark (Meta). Si gen yon diferans, se vèsyon angle a ki bon:",
}

PROMPT = """Translate each text below from English into {lang}, for a family filing an insurance or disaster-aid claim after a disaster.
Use plain everyday words a member of the public understands. Keep the meaning exactly; add and drop nothing.
Keep exactly as they are: numbers, money, percentages, units (ft, sq ft, NAVD88), dates, building ids like b023, phone numbers, web addresses,
and the names of agencies, programs, datasets and models (FEMA, NOAA, USDA NAIP, USGS 3DEP, National Flood Insurance Program, DisasterAssistance.gov,
Copernicus EMS, European Commission JRC, Maxar, Microsoft, Muse Spark, Muse Spark Open, Landfall).
A text that is only a name, number or code stays as it is.
Answer with one JSON object mapping each key to its translation, nothing else.

{texts}"""

_lock = threading.Lock()


def translate(texts: list[str], lang: str, batch: int = 40) -> dict[str, str]:
    """English texts -> {english: translation}. Texts it can't translate are left out."""
    if lang not in LANGS or not texts or not (os.environ.get("META_API_KEY") or os.environ.get("BASETEN_API_KEY")):
        return {}

    def one(chunk: list[str]) -> dict[str, str]:
        keys = [f"t{i}" for i in range(len(chunk))]
        body = "\n".join(f"{k}: {json.dumps(t, ensure_ascii=False)}" for k, t in zip(keys, chunk))
        # the reply is held to one string per key, so a batch can't come back short
        schema = {"type": "object", "properties": {k: {"type": "string"} for k in keys}, "required": keys, "additionalProperties": False}
        try:
            reply, _ = muse_chat(SPARK_MODEL, [{"role": "user", "content": PROMPT.format(lang=LANGS[lang], texts=body)}], max_tokens=8000, schema=schema)
            m = re.search(r"\{.*\}", reply, re.S)
            got = json.loads(m.group(0)) if m else {}
        except Exception as e:  # noqa: BLE001 - an untranslated sentence stays in English
            errors.append(f"{type(e).__name__}: {e}"[:300])
            return {}
        return {t: str(got[k]).strip() for k, t in zip(keys, chunk) if isinstance(got.get(k), str) and got[k].strip()}

    out: dict[str, str] = {}
    with ThreadPoolExecutor(4) as pool:
        for part in pool.map(one, [texts[k : k + batch] for k in range(0, len(texts), batch)]):
            out.update(part)
    return out


errors: list[str] = []  # the last few translation failures, for the server log and the page's source


# text worth translating: has a letter, and isn't just an id, a number with units or a code
_WORDY = re.compile(r"[A-Za-z]{2,}")
_KEEP = re.compile(r"^(b\d{3}|[\d.,:%$ ()\-]+(ft|sq ft|NAVD88|UTC)?|https?://\S+|PDF)$")


def page(doc: str, lang: str, d: Path, english_url: str) -> str:
    """The claim page with its text translated, a notice at the top, and lang set."""
    if lang not in LANGS:
        return doc
    # text between tags (the title too), outside <style> and <script>
    parts = re.split(r"(<[^>]+>)", doc)
    skip = False
    spots: list[int] = []
    for i, p in enumerate(parts):
        if p.startswith("<"):
            tag = p[1:].split()[0].rstrip(">").lower() if len(p) > 2 else ""
            skip = tag in ("style", "script") or (skip and tag not in ("/style", "/script"))
            continue
        text = html.unescape(p).strip()
        if not skip and _WORDY.search(text) and not _KEEP.match(text):
            spots.append(i)
    texts = sorted({html.unescape(parts[i]).strip() for i in spots})
    path = d / f"translations.{lang}.json"
    with _lock:
        known = json.loads(path.read_text()) if path.exists() else {}
    errors.clear()
    new = translate([t for t in texts if t not in known], lang)
    for err in errors:
        print(f"translation to {lang} failed: {err}", flush=True)
    if new:
        with _lock:
            known = {**(json.loads(path.read_text()) if path.exists() else {}), **new}
            path.write_text(json.dumps(known, indent=1, ensure_ascii=False))
    translated = any(html.unescape(parts[i]).strip() in known for i in spots)
    for i in spots:
        raw = parts[i]
        text = html.unescape(raw).strip()
        if text in known:
            lead, trail = raw[: len(raw) - len(raw.lstrip())], raw[len(raw.rstrip()) :]
            parts[i] = lead + html.escape(known[text], quote=False) + trail
    notice = (
        '<p style="background:#eef6fb;border:1px solid #b9d7ea;padding:8px 10px;border-radius:6px">'
        f'{html.escape(NOTICE[lang])} <a href="{html.escape(english_url)}">English</a></p>'
    )
    if not translated:
        notice = f'<p style="background:#fbeeee;border:1px solid #eab9b9;padding:8px 10px;border-radius:6px">The translation isn\'t available right now, so this page is in English.</p><!-- {html.escape("; ".join(errors))} -->'
    out = "".join(parts).replace('<html lang="en">', f'<html lang="{lang}">', 1)
    # the notice goes under the page's title block, before the draft warning
    return out.replace('<div class="draft">', notice + '<div class="draft">', 1)
