# Landfall

An agent that turns aerial and satellite photos taken after a hurricane, wildfire, earthquake or flood into a draft FEMA Preliminary Damage Assessment: every building compared with its pre-storm photo, flood depth estimated, each one sorted into FEMA's categories with its evidence, and a report a county can work from.

**Built during the Meta Global AI Developer Hackathon, Oct 26 to Nov 4, 2026.** Everything in this repository was written during the event. See [BUILD_LOG.md](BUILD_LOG.md) for a dated record of what was built each day.

> Landfall produces a draft for trained assessors. It is not an official damage determination.

## Status

Day 1. Working so far:

- `landfall/geo.py`: Web Mercator math to turn building outlines into pixel crops of aerial tiles.
- `landfall/sources.py`: downloads NOAA post-storm tiles, warps NAIP pre-storm photos into the same pixel grid, and pulls Microsoft building outlines.
- `landfall/align.py`: finds each building's pre-storm roof in the post-storm photo (edge matching, then a check against its neighbors). Buildings whose roof is not where the neighbors say it should be are flagged, which is how destroyed houses show up.
- `landfall/sam.py`: SAM 3.1 concept masks ("roof") on Meta Model API.
- `landfall/measure.py`: the building's structure mask (SAM "roof", "house" and "building" combined) before and after, matched by shape so a house that leans a few meters still counts, and the share that is gone.
- `landfall/agent.py`: Muse Spark Open looks at each before/after pair with the measurements and proposes a FEMA category; the verifier checks it and the model revises; unsure calls go to Muse Spark for a second opinion. Every step is kept in a trace.
- `landfall/flood.py`: storm water surface from USGS high-water marks and NOAA tide gauges, ground from USGS 3DEP lidar, depth above ground per building.
- `landfall/fema.py`: FEMA category definitions and the deterministic verifier the agent's calls are checked against.
- `landfall/pipeline.py`: the stages (prepare, segment, flood, debris, assess) as library functions with progress events; the scripts, the agent and the app all call these.
- `landfall/session.py` and `landfall/app.py`: the conversation the app runs. The agent gets the pipeline stages as tools, so it runs the assessment itself, then answers questions with the area tools.
- `landfall/storms.py`: finds the NOAA post-storm flight and tide gauges for any box, from NOAA's own flight catalog.
- Other disasters (`landfall/storms.py`): the Los Angeles wildfires (NOAA flew them in January 2025; demo area Altadena), the Texas Hill Country floods (NOAA, July 2025; demo area Ingram, with water depth at each building from the USGS high-water marks nearest it, since a river's level falls along its course) and the Myanmar earthquake (Maxar, March 2025; demo area Mandalay). Each kind of disaster has its own description of what each FEMA category looks like from the air (`agent.HAZARDS`): burned to the slab, a pancaked floor under a whole roof slab, a house swept off its foundation. On Altadena the drafts match a by-eye read of all 51 buildings (`eval/altadena_labels.json`). Earthquake damage is the hardest to see from a satellite: a different sun angle moves shadows, so Mandalay's calls are leads for a field check. The catalog also holds Hurricanes Michael, Ida, Nicole, Debby and Francine and the April 2025 Kentucky floods (NOAA), and the Turkey–Syria earthquake and the Valencia, Rio Grande do Sul and Mokwa floods (Maxar): every listed disaster has photos from both before and after. Older US storms aren't served by NOAA as map tiles, Hawaii has no NAIP before photos (so not Maui), and many Maxar events have only one side or no building outlines, so those are left out: every disaster on the menu has a before and an after photo where it opens, and satellite ones ship after-photo map tiles for that spot (`scripts/maxar_tiles.py` writes `demo/tiles/<layer>/layer.json`). A building hidden by cloud in the after photo is not sent to the model; it goes straight to an assessor.
- `landfall/maxar.py`: storms outside the US. Before and after satellite photos from Maxar's Open Data program (Hurricane Melissa in Jamaica and Cyclone Ditwah in Sri Lanka, plus the satellite disasters below), picking the earliest clear pass after landfall and the newest clear pass before it. Building outlines come from Microsoft's worldwide dataset. Surveyed flood depth and tax-roll values are US and Florida data, so outside the US the drafts rest on the photos, flood depth is reported as unknown, repair cost uses the country's average building value from the European Commission JRC global flood damage database (rougher, at 2010 prices), and every category also carries the international grade (Copernicus EMS: no visible damage, possibly damaged, damaged, destroyed).
- `landfall/photos.py`: your own photos. Upload an after photo of the land (a drone or plane shot), and a before photo if you have one, then draw a box over where they were taken. The before photo is matched to the after one (SIFT features and a homography), Segment Anything finds the buildings in the photo itself, and the same pipeline runs from there: structure loss, flood depth in the US, the agent's drafts, costs and needs. Without a before photo, the after photo is found in the archive's before photo of the spot (NAIP in the US, Maxar elsewhere) by matching edges over quarter turns and sizes around the drawn box; that places it on the map properly, the archive becomes the before side and the map's building outlines are used. If it can't be found there, each building is judged from the after photo alone and sent for a field check. The uploader says where the photos were taken (a city or town), what happened (hurricane, flood, wildfire, earthquake) and the year, plus the day and a name if they know them. Nothing has to be drawn: the agent finds where in the town the photo was taken (`pipeline.find_upload`). It matches the photo's edge directions against Esri's newest photo of up to 5 × 5 km of the town at about 1 m a pixel, over quarter turns and ground widths from 60 m to 1.5 km. Each size's peak is ranked by how far it stands above that size's own scores, and the best few spots are checked on sharp photos as a drawn box would be. If none holds up, the user is asked to drag a box over the spot. A street address (or GPS in the photo's EXIF, read before the metadata is dropped) narrows the search to 2 km (1.2 km) around it, which takes a few seconds instead of half a minute. Esri's photo dates are cached on disk, and a disaster newer than Esri's latest photo needs only one date lookup. The map goes to that place; if Landfall has a disaster of that kind there that year, the photos are tied to it, and otherwise to a new event, such as one that just happened. The archive's before photo is the newest one before that day, or before the year began when only the year is given. For a new disaster it comes from Esri's World Imagery Wayback (`landfall/wayback.py`): every past version of Esri's worldwide basemap, with a metadata service giving each spot's photo date, so a binary search over the versions finds the newest photo taken before the date anywhere in the world (in the US the newer of that and NAIP is used). The map shows that photo, labelled Before with its date, while the box is placed. It is also the fallback when NAIP or Maxar has nothing for a catalog disaster. Esri imagery is used under Esri's terms of use, with credit. With the day, a new US hurricane's water level still comes from NOAA's live tide gauges.
- `landfall/cost.py` and `landfall/parcels.py`: repair cost ranges. Building values come from the Florida tax roll (median value per square foot of the homes nearby); flood damage follows FEMA's P-784 bands, with the first-floor height estimated from the year built and the FEMA flood zone. Buildings whose repairs could reach half their value are flagged under FEMA's 50% substantial-damage rule. No owner names or addresses are kept.
- `landfall/priority.py`: who needs help first. Households likely displaced (Major or Destroyed, or 18 inches or more of water inside at the estimated floor height), motels and shops listed separately, and buildings ranked by urgency.
- `landfall/packet.py`: a printable evidence packet per building for an insurance claim or a FEMA application: the photo pair with dates, what the photos show, flood water with its sources, the property record, the repair range and how the draft was made.
- `landfall/area.py`: the area agent. Muse Spark Open calls tools over the building drafts (overview, find buildings, open one building, plan a field route, PDA totals, repair costs, urgent needs) to answer questions and draft the PDA summary. Every number it reports comes from a tool result, and building ids in its answer are checked against the area.

## Hosted copy

The public copy runs on Render's free plan from `render.yaml` (Docker, redeployed on every push to main). It ships with four finished areas from `demo/` (Cedar Key and Treasure Island after Helene, two in Montego Bay after Melissa): anyone can open them, click buildings and ask questions (30 an hour each). The free plan has a tenth of a CPU, too little to assess a new area in reasonable time, so drawing a new box needs the access code set as `LANDFALL_ACCESS_CODE`. On a machine with a few CPUs a 60-building box takes about 4 minutes. `deploy/huggingface/` holds the same setup for a Hugging Face Space.

## Run the app

```
pip install -e ".[app,data,sam]"
BASETEN_API_KEY=... META_API_KEY=... uvicorn landfall.app:app --port 8000
```

Open http://localhost:8000. Cedar Key opens already assessed: ask about it by typing or with the mic (Muse Voice Transcribe), or click a building to see the photo pair and how its call was made. To assess somewhere new, pick the storm, click **Draw an area**, drag a box over a town and send. The agent finds the NOAA flight, the pre-storm photos and the tide gauges for that box, then runs each stage as a tool and reports as it goes; buildings color in on the map as their drafts arrive. A box of about 30 to 60 buildings takes 3 to 6 minutes. `?at=lat,lon,zoom` opens the map somewhere else.

Reproduce the Cedar Key data (about a minute, plus about 2 minutes of SAM calls):

```
pip install -e ".[data,sam,dev]"
python scripts/prepare_area.py --area cedar_key     # imagery, outlines, alignment
python scripts/review_sheet.py --area cedar_key     # contact sheets to check alignment by eye
META_API_KEY=... python scripts/segment_area.py --area cedar_key   # SAM 3.1 masks and structure loss
python scripts/flood_area.py --area cedar_key      # flood depth: high-water marks + tide gauge minus lidar ground
META_API_KEY=... python scripts/debris_area.py --area cedar_key    # SAM debris piles in the yard, blue tarps on the roof
BASETEN_API_KEY=... META_API_KEY=... python scripts/assess_area.py --area cedar_key   # agent drafts a category per building
python scripts/score_eval.py --area cedar_key      # compare with the hand labels in eval/
BASETEN_API_KEY=... python scripts/ask_area.py --area cedar_key "Which flooded houses might be Major?"
BASETEN_API_KEY=... python scripts/ask_area.py --area cedar_key --pda   # draft PDA summary
```

Evaluation: 50 Cedar Key buildings labeled by hand from the photos by two labelers working blind (`eval/`). The two labelers agree exactly on 43 of 50 (Cohen's kappa 0.49) and within one category on all 50; they split on yard debris (Affected or none) and on two houses with some walls standing (Destroyed or Major). Counting surveyed flood water (0.5 ft or more around a house makes it at least Affected under the PDA rule, which aerial labels can't see), the agent matches 47/50 of Claude's labels and 48/50 of Harry's, and 50/50 within one category for both. Every remaining miss is a building the two labelers disagree on: light yard debris (Claude said Affected, Harry and the agent said none) and two houses with some walls standing (Harry said Major, Claude and the agent said Destroyed). See `eval/cedar_key_score.txt` and the caveats in the label files.

Example output: [`examples/cedar_key_pda_summary.md`](examples/cedar_key_pda_summary.md), the draft PDA summary the area agent wrote for Cedar Key.

Known gaps: SAM only finds heavy debris, so light yard debris is left to the model's eye; flood-only houses are drafted Affected because aerial photos don't show first-floor height.

## Meta models

| Model | Role |
|---|---|
| SAM 3.1 (Meta Model API) | Roof, tarp, debris and water masks on both photo dates |
| Muse Spark Open (Baseten, `meta-models/muse-spark-open`) | Default agent: assigns categories, plans, writes new SAM prompts |
| Muse Spark (Meta Model API) | Second opinion on low-confidence calls |
| Muse Voice Transcribe | Voice questions |

## Run the tests

```
pip install -e ".[dev]"
pytest
```

## Data sources

| Data | Source | Rights |
|---|---|---|
| Post-storm aerial photos | NOAA NGS emergency response imagery | US government work, public domain |
| Before and after satellite photos outside the US | Maxar Open Data program | CC BY-NC 4.0 (non-commercial, attribution) |
| Pre-storm aerial photos | USDA NAIP | US government work, public domain |
| Building outlines | Microsoft Global ML Building Footprints | ODbL |
| Roads | OpenStreetMap | ODbL, © OpenStreetMap contributors |
| High-water marks | USGS Flood Event Viewer | US government work, public domain |
| Ground elevation | USGS 3DEP | US government work, public domain |
| Damage categories | FEMA Preliminary Damage Assessment Guide | Public guidance |
| Building values, year built, units | Florida Department of Revenue statewide cadastral (county property appraisers) | Public record |
| Flood zones and base flood elevations | FEMA National Flood Hazard Layer | US government work, public domain |
| Building values outside Florida | European Commission JRC, Global flood depth-damage functions (Huizinga et al. 2017), national maximum damage values | European Commission reuse policy, with attribution |
| Flood damage bands | FEMA P-784 Substantial Damage Estimator, Appendix E | Public guidance |

## Third-party code and tools

- Python libraries listed in `pyproject.toml`.
- Development tools: Muse Code with the [Superpowers](https://github.com/obra/superpowers) skills (MIT), and Claude Code. These helped write code; they are not part of the product.

## License

MIT
