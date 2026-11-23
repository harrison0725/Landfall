# Checks of the square estimate's assumptions

Made by `scripts/check_assumptions.py`.

## 1. Forward speed over land (inland wind decay)

Gust at that distance and chance of Hazus damage state 2+ for a one-story wood house, built 1960 / built 2010 (ASCE 7 design wind 130 mph). Landfall assumes 12 mph.

| Category | Miles inland | 8 mph | 12 mph | 18 mph | 24 mph |
|---|---|---|---|---|---|
| Category 1 | 0 | 94 mph: 0% / 0% | 94 mph: 0% / 0% | 94 mph: 0% / 0% | 94 mph: 0% / 0% |
| Category 1 | 10 | 87 mph: 0% / 0% | 89 mph: 0% / 0% | 91 mph: 0% / 0% | 91 mph: 0% / 0% |
| Category 1 | 25 | 79 mph: 0% / 0% | 84 mph: 0% / 0% | 87 mph: 0% / 0% | 88 mph: 0% / 0% |
| Category 1 | 50 | 68 mph: 0% / 0% | 75 mph: 0% / 0% | 81 mph: 0% / 0% | 84 mph: 0% / 0% |
| Category 1 | 100 | 55 mph: 0% / 0% | 63 mph: 0% / 0% | 71 mph: 0% / 0% | 75 mph: 0% / 0% |
| Category 2 | 0 | 113 mph: 9% / 3% | 113 mph: 9% / 3% | 113 mph: 9% / 3% | 113 mph: 9% / 3% |
| Category 2 | 10 | 105 mph: 3% / 1% | 108 mph: 4% / 1% | 109 mph: 5% / 1% | 110 mph: 5% / 1% |
| Category 2 | 25 | 94 mph: 0% / 0% | 100 mph: 1% / 0% | 104 mph: 3% / 1% | 106 mph: 4% / 1% |
| Category 2 | 50 | 79 mph: 0% / 0% | 89 mph: 0% / 0% | 96 mph: 1% / 0% | 100 mph: 1% / 0% |
| Category 2 | 100 | 61 mph: 0% / 0% | 72 mph: 0% / 0% | 82 mph: 0% / 0% | 89 mph: 0% / 0% |
| Category 3 | 0 | 131 mph: 45% / 28% | 131 mph: 45% / 28% | 131 mph: 45% / 28% | 131 mph: 45% / 28% |
| Category 3 | 10 | 121 mph: 21% / 9% | 124 mph: 28% / 15% | 127 mph: 35% / 20% | 128 mph: 37% / 22% |
| Category 3 | 25 | 107 mph: 4% / 1% | 115 mph: 12% / 4% | 120 mph: 18% / 7% | 123 mph: 25% / 13% |
| Category 3 | 50 | 89 mph: 0% / 0% | 101 mph: 1% / 0% | 110 mph: 5% / 1% | 115 mph: 12% / 4% |
| Category 3 | 100 | 66 mph: 0% / 0% | 80 mph: 0% / 0% | 93 mph: 0% / 0% | 101 mph: 1% / 0% |
| Category 4 | 0 | 157 mph: 94% / 87% | 157 mph: 94% / 87% | 157 mph: 94% / 87% | 157 mph: 94% / 87% |
| Category 4 | 10 | 143 mph: 74% / 59% | 148 mph: 83% / 70% | 150 mph: 87% / 75% | 152 mph: 89% / 78% |
| Category 4 | 25 | 126 mph: 33% / 18% | 135 mph: 55% / 39% | 142 mph: 72% / 57% | 145 mph: 78% / 64% |
| Category 4 | 50 | 103 mph: 2% / 1% | 118 mph: 16% / 6% | 129 mph: 40% / 24% | 135 mph: 55% / 39% |
| Category 4 | 100 | 74 mph: 0% / 0% | 91 mph: 0% / 0% | 108 mph: 4% / 1% | 118 mph: 16% / 6% |
| Category 5 | 0 | 181 mph: 100% / 99% | 181 mph: 100% / 99% | 181 mph: 100% / 99% | 181 mph: 100% / 99% |
| Category 5 | 10 | 165 mph: 98% / 94% | 170 mph: 99% / 97% | 174 mph: 99% / 98% | 175 mph: 99% / 98% |
| Category 5 | 25 | 144 mph: 76% / 61% | 155 mph: 92% / 83% | 163 mph: 97% / 93% | 167 mph: 98% / 95% |
| Category 5 | 50 | 117 mph: 14% / 6% | 134 mph: 53% / 36% | 148 mph: 83% / 70% | 155 mph: 92% / 83% |
| Category 5 | 100 | 81 mph: 0% / 0% | 102 mph: 2% / 0% | 122 mph: 23% / 11% | 134 mph: 53% / 36% |

Largest change for the 1960 house between 8 and 24 mph: 77% (cat5, 50 miles inland).

## 2. Helene at Cedar Key: predicted vs found

| Buildings | Count | Damaged: predicted / found | Badly damaged: predicted / found | Water inside: predicted / found |
|---|---|---|---|---|
| all | 771 | 133 / 139 | 78 / 82 | 318 / 312 |
| zone: A or other | 551 | 63 / 73 | 31 / 36 | 219 / 223 |
| zone: V | 220 | 70 / 66 | 47 / 46 | 99 / 89 |
| foundation: crawlspace | 131 | 17 / 25 | 9 / 18 | 50 / 55 |
| foundation: piers | 149 | 22 / 37 | 12 / 21 | 45 / 39 |
| foundation: piles | 185 | 29 / 21 | 17 / 10 | 27 / 21 |
| foundation: slab | 249 | 55 / 48 | 34 / 30 | 164 / 165 |
| foundation: solid wall | 36 | 6 / 5 | 3 / 2 | 22 / 22 |
| era: Florida Building Code (2002 or later) | 85 | 12 / 13 | 7 / 7 | 23 / 8 |
| era: after flood-elevation rules, before the Florida Building Code (1975-2001) | 285 | 47 / 34 | 27 / 14 | 84 / 67 |
| era: before flood-elevation rules (pre-1975) | 312 | 49 / 48 | 27 / 24 | 160 / 179 |
| era: year built unknown (dropped from the tax roll) | 89 | 25 / 44 | 16 / 37 | 50 / 58 |

Brier score (lower is better; 0 is perfect): 0.136, against 0.148 for giving every building the same average chance. Lower than that means the building facts help tell buildings apart.

Helene's water at the surveyed buildings against the National Hurricane Center's worst case for each category (median, feet; positive means the map is deeper than Helene was):

| Category | Worst-case map minus Helene |
|---|---|
| Category 1 | +0.0 ft |
| Category 2 | +3.0 ft |
| Category 3 | +7.0 ft |
| Category 4 | +13.2 ft |
| Category 5 | +13.9 ft |
