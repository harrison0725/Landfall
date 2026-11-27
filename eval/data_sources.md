# Where Landfall's numbers come from

A check of every dataset and every fixed number Landfall uses to predict damage (November 27, 2026).
Each one is either **sourced** (it comes from a published dataset, standard or study), **measured**
(Landfall fit it to field surveys), or a **guess**, which comes with the scientific reason for it.
Guesses that didn't hold up were replaced. They are listed at the end under "Fixed in this check".

## 1. Buildings

| What | Where it comes from | Kind |
|---|---|---|
| Where buildings are, walls, foundation, stories, value | USACE National Structure Inventory (NSI). NSI itself fills in walls and foundation from regional patterns where county records lack them | sourced |
| Year built, living area, value (Florida) | Florida Department of Revenue statewide tax roll | sourced |
| Year built (LA fires) | CAL FIRE damage inspections (DINS), from the county assessor | sourced |
| Walls and stories seen from the street | Mapillary street photos, read by the AI | measured (78% agreement with the Helene survey) |
| Outlines | Microsoft building footprints | sourced |
| Flood zone, base flood elevation | FEMA National Flood Hazard Layer | sourced |
| When homes were built, by state (for buildings with no year built) | US Census ACS 2019-2023, table B25034 (`scripts/build_housing_age.py`) | sourced (new) |
| Unknown walls taken as wood frame | Census Survey of Construction: about 93-95% of new US single-family homes are wood. Rarely used, because NSI almost always has walls. In Florida, where concrete block is common, this guess would be wrong, so it only covers the few buildings NSI misses | guess, reasoned |
| Floors from building height: 3.2 m per story | Typical floor-to-floor height (2.7-3.0 m ceiling plus the floor), as used in global building-height datasets | guess, reasoned |
| First floor 1-3 ft above ground (old or X-zone houses) | Hazus default first-floor heights for slab and crawlspace | sourced |
| Built 1985 on: floor at the base flood elevation; before 1975: not | NFIP rules: buildings from before December 31, 1974 (or before the town's first flood map) are "pre-FIRM". Most towns got their first map between 1974 and the mid-1980s, so 1975-1984 is left unknown | sourced rule, reasoned cutoff |

## 2. How strong the disaster is

| What | Where it comes from | Kind |
|---|---|---|
| Hurricane categories (middle of each Saffir-Simpson band) | NHC Saffir-Simpson scale | sourced |
| 3-second gust from 1-minute wind, x1.22 | ASCE 7 Durst curve ratio (1.52 / 1.24) | sourced |
| Wind weakening inland | Kaplan and DeMaria (1995) decay model (26.7 kt floor, 0.9 reduction, 0.095 per hour) | sourced |
| How fast storms move inland: 13.6 mph, range 6.3-28.4 mph | Median and 10th/90th percentile of 174 US hurricane landfalls since 1950 in HURDAT2 (`scripts/forward_speed.py`) | measured (new) |
| Storm surge depth | NHC Storm Surge Risk Maps (SLOSH maximum of maximums) | sourced |
| Design wind speed | ASCE 7-16 hazard tool at the location | sourced |
| Design wind speed when ASCE's tool is down and the place is over 150 miles inland | 115 mph, the ASCE 7-10 interior speed for homes | sourced (new) |
| Tornado categories, wind ranges | Enhanced Fujita scale (SPC) | sourced |
| Tornado history | SPC tornado database, 1995 on | sourced |
| How much of a tornado's path gets each EF level | NWS damage survey maps of 876 tornadoes | measured |
| Earthquake shaking (MMI to PGA) | Worden et al. (2012), as USGS ShakeMap uses | sourced |
| Seismic design level | ASCE 7-16 design maps at the location (USGS), grouped as FEMA P-749 section 4 does; state lists only if USGS is down | sourced (new) |
| Past hurricanes | NHC HURDAT2, 1950 on | sourced |
| Wind over land, x0.81 | NIST/ARA measured gusts in six storms vs the vortex model | measured |
| 2050 sea level | 2022 Interagency Sea Level Rise Report, Intermediate, at the nearest tide gauge | sourced |
| 2050 winds +3% | Knutson et al. (2020), scaled to 2041-2060 as IPCC AR6 does | sourced |
| Sea temperature | NOAA ERSST v5; 2050 from 41 CMIP6 models (SSP2-4.5) | sourced |

## 3. Damage

| What | Where it comes from | Kind |
|---|---|---|
| Wind damage | Hazus Hurricane v5.1 curves (NHERI SimCenter library), building details from the SimCenter rulesets | sourced |
| Wind damage adjusted to what surveys found | Fit to field surveys of 7 places, 7,431 buildings | measured |
| Calibration fades out below 100 mph, gone at 70 | The surveys' lowest wind band (80-90 mph) found 0-3% of roofs damaged, below the fitted curve | measured |
| Roof shape: gable or hip by year built (69% gable before 1976, 56% 1976-2001, 47% 2002 on) | 2,551 houses whose roof shape field surveys recorded (WiSPD, PRJ-4186; `scripts/roof_shapes.py`). Hip roofs had 0.66 times the odds of roof damage at the same wind (0.54-0.81), as Hazus expects | measured (new) |
| Water damage | Helene survey (DesignSafe PRJ-6245) and two more surveyed towns, by water above the floor | measured |
| Water damage for river floods | Measured on the coast (A zones, where waves were small). River floods are slower and carry no salt, so this probably runs a little high for them | measured elsewhere, noted |
| Repair cost from water | FEMA FIA depth-damage curves | sourced |
| Wind + water combined | Hazus combination table | sourced |
| Tornado damage | Enhanced Fujita damage indicators 2 and 3 (homes and manufactured homes) | sourced |
| Earthquake damage | Hazus Earthquake v6.1 curves | sourced |
| Wildfire, Helene-like surge | Share of look-alike buildings damaged in the LA 2025 fires and Cedar Key | measured |
| Flooding alone is not damage | Harry's decision (Nov 18) | project rule |
| Major = water over 18 inches in a living space | FEMA Preliminary Damage Assessment Guide (2021) | sourced (citation checked) |

## 4. Repair cost

| What | Where it comes from | Kind |
|---|---|---|
| Building value (US) | NSI structure value, or the Florida tax roll | sourced |
| Building value (outside the US) | JRC global damage database (Huizinga et al. 2017), national averages in 2010 prices, not adjusted for inflation (the output says so) | sourced, dated |
| Cost by water above the floor | FEMA P-784 (Substantial Damage Estimator) bands | sourced |
| Cost by damage category | Hazus repair cost per damage state for homes: 2%, 10%, 44.7%, 100% | sourced (new) |

## Fixed in this check

1. **Buildings with no year built were treated as old.** The survey calibration and the water damage
   took an unknown year as pre-1976, and earthquakes took it as 1960. Now they are averaged over the
   state's homes by when they were built (Census ACS). In Florida that is 28% before 1976, 45%
   1976-2001 and 27% 2002 on. Effect: a Florida house with no year built, 120 mph gust and 3 ft of
   surge now has a 17% chance of bad damage, down from 26%.
2. **The unknown-year mix in the wind curves was even thirds** (1960, 1990, 2010). Now each code
   cutoff year is weighted by the state's Census shares.
3. **Storm speed inland was 12 mph (range 8-24) with no source.** Now 13.6 mph (6.3-28.4), measured
   from 174 US landfalls.
4. **Seismic design level came from a state list** (all of Nevada "high", all of Tennessee
   "moderate"). Now from the USGS design maps at the place: Memphis and Charleston come out high,
   Billings low.
5. **Repair cost by damage category used round numbers with no source** (Affected 0-10%, Minor
   10-25%, Major 25-75%, Destroyed 75-100%). Now the ranges sit between Hazus's repair cost ratios:
   Affected 0-4.5%, Minor 4.5-21%, Major 21-67%, Destroyed 67-100%.
6. **No rule for design wind far inland when ASCE's tool fails.** Now 115 mph, ASCE 7-10's interior
   value.
7. **The year was fixed at 2026 in the code.** Now it reads today's date.
8. **Roof shape was half gable, half hip.** Now the shares surveyed on 2,551 houses, by year built. The calibration was refit: Hazus alone now gets counts closer (median count error 29% to 26%); the calibrated model is unchanged (log loss 0.632).
9. **The 18-inch flood line cited state guides "to be checked".** Checked against FEMA's 2021 PDA
   Guide and cited directly.

## Still open

- The rulesets' other defaults (garage, roof deck nails) are reasoned guesses, not measured. Garages could be
  read from the Mapillary street photos (149 of 150 Cedar Key buildings); roof nails can't be seen.
- Roof shapes come from Gulf Coast surveys (FL, TX, LA); Louisiana's houses had far more hip roofs (35% gable).
- NSI fills in walls and foundation from regional patterns where it has no record; Landfall can't
  tell which ones those are.
- JRC values outside the US are in 2010 prices.
