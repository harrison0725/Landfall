# Build log

Building period: Oct 26 to Nov 4, 2026 (confirmed at the kickoff on Oct 26).

## Oct 26 (Mon)

- Created the repository.
- Geometry helpers: lon/lat to Web Mercator pixels, building crop windows with a meter margin.
- Pre/post alignment: edge-map normalized cross-correlation over a ±12 px shift search, with a confidence flag for flat or destroyed crops.
- FEMA categories and the deterministic verifier (no under-calls; at most one step above what the measurements show; unaligned buildings go to human review).
- 18 tests passing.
