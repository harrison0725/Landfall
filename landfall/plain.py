"""Each building's note in everyday words, for the public.

The per-building agent writes its evidence for assessors ("per PDA flood rule
at least affected"). Asking it to write plainly changed some of its damage
calls, so the calls stay as they are and a separate, text-only pass rewrites
the notes afterwards. The rewrite never sees the photos and can't change a
category: it only says the same thing in plainer words. Kept in plain.json
beside the area; the card shows it, and the original stays in the record.
"""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .agent import SPARK_MODEL, SPARK_URL, _chat

WORDS = {"none": "no damage seen", "affected": "slightly damaged", "minor": "damaged", "major": "badly damaged", "destroyed": "destroyed"}

PROMPT = """Rewrite each building note below for a member of the public with no training in disasters or insurance.
Keep every fact and the same conclusion; add nothing. One or two short sentences, everyday words.
Say the damage level in these words: none = "no damage seen", affected = "slightly damaged", minor = "damaged", major = "badly damaged", destroyed = "destroyed".
No jargon or abbreviations (PDA, FEMA rule, structure loss, outline, verifier, mask): say what they mean, e.g. "the yellow outline" is "where the building stood", "per the PDA flood rule" is "because flood water stood around it".
Answer with one JSON object mapping each id to its rewritten note, nothing else.

{notes}"""


def rewrite(notes: dict[str, tuple[str, str]], batch: int = 25) -> dict[str, str]:
    """{id: (category, note)} -> {id: plain note}. Notes it can't rewrite are left out."""
    key = os.environ["META_API_KEY"]
    ids = list(notes)

    def one(chunk: list[str]) -> dict[str, str]:
        text = "\n".join(f'{i} ({WORDS.get(notes[i][0], notes[i][0])}): {notes[i][1]}' for i in chunk)
        try:
            # the reply is held to one string per id, so a batch can't come back short
            schema = {"type": "object", "properties": {i: {"type": "string"} for i in chunk}, "required": chunk, "additionalProperties": False}
            reply = _chat(SPARK_URL, key, SPARK_MODEL, [{"role": "user", "content": PROMPT.format(notes=text)}], max_tokens=6000, schema=schema)
            m = re.search(r"\{.*\}", reply, re.S)
            got = json.loads(m.group(0)) if m else {}
        except Exception:  # a note in plain words is nice to have; the card falls back to the original
            return {}
        return {i: str(got[i]).strip() for i in chunk if isinstance(got.get(i), str) and got[i].strip()}

    out: dict[str, str] = {}
    with ThreadPoolExecutor(4) as pool:
        for part in pool.map(one, [ids[k : k + batch] for k in range(0, len(ids), batch)]):
            out.update(part)
    return out


def write(d: Path) -> int:
    """Rewrite an area's notes into plain.json; returns how many were rewritten."""
    drafts = json.loads((d / "assessments.json").read_text())
    notes = {a["id"]: (a.get("category", ""), a.get("evidence", "")) for a in drafts if a.get("evidence")}
    plain = rewrite(notes)
    if len(plain) < len(notes):  # a batch the model fumbled: once more for those
        plain.update(rewrite({i: n for i, n in notes.items() if i not in plain}))
    if plain:
        (d / "plain.json").write_text(json.dumps(plain, indent=1))
    return len(plain)
