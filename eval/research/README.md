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

**Year built for destroyed houses (the missing 2024 tax roll).** The 2026 Levy County roll dropped 89 Cedar Key buildings, and 37 of them were badly damaged. The 2024 roll isn't online: the Florida Department of Revenue's data portal needs a login, and the historical parcel services only cover Collier County. Two other sources filled 43 of the 89:

* the county's effective year built from the survey's property records (27 buildings);
* the University of Florida Envision Heritage survey of Cedar Key, done in 2021 before the storm (16 more).

With these, Cedar Key has a year for 94% of buildings (was 88%), including 79% of the badly damaged ones. The remaining 46 need a records request to the Levy County Property Appraiser for the 2024 roll.

## 1. Unseen storms: Landfall today vs learned from other storms

There are two versions:

* **v1** is Landfall as it was: raw Hazus wind odds, plus water-damage rates and first-floor heights measured only at Cedar Key.
* **v2** learns from the other places in two ways:
  * The Hazus wind odds are recalibrated against roof damage on buildings that only saw wind: `logit p = a + b·logit(Hazus) + c·[built 2002+] + d·[built 1976-2001]`.
  * Water damage becomes a logistic model in three things: water depth above the first floor, water depth above the ground (deeper water carries bigger waves) and V zone. First-floor heights are pooled from all surveyed towns.

**Every place is predicted with v2 numbers fit without that place.** Cedar Key in v1 uses its own survey split in two halves.

**Damaged buildings (found vs predicted)**

| Place | Buildings | Gust mph | Water ft | Found | v1 | v2 | Log loss v1 → v2 | AUC v1 → v2 |
|---|---|---|---|---|---|---|---|---|
| Horseshoe Beach (Helene) | 313 | 99 | 10.0 | 226 | 92 | 173 | 1.08 → 0.69 | 0.46 → 0.56 |
| Cedar Key (Helene) | 771 | 85 | 4.5 | 139 | 135 | 229 | 0.46 → 0.50 | 0.69 → 0.68 |
| Fort Myers Beach (Ian) | 3,407 | 101 | 8.9 | 1,652 | 1,070 | 2,319 | 0.73 → 0.75 | 0.64 → 0.65 |
| Harvey | 1,133 | 131 | – | 538 | 403 | 468 | 0.73 → 0.63 | 0.72 → 0.70 |
| Irma | 836 | 111 | – | 297 | 86 | 290 | 0.97 → 0.63 | 0.69 → 0.67 |
| Michael | 547 | 132 | – | 300 | 310 | 300 | 0.90 → 0.69 | 0.57 → 0.59 |
| Laura | 424 | 133 | – | 136 | 133 | 192 | 0.55 → 0.61 | 0.78 → 0.78 |

Lower log loss is better. AUC is how well a version ranks the buildings that were damaged above the ones that weren't: 0.5 is a coin flip, 1.0 is perfect.

