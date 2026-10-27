"""One conversation with Landfall about one area, from the drawn box to the
last follow-up question.

The agent runs the pipeline itself: it gets the stages as tools, decides
the order and how many buildings to draft, and reads each stage's summary
before the next. Once drafts exist, the area tools from area.py answer
questions about them. Everything it does is reported through `on_event`,
which the web app streams to the browser.
"""

from __future__ import annotations

import json
import traceback
import re
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from .area import ORDER, TOOLS as AREA_TOOLS, Area, run_tools
from .area import SYSTEM as AREA_SYSTEM
from .pipeline import STAGES, AreaSpec, spec_for
from .storms import STORMS

SYSTEM = (
    AREA_SYSTEM
    + """

You can also assess a new area. When the user gives you a box, work through these tools in order, reading each result before the next:
1. locate_imagery: find post-storm photos (NOAA aerial flights in the US, Maxar satellite imagery elsewhere), pre-storm photos and tide gauges for the box. If there is no imagery, stop and say so.
2. prepare_area: download photos and building outlines, line up every building.
3. measure_structures: Segment Anything masks; share of each building missing after the storm.
4. measure_flood: surveyed water surface minus lidar ground at each building.
5. look_for_debris: debris piles in the yard and blue tarps on roofs.
6. draft_categories: the per-building agent drafts a FEMA category for each building, worst-measured first.
7. estimate_costs_and_needs: repair cost ranges from local tax-roll values, FEMA's 50% substantial-damage flags, and households likely displaced.
Then give a short first report: totals, repair cost range, households likely displaced, the most urgent buildings by id, and what needs a field check. Before each step, say in one short sentence what you are doing and why. If a step reports a problem (few buildings aligned, no flood data), say what it means for the results and carry on.
If a tool fails with an error, try it once more; if it fails again, stop and tell the user in two or three plain sentences that the assessment couldn't run because of a problem on the server, that nothing was drafted, and that they can still ask about the finished areas in the Area menu. Don't quote the error or list the steps you would have run. Don't name a parish, county or town for a box unless a tool result names it."""
)

PIPELINE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "locate_imagery",
            "description": "Find post-storm and pre-storm photos for the box (NOAA flights and USDA NAIP in the US; Maxar satellite imagery for storms elsewhere) and nearby tide gauges, and set up the area.",
            "parameters": {
                "type": "object",
                "properties": {
                    "storm": {"type": "string", "enum": list(STORMS)},
                    "max_buildings": {"type": "integer", "description": "buildings to assess, nearest the box center first (default 60, at most 150)"},
                },
                "required": ["storm"],
            },
        },
    },
] + [
    {"type": "function", "function": {"name": name, "description": desc, "parameters": {"type": "object", "properties": props}}}
    for name, desc, props in [
        ("prepare_area", "Download pre/post photos and building outlines and line up each building between the two flights.", {}),
        ("measure_structures", "Run Segment Anything on every building before and after; returns which buildings lost most of their structure.", {}),
        ("measure_flood", "Water depth above ground at each building from USGS high-water marks, NOAA tide gauges and 3DEP lidar.", {}),
        ("look_for_debris", "Run Segment Anything on each yard for debris piles and on each roof for blue tarps.", {}),
        (
            "draft_categories",
            "Draft a FEMA category for each building: Muse Spark Open looks at the photo pair and the measurements, a checker objects to calls the numbers don't support, unsure calls go to Muse Spark.",
            {"limit": {"type": "integer", "description": "only the N worst-measured buildings (default all)"}},
        ),
        (
            "estimate_costs_and_needs",
            "Repair cost range per building and for the area (local building values from the Florida tax roll, FEMA flood-damage bands, first-floor height estimated from year built and FEMA flood zone), the 50% substantial-damage flags, and households likely displaced.",
            {},
        ),
    ]
]


PIPELINE_NAMES = {t["function"]["name"] for t in PIPELINE_TOOLS}


