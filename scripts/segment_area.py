"""SAM 3.1 structure masks on every building's pre and post crop, and the
share of the pre-storm structure missing afterwards.

    META_API_KEY=... python scripts/segment_area.py --area cedar_key

Raw SAM lines are cached under data/<area>/sam/ so reruns are free. Writes
data/<area>/damage.json. The work is in landfall/pipeline.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from landfall.pipeline import cli  # noqa: E402

if __name__ == "__main__":
    cli("segment", __doc__, workers=dict(type=int, default=24))
