# Testing Landfall's damage odds on storms it never saw

November 2026, Seokhwan Jang. Research with Prof. David Prevatt (University of Florida).

Landfall predicts how many buildings a disaster will damage. It works from how and when each building was built, plus the storm's wind and water. Until now, every check used one storm in one town: Hurricane Helene at Cedar Key. This folder tests Landfall on 7,431 surveyed buildings from six hurricanes in seven places. It also calibrates Landfall's damage odds against those surveys and puts 90% ranges on the counts. Then it asks two questions: does knowing how and when a building was built help, and how good is the AI at reading buildings?

Reproduce:

```
pip install -e .[dev,research]
python scripts/research_data.py   # downloads ~30 MB to data/research_raw/, writes buildings.csv
python scripts/research_eval.py   # results.json, predictions.csv, landfall/history/calibration.json
python scripts/research_ai.py     # ai_accuracy.json
```

## Data

| Place | Storm | Buildings | Survey | What "damaged" means |
|---|---|---|---|---|
| Cedar Key, FL | Helene 2024 | 771 | Anton et al., DesignSafe PRJ-6245 ([doi](https://doi.org/10.17603/ds2-q4n1-rf48)) | roof, walls/floors or foundation at damage state 2+ (DS4+ = badly) |
| Horseshoe Beach, FL | Helene 2024 | 313 | same | same |
| Fort Myers Beach, FL | Ian 2022 | 3,407 | Figueira et al., PRJ-5700 ([doi](https://doi.org/10.17603/ds2-c1g8-qy41)) | same scale and method |
| Panhandle, FL | Michael 2018 | 547 | WiSPD v1.0, Roueche, Nakayama, Kijewski-Correa, Prevatt, PRJ-4186 ([doi](https://doi.org/10.17603/ds2-ety5-qv78)) | StEER wind rating 2+ (3+ = badly) |
| Lake Charles area, LA | Laura 2020 | 424 | same | same |
| Florida Keys and SW FL | Irma 2017 | 836 | same | same |
| Rockport area, TX | Harvey 2017 | 1,133 | same | same |

All surveys are Open Data Commons Attribution. For each building, the table keeps only what Landfall could know before a storm:

* walls, foundation and stories from the National Structure Inventory fields;
* year built from the county tax roll;
* the FEMA flood zone;
* the ASCE 7-10 design wind speed;
* distance to the coast.

The storm's hazard at each building:

* **Wind:** the NIST/ARA wind fields (3-second peak gust at 10 m over open ground, fit to each storm's station data). For WiSPD, the per-house gusts that come with the data. At Cedar Key the NIST/ARA field gives 85 mph; NOAA station CKYF1 measured 84 mph.
* **Water:**
  * Cedar Key: 10.9 ft NAVD88, from a USGS high-water mark and NOAA gauge 8727520.
  * Fort Myers Beach: the survey's own fit to USGS and StEER high-water marks.
  * Horseshoe Beach: no USGS mark exists. We used NHC's report of 8 to 12 ft above the ground, which makes Horseshoe Beach the least certain place.

**Year built for destroyed houses (the missing 2024 tax roll).** The 2026 Levy County roll dropped 89 Cedar Key buildings, and 37 of them were badly damaged. The 2024 roll isn't online: the Florida Department of Revenue's data portal needs a login, and the historical parcel services only cover Collier County. Two other sources filled 33 of the 89:

* the county's effective year built from the survey's property records (17 buildings; 10 more said year 0 and are left unknown);
* the University of Florida Envision Heritage survey of Cedar Key, done in 2021 before the storm (16 more).

With these, Cedar Key has a year for 93% of buildings (was 88%), including 73% of the badly damaged ones. The remaining 56 need a records request to the Levy County Property Appraiser for the 2024 roll.

## 1. Unseen storms: Landfall today vs learned from other storms

There are two versions:

* **v1** is Landfall as it was: raw Hazus wind odds, plus water-damage rates and first-floor heights measured only at Cedar Key.
* **v2** learns from the other places in two ways:
  * The Hazus wind odds are recalibrated against roof damage on buildings that only saw wind: `logit p = a + b·logit(Hazus) + c·[built 2002+] + d·[built 1976-2001]`.
  * Water damage becomes a logistic model in five things: water depth above the first floor, water depth above the ground (deeper water carries bigger waves), V zone, and build era (2002 or later; 1976-2001). First-floor heights are pooled from all surveyed towns.

**Every place is predicted with v2 numbers fit without that place.** Cedar Key in v1 uses its own survey split in two halves.

**Damaged buildings (found vs predicted)**

| Place | Buildings | Gust mph | Water ft | Found | v1 | v2 | Log loss v1 → v2 | AUC v1 → v2 |
|---|---|---|---|---|---|---|---|---|
| Horseshoe Beach (Helene) | 313 | 99 | 10.0 | 226 | 92 | 163 | 1.08 → 0.62 | 0.46 → 0.73 |
| Cedar Key (Helene) | 771 | 85 | 4.5 | 139 | 135 | 243 | 0.46 → 0.50 | 0.69 → 0.71 |
| Fort Myers Beach (Ian) | 3,407 | 101 | 8.9 | 1,652 | 1,070 | 2,321 | 0.73 → 0.74 | 0.64 → 0.68 |
| Harvey | 1,133 | 131 | – | 538 | 403 | 468 | 0.73 → 0.63 | 0.72 → 0.70 |
| Irma | 836 | 111 | – | 297 | 86 | 290 | 0.97 → 0.63 | 0.69 → 0.67 |
| Michael | 547 | 132 | – | 300 | 310 | 300 | 0.90 → 0.69 | 0.57 → 0.59 |
| Laura | 424 | 133 | – | 136 | 133 | 192 | 0.55 → 0.61 | 0.78 → 0.78 |

Lower log loss is better. AUC is how well a version ranks the buildings that were damaged above the ones that weren't: 0.5 is a coin flip, 1.0 is perfect.

* **Overall: v2 is better calibrated.** Mean log loss over the seven places went from 0.77 to 0.63. v1 did worse than giving every building the same rate (0.70); v2 does better than that rate.
* **Ranking improved a little.** AUC within places went from 0.66 to 0.685, mostly at Horseshoe Beach (0.46 to 0.73), once water damage knew when each building was built.
* **Wind was the biggest error in v1.** Below about 120 mph gusts, Hazus gives almost no roof damage, but surveys found 10-45% of roofs damaged at 70-120 mph. At 140+ mph, Hazus is too sure (80-90% vs 29-64% found). Irma shows it best: v1 predicted 86 damaged where 297 were found; v2 predicts 290.
* **Water damage does not travel well between towns.** At the same water depth above the floor, Horseshoe Beach (near Helene's landfall) was damaged far more often than Cedar Key or Fort Myers Beach. Cedar Key and Fort Myers Beach agree with each other, which is notable because they are different storms. As a result, v2 overshoots Cedar Key and Fort Myers Beach and undershoots Horseshoe Beach. Water depth above the ground matters beyond depth above the floor: about +0.26 log-odds per foot, as waves suggest. Horseshoe Beach's water level is also the least certain.
* **Newer buildings came through the same water far better.** At the same water depth, buildings from 2002 or later had 0.24 times the odds of wall, floor or foundation damage of those built before 1976 (95% CI 0.12-0.46, standard errors clustered by town), and 1976-2001 buildings 0.46 times (0.38-0.55). The effect points the same way inside each of the three towns (weakest at Cedar Key). Adding it lowered held-out water log loss from 0.669 to 0.657, but did not fix the town-to-town miss in counts.
* **Badly damaged:** v1 found 48 of 165 at Horseshoe Beach, 545 of 1,089 at Fort Myers Beach and 14 of 105 for Irma. v2 gives 98, 1,664 and 105.

## 2. Wind (roof) calibration

This step uses only roof damage, on 3,992 buildings that saw wind alone: WiSPD houses with no surge, plus surveyed buildings whose first floor stayed dry. Fit on everything:

* damaged: a = 0.18, b = 0.32 (Hazus is far too steep), c = −0.37 (built 2002+), d = +0.21 (built 1976-2001);
* badly damaged: a = −0.29, b = 0.25, c = −1.61, d = −0.32.

In the app, the calibration fades to raw Hazus below 70 mph, because no surveyed storm had lower gusts.

* **Repair cost follows the calibrated odds.** For each building, Landfall finds the gust at which raw Hazus gives the calibrated chance of damage, and uses Hazus's repair cost at that gust. Where surveys found more damage than Hazus (below about 130 mph), repair cost goes up; at the top end, where Hazus was too sure, it goes down. Water repair cost is still the FIA depth curve. No survey here has repair dollars, so this is not checked against real costs.
* **App numbers in a Category 3:** Gainesville (wind only) goes from 87 to 447 damaged and from $25M to $116M in repairs. Cedar Key (mostly surge) goes from 371 to 578 damaged and from $64M to $69M.
* **Cedar Key's own dry roofs are a known miss.** 9 of 342 had roof damage, while v2 says about 42. Other storms found 10-20% of roofs damaged at 70-100 mph; Cedar Key found 3%.

## 3. Ranges

The ranges combine two sources of uncertainty:

* Model spread: how far off v2 was at the *other* places, as the root-mean-square of log(predicted / found) = 0.31. Six or seven places give only a rough spread, so ranges treat a new place as a Student's t prediction (sd × √(1 + 1/n), t with n degrees of freedom) instead of a normal one. That widens them by about a third.
* The wind field's uncertainty (±10% gust), each survey's water uncertainty, and building-to-building chance.

**The 90% ranges now hold the true count in all 7 places.** Before the small-sample correction, Cedar Key was a miss (148 to 348 predicted, 139 found). It is still the place v2 is most off (1.75 times too many), so the ranges are wide rather than the prediction good.

| Place | Predicted | 90% range | Found |
|---|---|---|---|
| Horseshoe Beach | 163 | 84-275 | 226 |
| Cedar Key | 243 | 131-454 | 139 |
| Fort Myers Beach | 2,321 | 1,180-3,381 | 1,652 |
| Harvey | 468 | 211-846 | 538 |
| Irma | 290 | 102-608 | 297 |
| Michael | 300 | 127-512 | 300 |
| Laura | 192 | 88-372 | 136 |

The app now says, for example: "about 578 (likely between 308 and 688) of the 688 buildings will likely be damaged." Its range uses a model spread of 0.38 (0.31 widened the same way for seven places, so that its ±1.645 spread is a t 90% range) plus chance. A chosen scenario has no hazard uncertainty, because the category is given. Other disasters (tornado, earthquake, flood) are untested, so their ranges use a wider 0.5.

## 4. Does how and when a building was built matter?

**Baselines and models with one fact hidden.** Every model and baseline is fit without the place it's scored on.

| Model | AUC within place | Mean log loss |
|---|---|---|
| Landfall v2 | **0.685** | **0.632** |
| v2 without year built | 0.662 | 0.667 |
| v2 without flood zone | 0.655 | 0.634 |
| v2 without walls | 0.684 | 0.633 |
| v2, nothing known about the building | 0.615 | 0.657 |
| Landfall v1 | 0.660 | 0.774 |
| Same rate for all | 0.500 | 0.696 |
| Year built only | 0.495 | 0.705 |
| Distance to coast only | 0.543 | 0.688 |
| Wind and water only | 0.623 | 0.656 |
| Boosted trees, everything | 0.638 | 0.703 |

Without any building facts, ranking falls from 0.685 to 0.615. Year built carries the most (0.685 to 0.662), then flood zone. Year built alone ranks no better than chance: it matters through how it changes the effect of wind and water, not by itself. Hiding foundation or stories changes almost nothing, partly because the National Structure Inventory's foundation is wrong most of the time (see section 5).

In a data-driven model, shuffling **year built drops AUC by 0.065, more than shuffling wind (0.051)**. Next come flood zone (0.020) and walls (0.010).

**Year built at the same wind.** This uses roof damage on wind-only buildings, with storm and place held fixed (3,952 buildings).

| Wind | Built before 1976 | 1976-2001 | 2002 or later (Florida Building Code era) |
|---|---|---|---|
| 70-100 mph | 10% | 19% | 8% |
| 100-120 mph | 26% | 37% | 17% |
| 120-140 mph | 58% | 62% | 42% |
| 140-175 mph | – | 64% | 29% |

Compared with houses built before 1976, houses built in 2002 or later had **0.58 times the odds of roof damage (95% CI 0.34-0.99, p = 0.045**, standard errors clustered by place). The result holds when any one place is dropped (0.48-0.72). It is strong within Fort Myers Beach (0.18) and Harvey (0.64), but absent within Irma (1.2) and Michael (0.9).

Houses built from 1976 to 2001 did no better than older ones. Beyond what Hazus already assumes, they did *worse* (odds ratio 2.0, CI 1.3-3.2). That would fit the known weak 1980s-90s roof practice before Hurricane Andrew's code changes took hold. This is worth asking Prof. Prevatt about.

## 5. How well the AI reads buildings, and what aerial photos miss

Cedar Key, the 122 buildings matched to the survey. Accuracy is shown with its 95% interval; kappa is agreement beyond chance (0 = chance, 1 = perfect).

| What | Street-photo AI vs assessors | vs UF 2021 (independent, before the storm) | Structure inventory vs assessors |
|---|---|---|---|
| Walls | 86% (78-91), kappa 0.57 | 89% (82-94), kappa 0.69 | 16% (11-24), kappa −0.06 |
| Foundation height (at grade / short / tall) | 57% (48-66), kappa 0.30 | 60% (50-69), kappa 0.32 | 32% (24-41), kappa 0.00 |
| Stories | 84% (76-90), kappa 0.54 | 90% (82-94), kappa 0.65 | 73% (64-80), kappa 0.27 |

First-floor height read from street photos was a median 0.9 ft off from the survey (70% within 1 ft) and 1.0 ft off from UF's laser-scanned floors.

The structure inventory calls most wooden Cedar Key houses masonry. Its foundation is no better than chance.

**Hidden from above:** these damaged buildings had an intact roof, so aerial photos cannot show the damage.

| Town | Share of damaged buildings | Share of badly damaged |
|---|---|---|
| Cedar Key | 58% (80 of 138) | 46% |
| Horseshoe Beach | 29% | 14% |
| Fort Myers Beach | 24% | 16% |

The agent's own aerial-only drafts caught 3 of 8 damaged buildings whose roofs were damaged, but only 1 of 5 whose roofs were intact. These samples are small.

## Validation notes (checked before sharing)

* **Joins:** Helene FIDs match the Cedar Key table 1:1 (771 of 771 by coordinates and roof rating). No duplicate buildings. WiSPD has 2,940 rated hurricane houses, all kept.
* **No peeking:** every v2 score, baseline and range is fit without the place it scores.
* **The damage scales differ by survey:** the virtual surveys use Tomiczek DS0-6, WiSPD uses StEER 0-4, and Hazus uses DS1-4. Lining up "damaged" as roof DS2+, StEER 2+ and Hazus DS2+ is our assumption.
* **Missing build years skew toward destroyed houses.** The year-built effect could be biased if those houses were mostly old ones.
* **Seven places is few:** cluster-robust intervals with 7 clusters are rough, so we also report each place's own estimate and the drop-one-place spread.
* **"Wind only" uses the survey's own record of surge (WiSPD)** or a dry first floor (virtual surveys). Both could mislabel some houses.
* **Horseshoe Beach's water level is an estimate:** NHC's 8-12 ft above ground.
* **13 appraiser records said year built 0.** They are now treated as unknown. Before that fix, "year built only" looked like it ranked buildings (0.61); it does not (0.50).

## Next steps

1. Request the 2024 Levy County roll for the 56 Cedar Key buildings still missing a build year.
2. Check repair costs against real claim or permit dollars (for example NFIP claims or county permit values).
3. Add a wave-exposure measure (distance to open water, or wave height from a surge model such as ADCIRC/CERA) so water damage carries over between towns.
4. Ask Prof. Prevatt whether lining up roof DS2+ with Hazus DS2 is right, and about the 1976-2001 result.
5. Add Idalia 2023 when a building-level survey is published. Only StEER's preliminary report is on DesignSafe so far.