class Session:
    """Holds the chat history and the area; one turn at a time."""

    def __init__(self, root: Path, area: str | None = None, use_spark: bool = False):
        self.root = root
        self.area = area
        self.box: tuple[float, float, float, float] | None = None
        self.use_spark = use_spark
        self.messages: list[dict] = [{"role": "system", "content": SYSTEM}]
        self.lock = threading.Lock()
        self.on_event = lambda kind, **f: None
        # A hosted copy turns this off for visitors without the access code:
        # the pipeline spends API credits, questions about finished areas don't.
        self.may_run_pipeline = True
        self.max_buildings = 150
        self.pool = ThreadPoolExecutor(2)
        self.ahead: dict[str, Future] = {}

    # ---- tools ---------------------------------------------------------

    @property
    def tools(self) -> list[dict]:
        return PIPELINE_TOOLS + AREA_TOOLS

    def call(self, name: str, args: dict) -> dict:
        emit = self.on_event
        if not self.may_run_pipeline and name in PIPELINE_NAMES:
            return {
                "error": "This public copy only answers questions about areas already assessed. Assessing a new area needs the access code "
                "(it spends API credits). Tell the user, and suggest one of the finished areas."
            }
        try:
            if name == "locate_imagery":
                if self.box is None:
                    return {"error": "no box drawn yet; ask the user to draw one on the map"}
                n = max(5, min(int(args.get("max_buildings") or 60), self.max_buildings))
                spec = AreaSpec.for_box(self.area or _name(self.box), self.box, args.get("storm", "helene"), n, cache=self.root / "cache")
                spec.save(self.root)
                self.area = spec.name
                emit("area", name=spec.name, bbox=spec.bbox, flight=spec.noaa_flight or None)
                return {
                    "area": spec.name,
                    "storm": STORMS[spec.storm].name,
                    "post_storm_photos": spec.post_source,
                    "pre_storm_photos": spec.pre_label or "USDA NAIP (newest before the storm)",
                    "tide_gauges": [g["name"] for g in spec.gauges],
                    **(
                        {"note": "Outside the US: satellite photos from Maxar Open Data; no surveyed flood depth or property values here, so flood and repair cost will be unknown."}
                        if spec.imagery == "maxar"
                        else {}
                    ),
                    "buildings_to_assess": spec.n_buildings,
                }
            stage = {
                "prepare_area": "prepare",
                "measure_structures": "segment",
                "measure_flood": "flood",
                "look_for_debris": "debris",
                "draft_categories": "assess",
                "estimate_costs_and_needs": "costs",
            }.get(name)
            if stage:
                if not self.area:
                    return {"error": "no area yet: call locate_imagery first"}
                spec = spec_for(self.root, self.area)
                kwargs = {"limit": int(args["limit"])} if stage == "assess" and args.get("limit") else {}
                events: list[dict] = []

                def log(kind: str, **fields) -> None:
                    events.append({"event": kind, **fields})
                    emit("progress", stage=stage, event=kind, **fields)

                ahead = self.ahead.pop(stage, None)
                if ahead is not None:
                    # Started early, alongside measure_structures: wait for it
                    # and replay its progress as if it ran now.
                    done, early = ahead.result()
                    for e in early:
                        log(**e)
                    if isinstance(done, Exception):
                        raise done
                else:
                    if stage == "segment":
                        self._start_ahead(spec)
                    STAGES[stage](spec, self.root, log, **kwargs)
                if stage == "prepare":
                    emit("buildings", area=self.area)
                if stage == "assess":
                    return {"done": True, **next((e for e in reversed(events) if e["event"] == "assessed"), {})}
                if stage == "costs":
                    emit("buildings", area=self.area)
                # stage summaries are what the model needs; per-building chatter is for the UI
                return {"done": True, "summary": [e for e in events if e["event"] not in ("building",)]}
            if not self.area or not (self.root / self.area / "assessments.json").exists():
                return {"error": "no drafts yet for this area; run the assessment first"}
            return Area.load(self.root, self.area).call(name, args)
        except Exception as e:  # report to the model; it can explain or retry
            traceback.print_exc()  # and to the server log, since the model is told not to quote it
            emit("progress", stage=name, event="error", message=f"{type(e).__name__}: {e}"[:200])
            return {"error": f"{type(e).__name__}: {e}"[:400]}

    def _start_ahead(self, spec: AreaSpec) -> None:
        """Flood and debris need only the prepared area, not the structure
        masks, so run them while measure_structures does. Most of each is
        waiting on Meta's and USGS's APIs, so they overlap almost entirely."""

        def run(stage: str):
            early: list[dict] = []
            try:
                STAGES[stage](spec, self.root, lambda kind, **f: early.append({"kind": kind, **f}))
                return None, early
            except Exception as e:  # raised again when the tool is called
                return e, early

        for stage in ("debris", "flood"):
            if stage not in self.ahead:
                self.ahead[stage] = self.pool.submit(run, stage)

    # ---- turns ---------------------------------------------------------

    def turn(self, text: str, box: tuple[float, float, float, float] | None = None, storm: str | None = None) -> str:
        with self.lock:
            if box is not None:
                self.box = tuple(round(v, 6) for v in box)
                self.area = None
                self.ahead = {}
                w, s, e, n = self.box
                text = (
                    f"{text}\n\n[The user drew a box on the map: west {w}, south {s}, east {e}, north {n}"
                    + (f"; storm: {storm}" if storm else "")
                    + ".]"
                )
            elif self.area:
                text = f"{text}\n\n[Current area: {self.area}.]"
            self.messages.append({"role": "user", "content": text})
            a = run_tools(self.messages, self.tools, self.call, use_spark=self.use_spark, max_steps=20, on_event=self.on_event)
            return a.text


