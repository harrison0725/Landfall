"""Debris piles around each building and blue tarps on its roof, from SAM.

    META_API_KEY=... python scripts/debris_area.py --area cedar_key

Writes data/<area>/debris.json. The work (and why SAM needs the whole yard
to see debris) is in landfall/pipeline.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from landfall.pipeline import cli  # noqa: E402

if __name__ == "__main__":
    cli("debris", __doc__, workers=dict(type=int, default=24))
