"""Back-test of landfall/tornadoes.py: does the chance of a tornado path crossing a square hold up?

Rates from tornadoes in 1995-2010 (within 50 miles) predict the chance that a path crosses a
1-square-mile area in the next 15 years, at points every half degree from 26-47N, 104-70W.
That is compared with whether a real 2011-2025 path did. Writes eval/tornado_check.json.

Run: python scripts/tornado_check.py
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from landfall import tornadoes as T  # noqa: E402

R_SQ = 0.564  # radius of a 1-square-mile disc


def main() -> None:
    r = T.record()
    rows = []
    for la in np.arange(26, 47, 0.5):
        for lo in np.arange(-104, -70, 0.5):
            i, _ = T._nearby(lo, la, T.REACH_MI, 1995)
            i = i[r["year"][i] <= 2010]
            L = r["length_mi"][i].astype(float)
            h = r["width_yd"][i].astype(float) / 1760 / 2
            lam = ((L + 2 * R_SQ) * (2 * h + 2 * R_SQ)).sum() / (np.pi * T.REACH_MI ** 2) / 16  # crossings a year
            j, d = T._nearby(lo, la, R_SQ + 1.5, 2011)
            hit = bool(((d - r["width_yd"][j] / 1760 / 2) <= R_SQ).any())
            rows.append((1 - np.exp(-lam * 15), hit))
    a = np.array(rows, float)
    a = a[a[:, 0] > 0]
    bins = [0, 0.005, 0.01, 0.02, 0.04, 0.08, 1]
    out = []
    for b0, b1 in zip(bins, bins[1:]):
        m = (a[:, 0] > b0) & (a[:, 0] <= b1)
        if m.sum():
            out.append({"predicted": f"{b0:.1%}-{b1:.1%}", "squares": int(m.sum()),
                        "mean_predicted": round(float(a[m, 0].mean()), 4), "share_crossed": round(float(a[m, 1].mean()), 4)})
    res = {"bins": out, "squares": int(len(a)), "mean_predicted": round(float(a[:, 0].mean()), 4),
           "share_crossed": round(float(a[:, 1].mean()), 4)}
    (Path(__file__).resolve().parents[1] / "eval" / "tornado_check.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
