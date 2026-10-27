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
import re
import threading
from pathlib import Path

from .area import ORDER, TOOLS as AREA_TOOLS, Area, run_tools
from .area import SYSTEM as AREA_SYSTEM
from .pipeline import STAGES, AreaSpec, spec_for
from .storms import STORMS

SYSTEM = (
    AREA_SYSTEM
    + """

You can also assess a new area. When the user gives you a box, work through these tools in order, reading each result before the next:
1. locate_imagery: find NOAA post-storm photos, pre-storm photos and tide gauges for the box. If there is no imagery, stop and say so.
2. prepare_area: download photos and building outlines, line up every building.
3. measure_structures: Segment Anything masks; share of each building missing after the storm.
4. measure_flood: surveyed water surface minus lidar ground at each building.
5. look_for_debris: debris piles in the yard and blue tarps on roofs.
6. draft_categories: the per-building agent drafts a FEMA category for each building, worst-measured first.
Then give a short first report: totals, the worst buildings by id, and what needs a field check. Before each step, say in one short sentence what you are doing and why. If a step reports a problem (few buildings aligned, no flood data), say what it means for the results and carry on."""
)

PIPELINE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "locate_imagery",
            "description": "Find the post-storm NOAA flight, pre-storm NAIP and nearby tide gauges for the box, and set up the area.",
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
    ]
]


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

    # ---- tools ---------------------------------------------------------

    @property
    def tools(self) -> list[dict]:
        return PIPELINE_TOOLS + AREA_TOOLS

    def call(self, name: str, args: dict) -> dict:
        emit = self.on_event
        try:
            if name == "locate_imagery":
                if self.box is None:
                    return {"error": "no box drawn yet; ask the user to draw one on the map"}
                n = max(5, min(int(args.get("max_buildings") or 60), 150))
                spec = AreaSpec.for_box(self.area or _name(self.box), self.box, args.get("storm", "helene"), n)
                spec.save(self.root)
                self.area = spec.name
                emit("area", name=spec.name, bbox=spec.bbox, flight=spec.noaa_flight)
                return {
                    "area": spec.name,
                    "storm": STORMS[spec.storm].name,
                    "post_storm_flight": spec.noaa_flight,
                    "tide_gauges": [g["name"] for g in spec.gauges],
                    "buildings_to_assess": spec.n_buildings,
                }
            stage = {
                "prepare_area": "prepare",
                "measure_structures": "segment",
                "measure_flood": "flood",
                "look_for_debris": "debris",
                "draft_categories": "assess",
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

                STAGES[stage](spec, self.root, log, **kwargs)
                if stage == "prepare":
                    emit("buildings", area=self.area)
                if stage == "assess":
                    return {"done": True, **next((e for e in reversed(events) if e["event"] == "assessed"), {})}
                # stage summaries are what the model needs; per-building chatter is for the UI
                return {"done": True, "summary": [e for e in events if e["event"] not in ("building",)]}
            if not self.area or not (self.root / self.area / "assessments.json").exists():
                return {"error": "no drafts yet for this area; run the assessment first"}
            return Area.load(self.root, self.area).call(name, args)
        except Exception as e:  # report to the model; it can explain or retry
            return {"error": f"{type(e).__name__}: {e}"[:400]}

    # ---- turns ---------------------------------------------------------

    def turn(self, text: str, box: tuple[float, float, float, float] | None = None, storm: str | None = None) -> str:
        with self.lock:
            if box is not None:
                self.box = tuple(round(v, 6) for v in box)
                self.area = None
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
