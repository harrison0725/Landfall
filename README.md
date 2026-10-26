# Landfall

An agent that turns post-hurricane aerial photos into a draft FEMA Preliminary Damage Assessment: every building compared with its pre-storm photo, flood depth estimated, each one sorted into FEMA's categories with its evidence, and a report a county can work from.

**Built during the Meta Global AI Developer Hackathon, Oct 26 to Nov 4, 2026.** Everything in this repository was written during the event. See [BUILD_LOG.md](BUILD_LOG.md) for a dated record of what was built each day.

> Landfall produces a draft for trained assessors. It is not an official damage determination.

## Status

Day 1. Working so far:

- `landfall/geo.py`: Web Mercator math to turn building outlines into pixel crops of aerial tiles.
- `landfall/align.py`: lines up each building's pre-storm and post-storm crop with an edge-based shift search, and flags crops it can't align with confidence.
- `landfall/fema.py`: FEMA category definitions and the deterministic verifier the agent's calls are checked against.

Coming next: imagery download for Cedar Key (Hurricane Helene), SAM 3.1 segmentation, flood depth, and the agent loop on Muse Spark Open.

## Meta models

| Model | Role |
|---|---|
| SAM 3.1 (Meta Model API) | Roof, tarp, debris and water masks on both photo dates |
| Muse Spark Open (Baseten, `meta-models/muse-spark-open-1026`) | Default agent: assigns categories, plans, writes new SAM prompts |
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
