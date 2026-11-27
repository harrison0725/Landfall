"""How fast hurricanes move when they come ashore in the US (landfall/hazards.py FORWARD_MPH).

From the NHC best tracks already in landfall/history/hurricane_tracks.json (HURDAT2, 1950 on):
every fix marked as a landfall at hurricane strength (64 kt or more) on the US Gulf or East coast,
forward speed from the fixes either side of it. Prints the 10th, 50th and 90th percentiles.

Run: python scripts/forward_speed.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def us_mainland(lat: float, lon: float) -> bool:
    if not (24 < lat < 46 and -98 < lon < -66):
        return False
    if lat < 25 and lon > -80:  # the Bahamas and Cuba
        return False
    return not (lon < -97.5 and lat < 26)  # Mexico


def main() -> None:
    d = json.loads((ROOT / "landfall" / "history" / "hurricane_tracks.json").read_text())
    speeds = []
    for s in d["storms"]:
        for i in s["landfall"]:
            if s["kt"][i] < 64 or not us_mainland(s["lat"][i], s["lon"][i]):
                continue
            a, b = max(i - 1, 0), min(i + 1, len(s["t"]) - 1)
            hours = s["t"][b] - s["t"][a]
            if hours <= 0:
                continue
            k = math.cos(math.radians(s["lat"][i]))
            speeds.append(math.hypot((s["lon"][b] - s["lon"][a]) * k, s["lat"][b] - s["lat"][a]) * 69.17 / hours)
    p10, p50, p90 = np.percentile(speeds, [10, 50, 90])
    print(f"{len(speeds)} US hurricane landfalls since {d['first_year']}: forward speed 10th {p10:.1f}, median {p50:.1f}, 90th {p90:.1f} mph")


if __name__ == "__main__":
    main()
