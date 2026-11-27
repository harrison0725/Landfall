# Checks behind tornadoes in "Over the years"

November 2026. `landfall/tornadoes.py` gives each US square its tornado chances over the next 10, 30 and 50 years, from NOAA Storm Prediction Center's record of every tornado path since 1950 (`scripts/build_tornadoes.py`, 73,458 tornadoes).

## How it works

1. **How often tornadoes come.** It counts every tornado since 1995 whose path came within 50 miles of the square. From 1995 on, Doppler radar finds the weak tornadoes, and path widths are the largest width.
2. **Where they land.** Each simulated tornado is one of those, with its own rating, length, width and direction, dropped at a random spot within the 50 miles. Most miss the square.
3. **Wind across the path.** The wind is strongest along the center line, at a speed within the rating's Enhanced Fujita range. It falls off to 65 mph (the bottom of EF0) at the path's edge, as in a Rankine vortex.
4. **Damage.** Each building in the path is damaged according to the Enhanced Fujita scale's damage indicators for houses and manufactured homes. A building counts once.

The likely-between range is the middle 90% of 4,000 simulated periods. For most squares, the most likely number damaged is zero, while a direct hit can damage hundreds. The card says that plainly instead of giving only an average.

## Back-test: the chance of a path crossing a square

Tornadoes from 1995-2010 were used to predict the chance that a path crosses a 1-square-mile area in the next 15 years. The test used 2,096 points every half degree east of the Rockies (26-47N, 104-70W). The prediction was then compared with whether a real 2011-2025 path did cross (`scripts/tornado_check.py`, `eval/tornado_check.json`).

| Predicted chance | Squares | Average predicted | Share actually crossed |
|---|---|---|---|
| 0-0.5% | 203 | 0.2% | 0.5% |
| 0.5-1% | 144 | 0.7% | 0.7% |
| 1-2% | 255 | 1.5% | 2.8% |
| 2-4% | 457 | 3.0% | 3.9% |
| 4-8% | 614 | 5.8% | 6.0% |
| 8% and up | 423 | 11.7% | 9.9% |
| **All** | **2,096** | **5.0%** | **5.1%** |

The chance of a hit holds up out of sample. Lower-risk places run slightly low, and the riskiest run slightly high.

## Example squares (next 30 years)

| Square | Tornadoes a year within 50 miles | Chance a path crosses the square | Rated EF2 or stronger |
|---|---|---|---|
| Moore, Oklahoma | 13 | 30% | 18% |
| Gainesville, Florida | 4 | 7% | 1% |
| Cedar Key, Florida | 1 | 2% | under 1% |

## Known limits

- **Damage hasn't been checked against a real tornado.** Only the chance of a hit was back-tested.
- **Peak wind along the whole path.** The rating's wind is taken to hold along the whole length. Strong tornadoes usually reach their peak over only part of their path, so damage from strong tornadoes likely runs high.
- **Year built isn't used.** The Enhanced Fujita damage indicators don't tell houses apart by age, so tornado damage depends only on house versus manufactured home.
- **Same climate in both views.** Tornadoes stay as in the past in the Climate of 2050 view, because scientists can't yet say how warming changes them in one place.
- **US only.** Squares outside the US get no tornado section.
