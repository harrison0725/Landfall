"""When homes were built, by state, for buildings whose year built is unknown (landfall/hazards.py).

US Census Bureau, American Community Survey 2019-2023 5-year estimates, table B25034 (Year
Structure Built, all housing units), for the US and each state, read from data.census.gov.
Writes landfall/history/housing_age.json: {"US": {"1930": share, ...}, "FL": {...}, ...},
keyed by the first year of each decade (1930 = "1939 or earlier", 2020 = "2020 or later").

Run: python scripts/build_housing_age.py
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://data.census.gov/api/access/data/table?id=ACSDT5Y2023.B25034&g={geo}"
BINS = {"002": 2020, "003": 2010, "004": 2000, "005": 1990, "006": 1980, "007": 1970, "008": 1960, "009": 1950, "010": 1940, "011": 1930}
FIPS = {"01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE", "11": "DC", "12": "FL",
        "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME",
        "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
        "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
        "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI",
        "56": "WY", "72": "PR"}


def shares(geo: str) -> dict:
    req = urllib.request.Request(API.format(geo=geo), headers={"User-Agent": "landfall-research/1.0"})
    d = json.load(urllib.request.urlopen(req, timeout=60))["response"]["data"]
    row = dict(zip(d[0], d[1]))
    counts = {str(y): float(row[f"B25034_{k}E"]) for k, y in BINS.items()}
    total = sum(counts.values())
    return {y: round(c / total, 4) for y, c in sorted(counts.items())}


def main() -> None:
    out = {"source": "US Census Bureau, American Community Survey 2019-2023 5-year estimates, table B25034 (year structure built)",
           "US": shares("010XX00US")}
    for f, st in FIPS.items():
        out[st] = shares(f"0400000US{f}")
    (ROOT / "landfall" / "history" / "housing_age.json").write_text(json.dumps(out, indent=0))
    print(out["US"], out["FL"])


if __name__ == "__main__":
    main()
