# Landfall

An agent that turns post-hurricane aerial photos into a draft FEMA Preliminary Damage Assessment: every building compared with its pre-storm photo, flood depth estimated, each one sorted into FEMA's categories with its evidence, and a report a county can work from.

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
- `landfall/fema.py`: FEMA category definitions and the deterministic verifier the agent's calls are checked against.

Reproduce the Cedar Key data (about a minute, plus about 2 minutes of SAM calls):

```
pip install -e ".[data,sam,dev]"
python scripts/prepare_area.py --area cedar_key     # imagery, outlines, alignment
python scripts/review_sheet.py --area cedar_key     # contact sheets to check alignment by eye
META_API_KEY=... python scripts/segment_area.py --area cedar_key   # SAM 3.1 masks and structure loss
BASETEN_API_KEY=... META_API_KEY=... python scripts/assess_area.py --area cedar_key   # agent drafts a category per building
```

Coming next: tarp/debris/water prompts, flood depth, a 50-building hand-labeled eval set, and the area-level agent (plan, ask questions, write the PDA summary).

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
| Pre-storm aerial photos | USDA NAIP | US government work, public domain |
| Building outlines | Microsoft US Building Footprints | ODbL |
| Roads | OpenStreetMap | ODbL, © OpenStreetMap contributors |
| High-water marks | USGS Flood Event Viewer | US government work, public domain |
| Ground elevation | USGS 3DEP | US government work, public domain |
| Damage categories | FEMA Preliminary Damage Assessment Guide | Public guidance |

## Third-party code and tools

- Python libraries listed in `pyproject.toml`.
- Development tools: Muse Code with the [Superpowers](https://github.com/obra/superpowers) skills (MIT), and Claude Code. These helped write code; they are not part of the product.

## License

MIT
