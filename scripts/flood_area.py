"""Flood depth above ground for every building in an area.

    python scripts/flood_area.py --area cedar_key

Writes data/<area>/flood.json with the water surface used (and every
observation behind it) and each building's ground elevation and depth.
The work is in landfall/pipeline.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from landfall.pipeline import cli  # noqa: E402

if __name__ == "__main__":
    cli("flood", __doc__)
