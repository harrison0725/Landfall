"""Download imagery and outlines for an area and line up every building.

    python scripts/prepare_area.py --area cedar_key

Writes data/<area>/: spec.json, buildings.geojson, pre.png / post.png
mosaics, crops/<id>_{pre,post}.png and alignment.json. The work is in
landfall/pipeline.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from landfall.pipeline import cli  # noqa: E402

if __name__ == "__main__":
    cli("prepare", __doc__)
