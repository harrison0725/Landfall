"""Find Mapillary street-level photos of every building, taken after the disaster.

    MAPILLARY_ACCESS_TOKEN=... python scripts/street_area.py --area cedar_key [--days-after 120]

Writes data/<area>/street.json and data/<area>/street/*.jpg. The next
assess run gives the photos to the agent. The work is in landfall/street.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from landfall.pipeline import cli  # noqa: E402

if __name__ == "__main__":
    cli("street", __doc__, days_after=dict(type=int, default=120))
