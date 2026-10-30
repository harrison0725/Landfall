"""Where people in an area can get help, from a web search.

The damage drafts say what happened to each building; they can't say where
to apply for aid or which recovery center is open. Muse Spark answers that
with its web search tool (Meta Model API, Responses), and every answer comes
with the pages it read, so the user can check them. Official sources first:
FEMA, the state, the county or city.
"""

from __future__ import annotations

import os
from datetime import date
from urllib.parse import urlparse

from . import http
from .agent import REASONING_EFFORT, SPARK_MODEL

URL = "https://api.meta.ai/v1/responses"

PROMPT = """Someone in {place} is asking about getting help after {disaster}. Search the web and answer in at most four short sentences, in everyday words.
Use official sources first: FEMA (fema.gov, disasterassistance.gov), the state, the county or the city; then the Red Cross or well-known charities. Give phone numbers and addresses exactly as the source does. If a recovery center, shelter or deadline may have closed or passed since the source was written, say so and give the date the source gives.
Never give medical or legal advice. If you can't find anything for this place, say so and give the general FEMA routes (disasterassistance.gov, the FEMA app, 1-800-621-3362).

Question: {question}"""


def find_help(place: str, disaster: str, question: str) -> dict:
    """The answer and the pages it came from: {answer, sources: [{title, url}], searched_on}."""
    key = os.environ.get("META_API_KEY")
    if not key:
        return {"error": "web search isn't available on this server"}
    body = {
        "model": SPARK_MODEL,
        "tools": [{"type": "web_search"}],
        "input": PROMPT.format(place=place or "this area", disaster=disaster or "the disaster", question=question[:400]),
    }
    if REASONING_EFFORT != "default":
        body["reasoning"] = {"effort": REASONING_EFFORT}
    try:
        r = http.post(URL, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=(5, 120))
        r.raise_for_status()
        out = r.json().get("output", [])
    except Exception as e:  # noqa: BLE001 - tell the agent, which tells the user
        return {"error": f"the web search didn't answer ({type(e).__name__})"}
    # the model says what it's about to search for as it goes; the answer is its last message
    said = [o for o in out if o.get("type") == "message"]
    texts, sources, seen = [], [], set()
    for o in said[-1:]:
        for c in o.get("content", []):
            if c.get("type") != "output_text":
                continue
            texts.append(c.get("text", ""))
            for a in c.get("annotations", []):
                url = str(a.get("url", ""))
                if urlparse(url).scheme == "https" and url.lower() not in seen:
                    seen.add(url.lower())
                    sources.append({"title": str(a.get("title") or urlparse(url).netloc)[:120], "url": url})
    answer = "\n".join(t.strip() for t in texts if t.strip())
    if not answer:
        return {"error": "the web search found nothing to say"}
    return {"answer": answer, "sources": sources[:6], "searched_on": date.today().isoformat()}
