# Build log

Building period: Oct 26 to Nov 4, 2026 (confirmed at the kickoff on Oct 26).

## Oct 26 (Mon)

- Created the repository.
- Geometry helpers: lon/lat to Web Mercator pixels, building crop windows with a meter margin.
- Pre/post alignment: edge-map normalized cross-correlation over a ±12 px shift search, with a confidence flag for flat or destroyed crops.
- FEMA categories and the deterministic verifier (no under-calls; at most one step above what the measurements show; unaligned buildings go to human review).
- 18 tests passing.

### Oct 26, evening: real data for Cedar Key

- Sources wired up: NOAA flight `20240928b` (two days after Helene's landfall) covers Cedar Key at zoom 19 (0.26 m/px); the newest NAIP before the storm is Feb 15, 2023 (0.3 m); Microsoft footprints give 825 buildings in town, of which the 150 nearest downtown and Dock Street (40 m² or larger) are the working set.
- Caveat: the NAIP baseline predates Hurricane Idalia (Aug 2023), and some lots were rebuilt or newly built between the two flights.
- The day-1 aligner (whole-crop edge correlation) lined up only 5 of 150 on real photos. The post-storm flight is dark with long shadows, and houses on pilings lean differently in each flight, so roofs and ground disagree about the shift.
- New aligner: find the pre-storm roof inside a ±7 m window of the post-storm photo on equalized edges, then check each building against its 8 nearest neighbors. 90 of 150 are matched by their own roof; the other 60 are placed by their neighbors and flagged.
- Eyeballed all 150 on contact sheets: about 133 of 150 sit within roughly 1.5 m of the right spot. The misses are mostly taller downtown buildings that lean further than their neighbors.
- First SAM 3.1 run (300 crops, "roof"): all five visibly destroyed houses in the set have their roof in the pre-storm mask and nothing in the post-storm one. About 20 more buildings are false alarms where SAM missed a roof in the dark post-storm photo or the placement was off.
- 26 tests passing.

### Oct 26, night: fewer false alarms, first agent run

- The "roof" prompt alone missed about one roof in six in the dark post-storm flight. The structure mask now combines "roof", "house" and "building". The post-storm side is segmented on a window 4 m wider and matched to the pre-storm shape, which absorbs leaning and misplaced houses. Buildings flagged at 25% loss or more dropped from 27 to 11, and all five destroyed houses are still flagged.
- Agent loop (`landfall/agent.py`): Muse Spark Open on Baseten sees each before/after pair with the measurements and proposes a FEMA category. The verifier checks the call, and the model can revise it or stand by it and send it to human review. Calls below 0.7 confidence go to Muse Spark 1.3 for a second opinion.
- First version forced the model to agree with the measurements and turned mask noise into 42 "Minor" calls. Two changes fixed it: the minor roof-loss threshold went from 5% to 15% (the noise level between flights), and a model that disagrees with the numbers now sends the building to an assessor instead of giving in.
- Run on all 150: 5 Destroyed (exactly the five destroyed houses), 11 Affected, 134 no visible damage. 17 went to human review, each with a sentence on what the measurement got wrong (trees over the roof, misplaced outline, new build since 2023). 20 calls went to Muse Spark.
- 31 tests passing.

### Oct 26, late: flood depth and a first evaluation

- Flood: the USGS STN API moved to `api.waterdata.usgs.gov/stnservices`. Helene has one surveyed high-water mark in Cedar Key: 10.96 ft NAVD88, a seed line inside a bathroom, 5.8 ft above ground. The NOAA Cedar Key tide gauge peaked at 10.84 ft NAVD88 (Sep 27, 04:42 UTC). The two agree, and their median, 10.9 ft, is used as the water surface. Ground comes from USGS 3DEP 1 m lidar (EPQS) at each building's centroid. 70 of 150 buildings had water above ground, up to 11.6 ft. The old town on the ridge stayed dry, and all five destroyed houses sit on low ground near the water.
- FEMA flood rule: the PDA criteria are about water in the living space (the 18-inch line between Minor and Major, from state field guides derived from the PDA Guide). We only know depth above ground, and many houses here are on pilings, so water around a house only proves Affected unless the first-floor height is known. `Measures.first_floor_ft` is there for when it is.
- Hand labels: 50 buildings (45 random plus the 5 destroyed), labeled by eye from the photo pairs before comparing with the agent. Caveats are in `eval/cedar_key_labels.json`: one labeler, not a trained assessor, and aerial labels can't see flood inside a house.
- Score: 37/50 exact, 50/50 within one category, 4 of 5 destroyed called Destroyed (the fifth called Major, with walls still standing), and no false Destroyed. 11 of 13 disagreements were already flagged for human review. The two silent misses are debris in the yard (Affected) that the agent called no damage.
- Six buildings failed on the first full run because the model ran out of tokens while reasoning. The limit went from 3,000 to 8,000 tokens and the six reran cleanly.
- 36 tests passing.

### Oct 27: a second labeler

- Harry labeled the same 50 buildings blind on a web page (same photo pairs, shuffled order, no other labels shown). His labels are in `eval/cedar_key_labels_harry.json`.
- The labelers agree on 43/50 (kappa 0.49, low because almost everything is "none"), and all 50 are within one category. They split on yard debris (5 houses: Claude said Affected, Harry said none) and on b094 and b098 (Claude said Destroyed, Harry said Major).
- Against Harry's labels the agent gets 38/50 exact (40/50 with the flood rule) and 50/50 within one, and 11 of 12 disagreements were already sent to review. The Affected-or-none line is where people disagree too, so the agent's misses there are within human noise.
