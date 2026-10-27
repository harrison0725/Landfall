"""Draft a FEMA category for every building with the agent loop.

    python scripts/assess_area.py --area cedar_key [--only b023 b094] [--limit 20]

Buildings are assessed worst-measured first, so a partial run still covers
the houses that matter most. Writes data/<area>/assessments.json; a partial
run replaces just those buildings. The work is in landfall/pipeline.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from landfall.pipeline import cli  # noqa: E402

if __name__ == "__main__":
    cli(
        "assess",
        __doc__,
        only=dict(nargs="*", default=None),
        limit=dict(type=int, default=0),
        workers=dict(type=int, default=12),
        no_escalate=dict(action="store_false", dest="escalate", default=True),
    )
