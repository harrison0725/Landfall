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

### Oct 27: debris and tarps

- SAM 3.1 finds nothing for "debris" on a tight building crop. With about 16 m of yard around the house and the phrase "pile of debris" (or "trash pile") it does find heavy debris, but it misses scattered branches and shingles and now and then marks dark tree canopy. A hit is evidence; a miss is not proof of a clean yard.
- `scripts/debris_area.py` measures new debris in an 8 m ring around each house (post-storm masks minus pre-storm ones) and the share of the roof SAM calls "blue tarp". 750 SAM calls, about $1.90.
- 5 of 150 houses have debris over 5% of the yard, and 1 roof (b087) is mostly marked as a tarp. These numbers go to the model as hints and raise the verifier floor (tarp ≥5% → Minor, debris ≥5% → Affected).
- Rerunning those 6: b098 went from Major to Destroyed (the model now cites the debris piles), and b087 went to Major with review (a possible full-roof tarp and 10.6 ft of water). Destroyed is now 5/5 against Claude's labels and 3/3 plus 2 extra against Harry's (he called b094 and b098 Major). The two light-debris misses (b063, b110) are still misses.

### Oct 27: the area agent

- `landfall/area.py`: Muse Spark Open with five tools over the building drafts (area_overview, find_buildings, get_building, plan_inspections, pda_totals). Both Muse Spark Open on Baseten and Muse Spark on Meta Model API return OpenAI-style tool calls.
- `plan_inspections` picks severe, unsure and deep-water houses and orders them as a nearest-neighbor walk (Cedar Key: 20 stops, about 1.5 km).
- First PDA draft: the model flipped the sign of latitudes it copied from the tools. Tools now return a preformatted "lat, lon" string, and the answer's building ids are checked against the area.
- The PDA draft takes about 20 seconds and 7 to 12 tool calls. Example in `examples/cedar_key_pda_summary.md`.

### Oct 27: the flood floor

- The biggest error left was not debris. 18 houses had 0.5 ft or more of surveyed water around them and were still drafted "none": the checker objected, but its message lumped flood together with noisy roof-loss masks, and the model argued both away as shadows and trees.
- The checker now raises flood as its own objection and says it is surveyed, not read off the photos. On a rerun the model conceded on all 18. As a backstop, `assess()` applies the flood floor itself if the model still refuses, and says so in the evidence and the trace.
- Score with the flood rule: 47/50 against Claude's labels and 48/50 against Harry's (was 37 to 39). The 3 and 2 remaining misses are exactly the buildings where the labelers disagree with each other: light yard debris (b063, b067, b110) and b094/b098.
- Tried more SAM debris prompts at 4x zoom ("debris", "scattered debris", "branches", "wood debris", "trash"): still nothing on light debris.

### Oct 27: tried Muse Wonder

- Meta Model API also serves `muse-wonder-1.0` (a reasoning model that takes images), `muse-image-1.0` (image generation) and `muse-voice-transcribe-1.0`. Only Muse Wonder could change accuracy, so it got the same 50 eval buildings, prompt and measurements as the agent, in a single pass without the checker.
- Result: 33/50 against Claude's labels and 32/50 against Harry's, counting the flood rule (the agent with its checker: 47 and 48). Most misses are 12 flooded houses it called Major by assuming the house sits at grade, which the PDA rule can't support without first-floor height. On what the photos show, it was no better than the agent: it caught the light debris at b063 but missed b067 and b110, and added Affected at b057 and Minor at b112, where both labelers saw nothing.
- It takes about 15 s and 1,600 tokens per building. Not added to the pipeline; Muse Voice Transcribe is a better fit for asking the area agent questions by voice in the app.

### Oct 27: the app

- The scripts' work moved into `landfall/pipeline.py` as stages that report progress. Rerunning Cedar Key through it reproduced damage.json, debris.json and flood.json exactly.
- `landfall/storms.py` reads NOAA's per-storm flight catalog (storms.ngs.noaa.gov/storms/<storm>/services/index.json) and probes a tile to find the earliest flight over a box; tide gauges come from the CO-OPS station list.
- The agent now runs the pipeline itself: the stages are tools (locate_imagery, prepare_area, measure_structures, measure_flood, look_for_debris, draft_categories) next to the question tools. The web app (FastAPI, Leaflet) streams every tool call, stage summary and building draft as it happens. Voice questions go through Muse Voice Transcribe (POST /v1/asr/transcribe, 16 kHz WAV).
- First unseen town, Treasure Island (Helene, NOAA flight 20240930b): 28 buildings in about 5.5 minutes, start to finish. Sped up by running more SAM and model calls at once.
- What the unseen town taught us:
  - Two "destroyed" buildings had been torn down between the Feb 2023 photo and the storm (one lot is now weeds and fencing, the other is paved parking). The model can now say `demolished_before_storm`; those buildings get "none", skip the measurement floors, and go to review. Every Destroyed call now gets a second opinion from Muse Spark, which caught both.
  - A town often spans two NAIP scenes; the pre-storm mosaic now fills scene edges from the neighboring scene.
  - The prepare step warns when another storm hit between the pre-storm photos and this one (Idalia, for anything with 2023 photos), and the flood step widens its search for high-water marks when none are close, saying the depths are rougher.
- Cedar Key eval unchanged after all this: 47/50 and 48/50, Destroyed 5/5 against Claude's labels.

### Oct 27: repair cost, who needs help first, claim packets

Harry asked for features from a list (damage detection, severity, repair cost, insurance claim help, damage mapping, emergency priorities). Detection, severity and mapping already exist; windows and walls can't be seen in photos taken straight down. Added the other three:

- Repair cost (`cost.py`, `parcels.py`): local building value per square foot from the Florida statewide tax roll (Cedar Key $190 from 631 homes, Treasure Island $257 from 1,480), times each building's floor area. Damage share is the larger of a range by drafted category and FEMA P-784's flood bands. The first-floor height comes from the year built and the FEMA flood zone: built 1985 or later in a zone with a base flood elevation means the floor is at the BFE; before 1975 means 1 to 3 ft above ground. That took the Cedar Key range from $5M–$48M down to $7.9M–$19.4M, with 20 buildings likely substantially damaged under FEMA's 50% rule and 51 possibly.
- The current roll is after the storm, so destroyed houses have no year built; their floor stays unknown and the range stays wide.
- Who needs help first (`priority.py`): Cedar Key has 16 households likely displaced and 32 possibly. The four destroyed buildings on the waterfront are a motel (land use 039), so they count as lodging lost, not households; they are listed separately with eight other businesses.
- Claim packet (`packet.py`): one printable page per building, opened from the map card.
- The agent gets `estimate_costs_and_needs` as a pipeline step and `repair_costs` and `urgent_needs` as question tools; the PDA summary gained "Who needs help first" and "Repair cost" sections.
