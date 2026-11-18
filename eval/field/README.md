# Field-validated ground truth for Cedar Key (Hurricane Helene)

Used by `scripts/score_field.py`. Results: `eval/cedar_key_field_score.txt`.

## Where the data comes from

StEER's own Helene project on DesignSafe (PRJ-5669) has published reports only
(the annotated media repository, the virtual and early-access reconnaissance
reports), not a building-by-building table. The building-level ratings here
come from a dataset that was checked against StEER's field surveys:

> Anton, A., Amini, M., Sedehi, F. J., Cox, D. T., Tomiczek, T. "An integrated
> pre- and post-storm dataset for Hurricane Helene (2024)." In *Virtual Damage
> Assessment and First-floor Elevation Estimation: Application to Horseshoe
> Beach and Cedar Key, Florida and Hurricane Helene (2024)*. DesignSafe-CI,
> PRJ-6245. https://doi.org/10.17603/ds2-q4n1-rf48. Open Data Commons
> Attribution License.

Files copied unchanged from that publication:

| file | what it is |
| --- | --- |
| `HxHelene_VDA_CedarKey.csv` | 771 Cedar Key buildings rated by trained assessors from post-storm street-level imagery (StEER's and Oregon State's) and aerial photos. Each building has a damage state (DS) for the roof, walls and elevated floors, foundation, windows and doors, and attached structures, and an overall DS (the worst component), on the Tomiczek et al. scale DS0 (none) to DS6 (collapse). It also has a first-floor height measured on street photos. |
| `HBCK_field_vda_comp.csv` | 28 buildings (14 in Cedar Key) rated in the field by StEER engineers on a 0-4 scale, and the virtual rating mapped onto it. This is how the virtual ratings were validated. |
| `Readme_*.txt` | the publishers' column descriptions |

## How the comparison works

* Each survey point is matched to the Landfall building outline it falls in,
  or the nearest within 3 m. 122 of Landfall's 150 downtown buildings match.
  Where several rated units sit on one outline, the worst rating counts.
* DS to FEMA category: DS0 none, DS1 affected, DS2-3 minor, DS4-5 major,
  DS6 destroyed. This crosswalk is ours, not an official one; the score is
  also reported with DS5 (partial collapse) counted as Destroyed.
* StEER field rating to FEMA: 0 none or affected, 1 minor, 2 major,
  3 major or destroyed, 4 destroyed.

## What the first run found (Nov 18, 2026)

* **Destroyed: 3 of 3 found, no false calls.** (Landfall's other two
  destroyed buildings have no survey point within 3 m.)
* **Damage below the roofline is missed.** The survey finds Minor or worse on
  16 matched buildings; Landfall finds 4. Of the 13 it rates lower, 8 have a
  roof the survey itself rates intact or cosmetic. The worst parts are walls
  and elevated floors (5), windows and doors (3), foundations (2) and decks or
  porches (2). 10 of the 13 stood in 0.5 ft or more of water. 11 of the 13
  were already sent to human review.
* **The two scales measure different things.** The survey rates damage to the
  structure and doesn't record water in the living space; FEMA's categories
  count it (18 inches or more is Major). Landfall's 35 "Affected" calls where
  the survey says DS0 are nearly all water around the house. With None and
  Affected merged, Landfall agrees on 107 of 122.
* **First-floor height is the biggest lever.** With the survey's own
  first-floor heights (measured on street photos) the PDA flood rule raises 38
  calls, most to Major: it finds 12 of 16 damaged buildings instead of 4, but
  the survey rates most of the raised houses DS0. Whether those houses had
  water inside (FEMA Major) while the structure stood (DS0) is exactly the
  question a field team has to settle, and a good one for the StEER data.

Street-level photos (Mapillary, `landfall/street.py`) target both gaps: they
show walls, openings and foundations, and the model reads the first-floor
height off them.

## With Mapillary street photos, flooding stated apart (Nov 18, 2026)

Seokhwan's rule from this run on: **flooding is not damage.** The category
rates the building itself; water around or inside it is stated beside the
category (`flood` in each record: depth around it, first-floor height, water
in the living space, and a sentence) and never raises it. The PDA flood rule
that used to raise calls is gone, and the model is told not to count water.

Run: `prepare`, `segment`, `flood`, `debris`, `street` and `assess` on a fresh
copy of Cedar Key. Drafts: `eval/cedar_key_drafts_street_2026-11-18.json`;
photo list: `eval/cedar_key_street_2026-11-18.json` (Mapillary image ids, no
photos are stored here); full score: `eval/cedar_key_field_score_street.txt`.

* **Coverage is nearly complete.** 149 of 150 buildings have at least one
  photo aimed at them, 337 in all. Every one is a 360° panorama from one
  contributor (Sitetour), taken October 4, 2024, eight days after landfall.
* **Agreement with the survey: 95 of 122 exact (78%), up from 65 (53%)**;
  kappa 0.53, the same. Destroyed 3 of 3, Major or worse 4 of 10 (was 3), and
  the agent's damage calls are right 4 of 5 times. No flooded house is called
  damaged because of water any more.
* **Minor or worse found: still 4 of 16.** The street photos did not add
  structural finds. Of the 12 missed, 8 have a sound roof; the worst parts
  are walls and elevated floors (4), windows and doors (3). On raised houses
  (b137, b138) the survey rates the walls or elevated floor DS4-5 and the
  model sees the house "intact on its pilings". The survey's own photos are
  from March to May 2025, ours from October 2024.
* **The flood note works.** By the survey's first floors, water reached the
  living space of 44 matched buildings. The note says so on 34, with no false
  alarms; 6 more had no first-floor reading. The model's first-floor reading
  is within 1 ft of the survey's on 78 of 108 (median error 1.0 ft).
* An earlier run the same day, with water still counted (PDA rule), found 9
  of 16 but called 19 survey-DS0 houses Major, all for water inside.