def _name(box: tuple[float, float, float, float]) -> str:
    w, s, e, n = box
    return re.sub(r"[^0-9a-z_]", "", f"box_{(s + n) / 2:.4f}_{-(w + e) / 2:.4f}".replace(".", "_"))


def area_geojson(root: Path, area: str) -> dict:
    """Outlines with their drafts, for the map."""
    d = root / area
    feats = json.loads((d / "buildings.geojson").read_text())["features"]
    drafts = {}
    if (d / "assessments.json").exists():
        drafts = {a["id"]: a for a in json.loads((d / "assessments.json").read_text())}
    floods = {}
    if (d / "flood.json").exists():
        floods = {b["id"]: b["flood_ft"] for b in json.loads((d / "flood.json").read_text()).get("buildings", [])}
    shift = {}
    if (d / "alignment.json").exists():
        from .geo import lonlat_to_pixel, pixel_to_lonlat

        al = json.loads((d / "alignment.json").read_text())
        zoom = al["summary"]["zoom"]
        shift = {r["id"]: (r["dx"], r["dy"]) for r in al["buildings"] if r["status"] != "unaligned"}

        def moved(geom: dict, dx: int, dy: int) -> dict:
            # Footprints trace the pre-storm photos; draw them where the
            # alignment found the house in the post-storm flight.
            ring = []
            for lon, lat in geom["coordinates"][0]:
                x, y = lonlat_to_pixel(lon, lat, zoom)
                ring.append(list(pixel_to_lonlat(x + dx, y + dy, zoom)))
            return {"type": "Polygon", "coordinates": [ring]}

    out = []
    for f in feats:
        bid = f["properties"]["id"]
        a = drafts.get(bid, {})
        geom = moved(f["geometry"], *shift[bid]) if bid in shift else f["geometry"]
        out.append(
            {
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "id": bid,
                    "category": a.get("category"),
                    "confidence": a.get("confidence"),
                    "needs_human": a.get("needs_human"),
                    "evidence": a.get("evidence"),
                    "decided_by": a.get("decided_by"),
                    "flood_ft": floods.get(bid),
                    "structure_loss": a.get("structure_loss"),
                    "severity": ORDER.index(a["category"]) if a.get("category") in ORDER else None,
                },
            }
        )
    return {"type": "FeatureCollection", "features": out}