* **Overall: v2 is better calibrated.** Mean log loss over the seven places went from 0.77 to 0.64. v1 did worse than giving every building the same rate (0.70); v2 does better than that rate.
* **Ranking barely changed.** AUC within places stayed about the same (0.66).
* **Wind was the biggest error in v1.** Below about 120 mph gusts, Hazus gives almost no roof damage, but surveys found 10-45% of roofs damaged at 70-120 mph. At 140+ mph, Hazus is too sure (80-90% vs 29-64% found). Irma shows it best: v1 predicted 86 damaged where 297 were found; v2 predicts 290.
* **Water damage does not travel well between towns.** At the same water depth above the floor, Horseshoe Beach (near Helene's landfall) was damaged far more often than Cedar Key or Fort Myers Beach. Cedar Key and Fort Myers Beach agree with each other, which is notable because they are different storms. As a result, v2 overshoots Cedar Key and Fort Myers Beach and undershoots Horseshoe Beach. Water depth above the ground matters beyond depth above the floor: about +0.26 log-odds per foot, as waves suggest. Horseshoe Beach's water level is also the least certain.
* **Badly damaged:** v1 found 48 of 165 at Horseshoe Beach, 545 of 1,089 at Fort Myers Beach and 14 of 105 for Irma. v2 gives 117, 1,708 and 105.

## 2. Wind (roof) calibration

This step uses only roof damage, on 3,992 buildings that saw wind alone: WiSPD houses with no surge, plus surveyed buildings whose first floor stayed dry. Fit on everything:

* damaged: a = 0.18, b = 0.32 (Hazus is far too steep), c = −0.37 (built 2002+), d = +0.21 (built 1976-2001);
* badly damaged: a = −0.29, b = 0.25, c = −1.61, d = −0.32.

In the app, the calibration fades to raw Hazus below 70 mph, because no surveyed storm had lower gusts.

* **Hazus repair costs are not recalibrated yet.** So Cedar Key in a Category 3 storm now shows about 598 of 688 buildings damaged but the same repair cost ($65M). Calibrating repair cost is the next step.
* **Cedar Key's own dry roofs are a known miss.** 9 of 342 had roof damage, while v2 says about 42. Other storms found 10-20% of roofs damaged at 70-100 mph; Cedar Key found 3%.

## 3. Ranges

The ranges combine two sources of uncertainty:

* Model spread: how far off v2 was at the *other* places, as the root-mean-square of log(predicted / found) = 0.29, or about ±30%.
* The wind field's uncertainty (±10% gust), each survey's water uncertainty, and building-to-building chance.

**The 90% ranges held the true count in 6 of 7 places.** Cedar Key was the miss: 148 to 348 predicted, 139 found.

| Place | Predicted | 90% range | Found |
|---|---|---|---|
| Horseshoe Beach | 173 | 105-266 | 226 |
| Cedar Key | 229 | 148-348 | **139** |
| Fort Myers Beach | 2,319 | 1,457-3,372 | 1,652 |
| Harvey | 468 | 218-863 | 538 |
| Irma | 290 | 126-621 | 297 |
| Michael | 300 | 135-477 | 300 |
| Laura | 192 | 83-316 | 136 |

The app now says, for example: "about 598 (likely between 373 and 688) of the 688 buildings will likely be damaged." Its range uses the 0.29 model spread plus chance. A chosen scenario has no hazard uncertainty, because the category is given. Other disasters (tornado, earthquake, flood) are untested, so their ranges use a wider 0.5.

## 4. Does how and when a building was built matter?

**Baselines and models with one fact hidden.** Every model and baseline is fit without the place it's scored on.

| Model | AUC within place | Mean log loss |
|---|---|---|
| Landfall v2 | **0.662** | **0.643** |
| v2 without year built | 0.653 | 0.653 |
| v2 without flood zone | 0.624 | 0.641 |
| v2 without walls | 0.658 | 0.645 |
| v2, nothing known about the building | 0.616 | 0.654 |
| Landfall v1 | 0.660 | 0.774 |
| Same rate for all | 0.500 | 0.696 |
| Year built only | 0.611 | 0.713 |
| Distance to coast only | 0.543 | 0.688 |
| Wind and water only | 0.623 | 0.656 |
| Boosted trees, everything | 0.650 | 0.702 |

Without any building facts, ranking falls from 0.662 to 0.616. Year built and flood zone carry most of that. Hiding foundation or stories changes almost nothing, partly because the National Structure Inventory's foundation is wrong most of the time (see section 5).

In a data-driven model, shuffling **year built drops AUC by 0.069, more than shuffling wind (0.052)**. Next come flood zone (0.028) and walls (0.012).

**Year built at the same wind.** This uses roof damage on wind-only buildings, with storm and place held fixed (3,954 buildings).

| Wind | Built before 1976 | 1976-2001 | 2002 or later (Florida Building Code era) |
|---|---|---|---|
| 70-100 mph | 10% | 19% | 8% |
| 100-120 mph | 26% | 37% | 17% |
| 120-140 mph | 58% | 62% | 42% |
| 140-175 mph | – | 64% | 29% |

Compared with houses built before 1976, houses built in 2002 or later had **0.58 times the odds of roof damage (95% CI 0.34-0.99, p = 0.045**, standard errors clustered by place). The result holds when any one place is dropped (0.48-0.72). It is strong within Fort Myers Beach (0.18) and Harvey (0.64), but absent within Irma (1.2) and Michael (0.9).

Houses built from 1976 to 2001 did no better than older ones. Beyond what Hazus already assumes, they did *worse* (odds ratio 2.0, CI 1.3-3.1). That would fit the known weak 1980s-90s roof practice before Hurricane Andrew's code changes took hold. This is worth asking Prof. Prevatt about.

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

## Next steps

1. Request the 2024 Levy County roll for the 46 Cedar Key buildings still missing a build year.
2. Calibrate repair cost the way wind damage was calibrated.
3. Add a wave-exposure measure (distance to open water, or wave height from a surge model such as ADCIRC/CERA) so water damage carries over between towns.
4. Ask Prof. Prevatt whether lining up roof DS2+ with Hazus DS2 is right, and about the 1976-2001 result.
5. Add Idalia 2023 when a building-level survey is published. Only StEER's preliminary report is on DesignSafe so far.
