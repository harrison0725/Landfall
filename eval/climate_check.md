# Checks behind "Over the years"

November 2026. `landfall/climate.py` gives each square its chances over the next 10, 30 and 50 years. It replays every Atlantic storm since 1950 (NOAA HURDAT2, 984 storms) past the square, then runs the same damage curves as the hurricane scenarios. Numbers come from `scripts/climate_check.py` and are stored in `eval/climate_check.json`.

## Wind at the square

Each storm's wind at a point comes from a simple vortex built from the best track. The vortex has the storm's strongest wind at its radius of maximum wind and weaker winds farther out, and it is faster on the right side. That gives a peak gust.

I compared this with NIST/ARA's measured-wind maps at 720 surveyed houses (120 per storm):

| Storm | Measured gust (median) | Vortex gust | Ratio |
|---|---|---|---|
| Harvey 2017 | 133 mph | 148 | 0.85 |
| Irma 2017 | 111 | 138 | 0.82 |
| Michael 2018 | 132 | 167 | 0.82 |
| Laura 2020 | 133 | 143 | 0.93 |
| Ian 2022 | 101 | 177 | 0.57 |
| Helene 2024 | 85 | 112 | 0.76 |

Over land, the measured gusts were 0.81 of the vortex's, give or take 17%, so Landfall scales the vortex by 0.81. The damage curves were calibrated on these same measured-wind maps (`eval/research`), so wind and damage use the same scale. Ian is the outlier. ARA's map puts Fort Myers Beach at about 101 mph, well below what Ian's Category 4 landfall 20 nautical miles away suggests.

## Storm surge

A storm can push surge onto a square when the square is on its right (onshore winds) within 100 nautical miles, or within its radius of maximum wind on either side. The water level above high tide is then a share of the National Hurricane Center's worst-case Category 1 level at the square. That share grows with the storm's strongest wind nearby.

The share was fit to the Cedar Key tide gauge (NOAA 8727520). It uses the peak water level for each of the 35 storms since 1950 that could push surge there and have gauge data. The fit has a correlation of 0.74, give or take 1.4 ft.

| Storm | Closest | Peak at the gauge (ft above high tide) |
|---|---|---|
| Helene 2024 | 52 nm | 9.3 |
| Idalia 2023 | 49 nm | 6.7 |
| Hermine 2016 | 77 nm | 5.9 |
| Elena 1985 | 45 nm | 5.4 |
| Josephine 1996 | 68 nm | 5.2 |
| Debby 2024 | 38 nm | 4.6 |

The fit gives Helene 6.6 ft, short of the 9.3 ft measured. Helene was unusually large, and storm size isn't in the rule.

**This uses one gauge.** Elsewhere, the rule relies on the worst-case maps to carry the local sea floor's effect. It hasn't been checked at a second gauge.

Each building's depth is the storm's water level minus how far it sits above the worst-case Category 1 water. The worst-case Category 1 level at a square is its deepest buildings' Category 1 depth plus 2.8 ft. The 2.8 ft was fit to Helene's measured water at Cedar Key buildings.

## Replaying Helene at Cedar Key

Helene replayed against today's 688 buildings in the Cedar Key square gives about 90 mph gusts, 6.6 ft of surge and about 83 buildings damaged (12%). The field survey found 139 of 771 damaged (18%). The shortfall comes from the surge rule's low water level for Helene.

## From past storms to future years

For each period:

1. The number of storms that reach the square is drawn at random, from how often they came in the 76 years of record. How often they come is itself uncertain, so the draw allows for that.
2. Each storm is a past storm picked at random, with its wind and water set a bit lower, as estimated, or a bit higher.
3. A building counts once, however many times it is damaged.
4. The spread from the multi-storm damage test (`eval/research`) is added.

The likely-between range is the middle 90% of 4,000 simulated periods.

## Change to the wind calibration

Storms with 40-80 mph gusts are common. The calibrated wind curve gave 5-7% of houses damaged at those speeds, while surveys at 80-90 mph found 0-3% (Cedar Key 9 of 342, Harvey 0 of 42). Over 30 years, that small overestimate added up: Gainesville showed 416 of 1,502 buildings damaged by storms that never brought hurricane-force gusts.

The calibration now fades to raw Hazus between 100 and 70 mph instead of between 70 and 40 mph. Scenario numbers change only below 100 mph gusts.

## Known limits

- **Past climate only.** More strong storms and higher seas are not included, so the numbers likely run low.
- **The wind vortex is simple.** It uses a fixed shape and leaves out the effect of terrain beyond the 0.81 scaling.
- **The surge rule is fit at one gauge** and misses storm size.
- **Atlantic storms only.** Squares on the Pacific side get no hurricane history.
