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
