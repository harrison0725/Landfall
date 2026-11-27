# Checks behind tornadoes in "Over the years"

November 2026. `landfall/tornadoes.py` gives each US square its tornado chances over the next 10, 30 and 50 years, from NOAA Storm Prediction Center's record of every tornado path since 1950 (`scripts/build_tornadoes.py`, 73,458 tornadoes).

## How it works

1. **How often tornadoes come.** It counts every tornado since 1995 whose path came within 50 miles of the square. From 1995 on, Doppler radar finds the weak tornadoes, and path widths are the largest width.
2. **Where they land.** Each simulated tornado is one of those, with its own rating, length, width and direction, dropped at a random spot within the 50 miles. Most miss the square.
3. **Wind in the path.** A tornado's top wind covers only a small part of its recorded path. National Weather Service damage surveys map EF contours (the Damage Assessment Toolkit, 2008 on). Matching 876 of those maps to the SPC record gives, for each rating, the share of the recorded length times largest width at or above each EF level (`scripts/build_tornado_wind.py`, `landfall/history/tornado_wind.json`). A building's level depends on how far it is from the center line, and its wind falls anywhere in that level's range.
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

## How much of the path gets each wind

Share of the recorded length × largest width at or above each level:

| Rating | Tornadoes mapped | EF0+ | EF1+ | EF2+ | EF3+ | EF4+ |
|---|---|---|---|---|---|---|
| EF1 | 595 | 100% | 35% | | | |
| EF2 | 207 | 84% | 38% | 6% | | |
| EF3 | 56 | 79% | 41% | 14% | 3% | |
| EF4-EF5 | 18 | 74% | 51% | 19% | 6% | 0.6% |

The first version of the model assumed the rated wind ran the whole length of the path. That put EF3 or stronger wind over about half of an EF3's path, when surveys show 3%. Almost no mapped area reached EF5, even in the 2013 Moore EF5, so EF5 wind is effectively never applied.

## Replaying real tornadoes

`scripts/tornado_replay.py` (`eval/tornado_replay.json`) takes the 13 biggest mapped tornadoes that hit homes, EF3 to EF5, including Moore 2013. For each one it counts the damage three ways, all with the same damage curves:

- **From the survey map:** homes (USACE National Structure Inventory) inside the surveyed outline, each at its mapped EF level. This is the best wind information there is.
- **New model:** homes inside the SPC length × width box, with wind from the surveyed shares above.
- **First model:** the same box with the rated wind along the whole path.

| 13 tornadoes together | Homes damaged | Badly damaged |
|---|---|---|
| From the survey map | 10,239 | 3,119 |
| New model | 9,199 (0.90×) | 3,152 (1.01×) |
| First model | 19,756 (1.9×) | 15,617 (5.0×) |

The first model put about five times too many homes in bad damage. Single tornadoes still miss in both directions, because the SPC box can be far bigger or smaller than the surveyed outline. For example, Nashville 2020's box holds 11,442 homes against 6,046 inside its surveyed outline.

**Moore 2013:**
- The survey map gives 1,854 homes damaged and 1,127 badly damaged.
- The new model gives 2,494 and 923.
- The first model gave 4,683 and 3,698.
- The NWS surveyed 1,232 houses: 1,063 had at least roof-deck damage and 817 had large parts of the roof structure gone or worse. That is a floor, since not every house was surveyed.

The damage curves matched the surveyed houses at each mapped level: 43% of surveyed houses at EF1 had roof-deck damage (the curve says 52%), and 95% at EF2 did (curve 97%). This partly checks itself, because surveyors draw the contours from those same houses.

## Example squares (next 30 years)

| Square | Tornadoes a year within 50 miles | Chance a path crosses the square | With EF2 or stronger wind at a building |
|---|---|---|---|
| Moore, Oklahoma | 13 | 31% | 15% |
| Gainesville, Florida | 4 | 7% | under 1% |
| Cedar Key, Florida | 1 | 2% | under 1% |

## Known limits

- **Damage counts are checked against survey maps, not against counts of homes actually damaged.** Only Moore has a survey floor.
- **Strong levels sit in strips along the center line.** In reality they come in patches. Totals hold, but which buildings get hit together is simplified.
- **Only 18 EF4 and EF5 maps.** Their shares are the least certain.
- **Year built isn't used.** The Enhanced Fujita damage indicators don't tell houses apart by age, so tornado damage depends only on house versus manufactured home.
- **Same climate in both views.** Tornadoes stay as in the past in the Climate of 2050 view, because scientists can't yet say how warming changes them in one place.
- **US only.** Squares outside the US get no tornado section.
