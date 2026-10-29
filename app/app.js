// Landfall front end: one agent session per tab, streamed over SSE.
"use strict";

const CATS = ["none", "affected", "minor", "major", "destroyed"];
const LABEL = { none: "No visible damage", affected: "Affected", minor: "Minor", major: "Major", destroyed: "Destroyed" };
const STEP = {
  locate_imagery: "Finding before and after imagery",
  prepare_area: "Downloading photos and lining up buildings",
  measure_structures: "Segment Anything: what's left of each building",
  measure_flood: "Flood depth from surveyed high-water marks",
  look_for_debris: "Segment Anything: debris and tarps",
  draft_categories: "Drafting a FEMA category per building",
  area_overview: "Reading area totals",
  find_buildings: "Searching the drafts",
  get_building: "Opening a building",
  plan_inspections: "Planning a field route",
  pda_totals: "Counting PDA categories",
};

const $ = (id) => document.getElementById(id);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const state = { config: {}, sid: null, area: null, box: null, upload: null, busy: false, drawing: false, layer: "after", features: {}, steps: [], spec: null, event: null };
const EVENT = "__upload";  // the Disaster menu's entry for a disaster only the uploaded photos know

// ---------------------------------------------------------------- map
const map = L.map("map", { zoomControl: false }).setView([29.1375, -83.0355], 16);
L.control.zoom({ position: "bottomright" }).addTo(map);
const streets = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 21, maxNativeZoom: 19, zIndex: 0, attribution: "© OpenStreetMap contributors",
});
let after = null;
const outlines = L.geoJSON(null, { style: styleFor, onEachFeature: bindFeature }).addTo(map);
let boxLayer = null;

// One layer for every NOAA flight of the storm. Each map tile waits for all the
// flights that cover it and draws them in one go, the area's own flight last so it
// wins wherever it has photos. Separate layers per flight showed whichever flight
// loaded first, then swapped to another day's photo as the rest arrived.
const NoaaStack = L.GridLayer.extend({
  createTile(coords, done) {
    const tile = document.createElement("canvas");
    const size = this.getTileSize();
    tile.width = size.x;
    tile.height = size.y;
    const tb = this._tileCoordsToBounds(coords);
    const fs = this.options.flights.filter((f) => !f.bounds || L.latLngBounds(f.bounds).intersects(tb));
    let left = fs.length;
    const finish = () => {
      const ctx = tile.getContext("2d");
      imgs.forEach((im) => { if (im.naturalWidth) ctx.drawImage(im, 0, 0, size.x, size.y); });
      done(null, tile);
    };
    const imgs = fs.map((f) => {
      const im = new Image();
      im.onload = im.onerror = () => { if (--left === 0) finish(); };
      im.src = `https://stormscdn.ngs.noaa.gov/${f.name}/${coords.z}/${coords.x}/${coords.y}`;
      return im;
    });
    if (!fs.length) setTimeout(finish);
    return tile;
  },
});
const flightLists = {};
function stormFlights(storm) {
  flightLists[storm] = flightLists[storm] || fetch(`/api/storms/${storm}/flights`).then((r) => (r.ok ? r.json() : [])).catch(() => []);
  return flightLists[storm];
}
let flightToken = 0;
function setFlight(flight, anyFlight = false) {
  layerLabel("After");
  if (after) map.removeLayer(after);
  after = null;
  applyLayer();
  const token = ++flightToken;
  if (!flight && !anyFlight) return;
  // the storm's other flights fill the map around this one: all after the storm, never older photos
  const storm = (state.spec && state.spec.storm) || $("storm").value;
  stormFlights(storm).then((fs) => {
    if (token !== flightToken || !fs.length) return;
    const flights = flight ? fs.filter((f) => f.name !== flight).concat([{ name: flight }]) : fs;
    after = new NoaaStack({
      flights, maxZoom: 21, maxNativeZoom: 20, zIndex: 1,
      attribution: flight ? `NOAA Emergency Response Imagery (${flight}; other flights after it, around it)` : "NOAA Emergency Response Imagery (flights after it)",
    });
    applyLayer();
  });
}
async function setOverlay(area) {
  // storms outside NOAA's coverage: show the after-storm mosaic Landfall built
  flightToken++;
  const r = await fetch(`/api/areas/${area}/after`);
  if (!r.ok) return;
  const o = await r.json();
  if (after) map.removeLayer(after);
  layerLabel("After");
  // shipped areas: map tiles, a wide after-storm layer under a sharp one; drawn areas: their own mosaic
  after = o.tiles
    ? tileGroup(o.tiles)
    : L.imageOverlay(`${o.url}?t=${Date.now()}`, o.bounds, { attribution: o.attribution || "Maxar Open Data (CC BY-NC 4.0)", zIndex: 2 });
  if (o.around) {
    // an uploaded photo: the archive's satellite photo of the place around it, not a street map
    after = L.layerGroup([L.tileLayer(o.around.tiles, { maxZoom: 21, maxNativeZoom: 19, zIndex: 1, attribution: o.around.attribution }), after]);
    layerLabel(`After · before around (${o.around.captured.slice(0, 7)})`);
  }
  applyLayer();
}
function tileGroup(tiles) {
  return L.layerGroup([].concat(tiles).map((t, i) => L.tileLayer(t.url, {
    maxZoom: 21, maxNativeZoom: t.max_native_zoom, minNativeZoom: t.min_zoom, zIndex: i + 1, bounds: t.bounds, attribution: t.attribution,
  })));
}
function setStormTiles(storm) {
  // satellite disasters: the after photos cut for the spot the disaster opens on
  flightToken++;
  layerLabel("After");
  if (after) map.removeLayer(after);
  after = storm && storm.tiles && storm.tiles.length ? tileGroup(storm.tiles) : null;
  applyLayer();
}
function setBeforeTiles(w) {
  // an uploaded photo of a disaster Landfall has no photos of: to find the
  // spot, the archive's newest photo of it from before, clearly labelled so
  flightToken++;
  if (after) map.removeLayer(after);
  after = L.tileLayer(w.tiles, { maxZoom: 21, maxNativeZoom: 19, zIndex: 1, attribution: w.attribution });
  layerLabel(`Before (${w.captured.slice(0, 7)})`);
  applyLayer();
}
function layerLabel(text) {
  document.querySelector('.seg button[data-layer="after"]').textContent = text;
}
function applyLayer() {
  // the street map stays underneath: past the last after-storm photo it shows, never older photos
  streets.addTo(map);
  if (state.layer === "after" && after) after.addTo(map);
  else if (after) map.removeLayer(after);
  outlines.bringToFront();
}
document.querySelectorAll(".seg button").forEach((b) =>
  b.addEventListener("click", () => {
    state.layer = b.dataset.layer;
    document.querySelectorAll(".seg button").forEach((x) => x.classList.toggle("on", x === b));
    applyLayer();
  })
);

function styleFor(f) {
  const p = f.properties;
  const c = p.category ? css("--" + p.category) : "#ffffff";
  return {
    color: p.needs_human ? css("--review") : c,
    weight: p.needs_human ? 2.5 : 1.5,
    dashArray: p.needs_human ? "4 3" : null,
    fillColor: c,
    fillOpacity: p.category ? (p.category === "none" ? 0.15 : 0.55) : 0.05,
  };
}
function bindFeature(f, layer) {
  state.features[f.properties.id] = layer;
  layer.on("click", () => openBuilding(f.properties.id));
  // fetch the card's photos while the pointer is on the building, before the click
  layer.on("mouseover", () => { if (state.area) new Image().src = `/api/areas/${state.area}/pair/${f.properties.id}.jpg`; });
  layer.bindTooltip(() => {
    const p = f.properties;
    return `<b>${p.id}</b> ${p.category ? LABEL[p.category] : "not drafted yet"}${p.needs_human ? " · review" : ""}`;
  }, { sticky: true });
}
async function loadBuildings(fit) {
  if (!state.area) return;
  const r = await fetch(`/api/areas/${state.area}/buildings`);
  if (!r.ok) return;
  const gj = await r.json();
  state.features = {};
  outlines.clearLayers();
  outlines.addData(gj);
  // same framing for every area: all drafted buildings in view, never closer than street level
  if (fit && gj.features.length) map.fitBounds(outlines.getBounds(), { padding: [30, 30], maxZoom: 18 });
}
function paintBuilding(id, props) {
  const layer = state.features[id];
  if (!layer) return;
  Object.assign(layer.feature.properties, props);
  layer.setStyle(styleFor(layer.feature));
}

function legend() {
  $("legend").innerHTML =
    CATS.map((c) => `<span><i style="--c:var(--${c})"></i>${LABEL[c]}</span>`).join("") +
    `<span><i class="dash"></i>Needs a field check</span>`;
}

// ---------------------------------------------------------------- drawing a box
let start = null;
$("draw").addEventListener("click", () => setDrawing(!state.drawing));
function setDrawing(on) {
  state.drawing = on;
  $("draw").classList.toggle("on", on);
  $("draw").textContent = on ? (matchMedia("(pointer: coarse)").matches ? "Drag a finger on the map…" : "Drag on the map…") : "Draw an area";
  map.dragging[on ? "disable" : "enable"]();
  map.touchZoom[on ? "disable" : "enable"]();
  map.getContainer().style.cursor = on ? "crosshair" : "";
  map.getContainer().style.touchAction = on ? "none" : "";  // a finger draws instead of scrolling the page
  if (on) $("context").textContent = "Drag a box over the buildings you want assessed (up to about 60). Click the button again or press Esc to cancel.";
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && state.drawing) { start = null; clearBox(); setDrawing(false); $("context").textContent = ""; }
});
// pointer events, so a mouse, a finger and a pen all draw the same way
const mapEl = map.getContainer();
mapEl.addEventListener("pointerdown", (e) => {
  if (!state.drawing || e.target.closest(".leaflet-control, .map-tools, .legend, .card")) return;
  e.preventDefault();
  mapEl.setPointerCapture(e.pointerId);
  start = map.mouseEventToLatLng(e);
  clearBox();
  boxLayer = L.rectangle([start, start], { color: css("--accent"), weight: 2, fillOpacity: 0.08, interactive: false }).addTo(map);
});
mapEl.addEventListener("pointermove", (e) => {
  if (state.drawing && start) boxLayer.setBounds([start, map.mouseEventToLatLng(e)]);
});
mapEl.addEventListener("pointerup", (e) => {
  if (!state.drawing || !start) return;
  const b = L.latLngBounds(start, map.mouseEventToLatLng(e));
  start = null;
  setDrawing(false);
  const w = b.getWest(), s = b.getSouth(), ea = b.getEast(), n = b.getNorth();
  const km2 = ((ea - w) * 111.32 * Math.cos(((s + n) / 2) * Math.PI / 180)) * ((n - s) * 110.57);
  if (km2 < 0.002) { clearBox(); $("context").textContent = "That box was too small. Drag across a few houses."; return; }
  state.box = [w, s, ea, n];
  if (state.upload) {
    // the photos go where the box is: show the after photo there to check the fit
    if (photoPreview) map.removeLayer(photoPreview);
    photoPreview = L.imageOverlay(state.upload.url, b, { opacity: 0.85, interactive: false }).addTo(map);
    $("context").innerHTML = `Your photos will be laid over this box, north up. <button type="button" class="linkish" id="clear-box">Draw again</button>`;
    $("clear-box").addEventListener("click", () => { clearBox(); setDrawing(true); });
    if (!$("input").value.trim()) $("input").value = "Assess the damage in my photos.";
    $("input").focus();
    return;
  }
  const cap = Math.min(60, state.config.max_buildings || 60);  // the agent's default, capped by this server
  $("context").innerHTML = `Box drawn: ${km2 < 0.1 ? Math.round(km2 * 1e6).toLocaleString() + " m²" : km2.toFixed(2) + " km²"}${
    cap ? `. I'll assess up to ${cap} buildings in it, nearest the middle first` : ""}. <button type="button" class="linkish" id="clear-box">Clear box</button>`;
  $("clear-box").addEventListener("click", () => { clearBox(); $("context").textContent = ""; });
  if (!$("input").value.trim()) $("input").value = "Assess the damage in this area.";
  $("input").focus();
});
function clearBox() {
  if (boxLayer) map.removeLayer(boxLayer);
  if (photoPreview) map.removeLayer(photoPreview);
  boxLayer = photoPreview = null;
  state.box = null;
}

// ---------------------------------------------------------------- uploading photos
// The user's own photos of the land (a drone or plane shot) instead of NOAA or
// Maxar: an after photo, a before photo if they have one, then a box over where
// they were taken, and the same assessment runs on them.
let photoPreview = null;
$("upload").addEventListener("click", () => {
  $("upload-form").hidden = !$("upload-form").hidden;
});
$("upload-cancel").addEventListener("click", () => { $("upload-form").hidden = true; });
// where and when: the map goes there, and the place and year pick the photo of
// the spot from before (and the disaster, when it's one Landfall has)
$("upload-year").max = new Date().getFullYear();
$("upload-date").max = new Date().toISOString().slice(0, 10);
$("upload-date").addEventListener("change", () => { if ($("upload-date").value) $("upload-year").value = $("upload-date").value.slice(0, 4); });
$("upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const after = $("upload-after").files[0];
  const before = $("upload-before").files[0];
  const status = $("upload-status");
  if (!after) { status.textContent = "Pick an after photo first."; return; }
  const where = $("upload-place").value.trim();
  const yr = $("upload-year").value;
  const day = $("upload-date").value;
  const kind = $("upload-kind").value;
  if (!where) { status.textContent = "Say where the photos were taken."; return; }
  if (!/^\d{4}$/.test(yr) || +yr > new Date().getFullYear()) { status.textContent = "Give the year it happened."; return; }
  if (day && day.slice(0, 4) !== yr) { status.textContent = "The day and the year don't match."; return; }
  status.textContent = `Finding ${where}…`;
  const r = await fetch(`/api/places?q=${encodeURIComponent(where)}`).catch(() => ({ ok: false }));
  const place = r.ok ? (await r.json())[0] : null;
  if (!place) { status.textContent = r.ok ? `Couldn't find ${where}. Try adding the state or country.` : "Place search isn't answering; try again in a moment."; return; }
  // a disaster Landfall already has here that year: use it (its photos, dates and flood marks)
  const known = (state.storms || []).find((s) => place.disasters.includes(s.key) && s.landfall.startsWith(yr) && (s.kind || "hurricane") === kind && (!day || Math.abs(new Date(day) - new Date(s.landfall)) < 21 * 864e5));
  const event = known ? null : { kind, date: day || yr, name: $("upload-name").value.trim() };
  status.textContent = "Uploading…";
  const put = (file, q) => fetch(`/api/uploads?${q}`, { method: "POST", body: file }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  });
  try {
    const a = await put(after, "side=after");
    if (before) await put(before, `side=before&id=${a.id}`);
    state.upload = { id: a.id, url: URL.createObjectURL(after), before: !!before, event, gps: a.gps };
  } catch (err) {
    status.textContent = `Couldn't upload: ${err.message}`;
    return;
  }
  status.textContent = "";
  $("upload-form").hidden = true;
  $("upload").textContent = before ? "Photos ready" : "Photo ready";
  $("upload").classList.add("on");
  const [w, s, ea, n] = place.bbox;
  map.fitBounds([[s, w], [n, ea]], { maxZoom: 17 });
  $("place").value = place.short || place.label;
  if (known && state.toStorm) state.toStorm(known.key);
  else {
    state.menus.showEvent(event.name || `${yr} ${kind}`, yr);
    // no disaster photos here: show the spot as it was before, to find where the photo was taken
    const at = `lon=${place.lon}&lat=${place.lat}&date=${day || yr + "-01-01"}`;
    fetch(`/api/wayback?${at}`).then((r) => (r.ok ? r.json() : null)).then((w) => { if (w && state.upload) setBeforeTiles(w); }).catch(() => {});
  }
  const what = known ? `${known.name} (${yr})` : event.name || `the ${yr} ${kind}`;
  // the agent finds where in the town it was taken, then assesses it: nothing to draw.
  // The photo's own GPS or a street address narrow the search to the blocks around it.
  const near = (lat, lon, km) => {
    const dy = km / 2 / 111.32, dx = dy / Math.cos((lat * Math.PI) / 180);
    return [lon - dx, lat - dy, lon + dx, lat + dy];
  };
  const spanKm = (b) => Math.max((b[2] - b[0]) * 111.32 * Math.cos((place.lat * Math.PI) / 180), (b[3] - b[1]) * 111.32);
  const gps = state.upload.gps;
  state.box = gps ? near(gps[0], gps[1], 1.2) : place.address || spanKm(place.bbox) < 2 ? near(place.lat, place.lon, 2) : place.bbox;
  if (gps || place.address) map.fitBounds([[state.box[1], state.box[0]], [state.box[3], state.box[2]]]);
  state.findUpload = true;
  $("input").value = `Find where my ${before ? "photos were" : "photo was"} taken in ${place.short || place.label} and assess the buildings in ${before ? "them" : "it"}`;
  send();
});

// ---------------------------------------------------------------- conversation
function scroll() { const l = $("log"); l.scrollTop = l.scrollHeight; }
function add(el) { $("log").appendChild(el); scroll(); return el; }
function div(cls, html) { const d = document.createElement("div"); d.className = cls; if (html != null) d.innerHTML = html; return d; }
function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]); }

function linkIds(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const hits = [];
  while (walker.nextNode()) if (/\bb\d{3}\b/.test(walker.currentNode.nodeValue)) hits.push(walker.currentNode);
  for (const node of hits) {
    const frag = document.createDocumentFragment();
    node.nodeValue.split(/(\bb\d{3}\b)/).forEach((part) => {
      if (/^b\d{3}$/.test(part) && state.features[part]) {
        const b = document.createElement("button");
        b.type = "button"; b.className = "bid"; b.textContent = part;
        b.addEventListener("click", () => openBuilding(part));
        frag.appendChild(b);
      } else frag.appendChild(document.createTextNode(part));
    });
    node.replaceWith(frag);
  }
}
function agentMessage(text) {
  const el = div("msg agent");
  el.innerHTML = window.marked ? marked.parse(text || "") : esc(text);
  linkIds(el);
  return add(el);
}
function suggestions(list) {
  const box = div("suggest");
  list.forEach((t) => {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = t;
    b.addEventListener("click", () => { $("input").value = t; send(); });
    box.appendChild(b);
  });
  add(box);
}

let current = null; // the step card for the running tool
function startStep(name, args) {
  const d = document.createElement("details");
  d.className = "step running";
  const extra = args && Object.keys(args).length ? esc(JSON.stringify(args)) : "";
  d.innerHTML = `<summary><span class="dot"></span><span class="name">${esc(STEP[name] || name)}</span><span class="meta">${extra}</span></summary><div class="lines"></div>`;
  d.dataset.tool = name;
  if (name === "draft_categories") {
    d.open = true;
    d.insertAdjacentHTML("beforeend", `<div class="drafts"></div>`);
  }
  current = { el: d, t0: performance.now() };
  return add(d);
}
function stepLine(text) {
  if (!current) return;
  current.el.querySelector(".lines").appendChild(div("", esc(text)));
}
function endStep(result) {
  if (!current) return;
  const failed = result && result.error;
  current.el.classList.remove("running");
  current.el.classList.add(failed ? "failed" : "done");
  const secs = ((performance.now() - current.t0) / 1000).toFixed(0);
  current.el.querySelector(".meta").textContent = `${secs}s`;
  if (failed) { stepLine(result.error); current.el.open = true; }  // show why without a click
  current = null;
}

function describeProgress(ev) {
  switch (ev.event) {
    case "buildings": return state.upload ? `${ev.found} buildings in the photo; assessing ${ev.kept}` : `${ev.found} footprints in the box; assessing ${ev.kept}`;
    case "photos": return `Photos: ${ev.note}`;
    case "detect": return `Looking for buildings in the photo: ${ev.tiles} Segment Anything calls`;
    case "imagery": return `${ev.side === "post" ? "After" : "Before"}: ${ev.source}`;
    case "aligned": return `Lined up ${ev.matched} by their own roof, ${ev.from_neighbors} from neighbors, ${ev.unaligned} couldn't be lined up`;
    case "sam": return `${ev.calls} Segment Anything calls: ${ev.prompts.join(", ")}`;
    case "structure_loss": return `Measured ${ev.measured} of ${ev.of}. Mostly gone: ${ev.over_60.join(", ") || "none"}. Partly gone: ${ev.over_25.join(", ") || "none"}`;
    case "flood_source": return ev.error ? ev.error : `${ev.source}: ${ev.elev_ft} ft NAVD88`;
    case "flood": return ev.error ? ev.error : `Water surface ${ev.surface_ft} ft; water reached ${ev.flooded} of ${ev.of} buildings, up to ${ev.max_ft} ft deep`;
    case "debris": return `Debris piles: ${ev.debris.join(", ") || "none found"}. Tarps: ${ev.tarps.join(", ") || "none found"}`;
    case "assess_plan": return `${ev.buildings} buildings, worst-measured first: ${ev.first.join(", ")}…`;
    case "assessed": return `Done: ${Object.entries(ev.counts).map(([k, v]) => `${v} ${k}`).join(", ")}; ${ev.review} for a field check`;
    case "building_failed": return `${ev.id} failed: ${ev.error}`;
    case "warning": return `Note: ${ev.text}`;
    default: return null;
  }
}

function handle(ev) {
  switch (ev.kind) {
    case "thought":
      add(div("thought", esc(ev.text)));
      break;
    case "tool":
      startStep(ev.name, ev.args);
      break;
    case "progress": {
      if (ev.event === "building") {
        const grid = current && current.el.querySelector(".drafts");
        if (grid) {
          const c = document.createElement("button");
          c.type = "button";
          c.className = `chip ${ev.category}${ev.needs_human ? " review" : ""}`;
          c.style.setProperty("--c", `var(--${ev.category})`);
          c.textContent = ev.id;
          c.title = `${LABEL[ev.category]} (${ev.confidence})${ev.revisions ? `, revised ${ev.revisions}×` : ""}: ${ev.evidence}`;
          c.addEventListener("click", () => openBuilding(ev.id));
          grid.appendChild(c);
          scroll();
        }
        paintBuilding(ev.id, { category: ev.category, needs_human: ev.needs_human, confidence: ev.confidence, evidence: ev.evidence });
      } else {
        const line = describeProgress(ev);
        if (line) stepLine(line);
      }
      break;
    }
    case "tool_result":
      if (ev.name === "get_building" && ev.result && ev.result.id) focusBuilding(ev.result.id);
      endStep(ev.result);
      break;
    case "area":
      state.area = ev.name;
      state.flight = ev.flight;
      setFlight(ev.flight);
      break;
    case "buildings":
      state.area = ev.area;
      // the new area joins the Area menu, picked, named after the place searched
      if (ev.area && ![...$("area").options].some((o) => o.value === ev.area)) {
        areaLabels[ev.area] = areaLabels[ev.area] || ($("place").value.trim() || areaLabel(ev.area));
        $("area").insertAdjacentHTML("beforeend", `<option value="${esc(ev.area)}">${esc(areaLabels[ev.area])}</option>`);
      }
      if (ev.area) $("area").value = ev.area;
      if (state.upload) {
        // found and laid out: the photos are used; if not found they stay, to drag a box over the spot
        state.upload = null;
        $("upload").textContent = "Upload photos";
        $("upload").classList.remove("on");
      }
      loadBuildings(true);
      if (!state.flight) setOverlay(ev.area);
      break;
    case "message":
      if (ev.area && ev.area !== state.area) { state.area = ev.area; }
      loadBuildings(false).then(() => agentMessage(ev.text));
      break;
    case "error":
      add(div("msg error", esc(ev.text)));
      break;
    case "idle":
      setBusy(false);
      break;
  }
}

function setBusy(b) {
  state.busy = b;
  $("send").disabled = b;
  $("send").textContent = b ? "Working…" : "Send";
}

async function send(e) {
  if (e) e.preventDefault();
  const text = $("input").value.trim();
  if (!text || state.busy) return;
  if (state.box && !state.upload && (!$("storm").value || $("storm").value === EVENT)) {
    add(div("msg error", "Which disaster is this? Search for it or pick it from the Disaster menu, then press Send again."));
    return;
  }
  const msg = div("msg user");
  msg.textContent = text;
  if (state.box) msg.insertAdjacentHTML("beforeend", `<span class="boxnote">${state.findUpload ? "+ your photo" : "+ the box on the map"}</span>`);
  add(msg);
  $("input").value = "";
  $("context").textContent = "";
  setBusy(true);
  let code = null;
  if (state.box && state.config.access_code_required) {
    code = stored("landfall-code") || (await askCode());
    if (!code) {
      add(div("msg error", "No problem. Without the code I can't assess a new area here, but you can pick a finished area from the Area menu and ask me about it."));
      setBusy(false);
      return;
    }
  }
  const storm = $("storm").value === EVENT ? null : $("storm").value || null;
  const body = { text, box: state.box, storm: state.box ? storm : null, access_code: code, upload: state.box && state.upload ? state.upload.id : null, event: state.box && state.upload ? state.upload.event : null };
  if (state.box) { outlines.clearLayers(); state.features = {}; }
  if (state.box && state.upload && !state.findUpload) {
    // the photos are used once; the map shows the area's own copy when it's ready
    state.upload = null;
    $("upload").textContent = "Upload photos";
    $("upload").classList.remove("on");
  }
  state.box = null;
  state.findUpload = false;
  $("context").textContent = "";
  const r = await fetch(`/api/sessions/${state.sid}/turn`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) {
    if (r.status === 403) store("landfall-code", null);
    add(div("msg error", esc((await r.json()).detail || r.statusText)));
    setBusy(false);
  } else if (code) store("landfall-code", code);
}
function askCode() {
  // asked in the chat, not a browser pop-up; resolves with the code, or null if they skip it
  return new Promise((resolve) => {
    const box = add(div("msg agent codeask", `<p>Assessing a new area runs the AI models, which costs money, so this public copy needs an access code.</p>
      <form class="coderow"><input type="password" autocomplete="off" placeholder="Access code" aria-label="Access code"><button class="send" type="submit">Assess</button><button class="linkish" type="button">Skip</button></form>`));
    const form = box.querySelector("form");
    const done = (v) => { form.remove(); resolve(v); };
    form.addEventListener("submit", (e) => { e.preventDefault(); const v = form.querySelector("input").value.trim(); if (v) done(v); });
    form.querySelector(".linkish").addEventListener("click", () => done(null));
    form.querySelector("input").focus();
  });
}
function stored(k) { try { return localStorage.getItem(k); } catch { return null; } }
function store(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* private window */ } }
$("compose").addEventListener("submit", send);
$("input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) send(e); });

// ---------------------------------------------------------------- building card
function focusBuilding(id) {
  const layer = state.features[id];
  if (layer) map.fitBounds(layer.getBounds(), { maxZoom: 19, padding: [120, 120] });
}
async function openBuilding(id) {
  focusBuilding(id);
  const layer = state.features[id];
  const p = layer ? layer.feature.properties : { id };
  const r = await fetch(`/api/areas/${state.area}/buildings/${id}`);
  const a = r.ok ? await r.json() : null;
  // A drawn area lives on the server's disk, which the free host wipes when
  // it restarts (an update, a long idle, running out of memory).
  const gone = !r.ok && !(await fetch("/api/areas").then((x) => x.json()).catch(() => [])).some((x) => x.name === state.area);
  const cat = a ? a.category : p.category;
  const trace = (a && a.trace) || [];
  // every area gets the same rows in the same order; what we can't know there says so
  const us = !(state.spec && state.spec.imagery === "maxar");
  const need = a && a.need;
  const cost = a && a.cost;
  const rows = [
    ["Confidence", a && a.confidence != null ? a.confidence.toFixed(2) : "–"],
    ["Decided by", a ? modelName(a.decided_by) : "–"],
    ["Structure missing", a && a.structure_loss != null ? `${Math.round(a.structure_loss * 100)}%` : "not measured"],
    ["Debris in yard", a && a.debris_share ? `${Math.round(a.debris_share * 100)}%` : "none found"],
    ["Water above ground", a && a.flood_ft != null ? `${a.flood_ft.toFixed(1)} ft` : us ? "none surveyed here" : "unknown (no flood survey)"],
    ["First floor", a && a.record && a.record.basis ? a.record.basis : us ? "unknown" : "unknown (no property records)"],
    ["Water inside", !us ? "unknown" : need && need.water_inside_ft[1] > 0 ? `${need.water_inside_ft[0].toFixed(1)}–${need.water_inside_ft[1].toFixed(1)} ft (estimate)` : "none"],
    ["Households", need ? (need.households ? `${need.households}${need.likely_displaced ? ", likely displaced" : need.possibly_displaced ? ", possibly displaced" : ""}` : "none (not a home)") : "–"],
    ["Repair cost", cost ? (cost.cost_high_usd ? `${usd(cost.cost_low_usd)}–${usd(cost.cost_high_usd)} of ${usd(cost.value_usd)}` : `none seen (value ${usd(cost.value_usd)})`) : "–"],
    ["50% rule", !us ? "US rule, not used here" : cost ? (cost.substantial_damage_likely ? "likely substantially damaged" : cost.substantial_damage_possible ? "possibly substantially damaged" : "below 50%") : "–"],
    ...(a && a.demolished ? [["Note", "gone before the disaster (demolished)"]] : []),
  ];
  $("card-body").innerHTML = `
    <h2>${esc(id)} ${cat ? `<span class="pill ${cat}" style="--c:var(--${cat})">${LABEL[cat]}</span>` : ""}${a && a.needs_human ? `<span class="pill" style="--c:var(--review)">Field check</span>` : ""}</h2>
    <img src="/api/areas/${state.area}/pair/${id}.jpg" alt="Before and after photos of ${esc(id)}">
    <div class="cap"><span>Before${a && a.photos ? ": " + esc(shortSource(a.photos.before)) : ""}</span><span>After${a && a.photos ? ": " + esc(shortSource(a.photos.after)) : ""}</span></div>
    <p>${a ? esc(a.evidence) : gone ? "This area is no longer on the server. The server restarted after it was drawn, and drawn areas are lost when that happens. Draw the box again to redo it." : "Not drafted yet. The assessment of this area is still running, or it stopped before it reached this building; the chat shows which step it is on."}</p>
    ${a ? `<dl class="kv">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("")}</dl>` : ""}
    ${a ? `<p><a class="packet" href="/api/areas/${state.area}/packet/${id}" target="_blank" rel="noopener">Evidence packet for a claim (printable)</a></p>` : ""}
    ${trace.length ? `<div class="trace"><b>How the call was made</b>${trace.map(traceRow).join("")}</div>` : ""}`;
  $("card").hidden = false;
}
function modelName(m) {
  const id = String(m || "").split("/").pop();
  if (/spark-open/i.test(id)) return "Muse Spark Open";
  if (/muse-spark/i.test(id)) return "Muse Spark";
  return id || "–";
}
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function shortSource(s) {
  // "Maxar WV02 2025-10-31 (1030..., 0.49 m)" -> "Maxar, Oct 2025"; "USDA NAIP, 2023-02-15" -> "NAIP, Feb 2023"
  const d = String(s).match(/(\d{4})-(\d{2})-\d{2}/);
  const who = /maxar/i.test(s) ? "Maxar" : /naip/i.test(s) ? "NAIP" : /noaa/i.test(s) ? "NOAA" : String(s).split(" (")[0];
  return d ? `${who}, ${MONTHS[+d[2] - 1]} ${d[1]}` : who;
}
function usd(x) {
  if (x == null) return "?";
  if (x === 0) return "$0";
  return x >= 1e6 ? `$${(x / 1e6).toFixed(1)}M` : `$${Math.round(x / 1e3)}k`;
}
function traceRow(t) {
  if (t.model === "satellite rule") return `<div class="t">Satellite photos: damage calls go to a field check.</div>`;
  if (t.model === "flood rule") return `<div class="t">Flood rule raised ${esc(t.raised_from)} to ${esc(t.to)}.</div>`;
  if (t.error) return `<div class="t">${esc(modelName(t.model))}: unreadable answer, asked again.</div>`;
  const a = t.answer || {};
  const obj = (t.verifier || []).map((o) => `<div class="obj">Checker: ${esc(o)}</div>`).join("");
  return `<div class="t"><div><b>${esc(modelName(t.model))}</b> said ${esc(String(a.category || "").toLowerCase())} (${a.confidence})</div>${obj || "<div>Checker: no objection.</div>"}</div>`;
}
$("card-close").addEventListener("click", () => { $("card").hidden = true; });

// ---------------------------------------------------------------- voice
let rec = null;
$("mic").addEventListener("click", async () => {
  if (rec) { rec.stop(); return; }
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
  catch { $("context").textContent = "Microphone not available."; return; }
  const chunks = [];
  rec = new MediaRecorder(stream);
  rec.ondataavailable = (e) => chunks.push(e.data);
  rec.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    rec = null;
    $("mic").classList.remove("rec");
    $("context").textContent = "Transcribing with Muse Voice Transcribe…";
    try {
      const wav = await toWav16k(new Blob(chunks));
      const r = await fetch("/api/transcribe", { method: "POST", headers: { "content-type": "audio/wav", "x-sample-rate": "16000" }, body: wav });
      const { text } = await r.json();
      $("context").textContent = "";
      if (text) { $("input").value = text; send(); }
      else $("context").textContent = "Didn't catch that.";
    } catch (err) { $("context").textContent = `Transcription failed: ${err}`; }
  };
  rec.start();
  $("mic").classList.add("rec");
  $("context").textContent = "Listening… click the mic again to stop.";
});
async function toWav16k(blob) {
  const raw = await new AudioContext().decodeAudioData(await blob.arrayBuffer());
  const off = new OfflineAudioContext(1, Math.ceil(raw.duration * 16000), 16000);
  const src = off.createBufferSource();
  src.buffer = raw; src.connect(off.destination); src.start();
  const pcm = (await off.startRendering()).getChannelData(0);
  const buf = new ArrayBuffer(44 + pcm.length * 2), v = new DataView(buf);
  const str = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF"); v.setUint32(4, 36 + pcm.length * 2, true); str(8, "WAVEfmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, 16000, true); v.setUint32(28, 32000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  str(36, "data"); v.setUint32(40, pcm.length * 2, true);
  pcm.forEach((x, i) => v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, x)) * 0x7fff, true));
  return new Blob([buf], { type: "audio/wav" });
}

// ---------------------------------------------------------------- start
// area names: the place, "Cedar Key, Florida", once the server has looked it up
const areaLabels = {};
function areaLabel(name) { return areaLabels[name] || (/^upload_/.test(name) ? "Your uploaded photos" : title(name)); }
async function boot() {
  legend();
  // the three lookups don't depend on each other: ask for them together
  const [storms, config, areas] = await Promise.all(["/api/storms", "/api/config", "/api/areas"].map((u) => fetch(u).then((r) => r.json())));
  const year = (s) => s.landfall.slice(0, 4);
  const group = (label, rows) => (rows.length ? `<optgroup label="${label}">${rows.map((s) => `<option value="${s.key}">${esc(s.name)} (${esc(s.where)}, ${year(s)})</option>`).join("")}</optgroup>` : "");
  // the Year menu narrows the Disaster menu to that year's disasters
  const years = [...new Set(storms.map(year))].sort().reverse();
  $("year").innerHTML = `<option value="">All years</option>` + years.map((y) => `<option>${y}</option>`).join("");
  const params = new URLSearchParams(location.search);
  if (years.includes(params.get("year"))) $("year").value = params.get("year");
  const withYear = (q) => q + ($("year").value ? `&year=${$("year").value}` : "");
  function stormMenu() {
    const y = $("year").value;
    const shown = storms.filter((s) => !y || year(s) === y);
    const hurricanes = shown.filter((s) => (s.kind || "hurricane") === "hurricane");
    // nothing picked yet, or a disaster Landfall has no photos of: say so rather than show another one
    const ev = state.event;
    $("storm").innerHTML =
      `<option value="">Search or pick a disaster…</option>` +
      (ev && (!y || ev.year === y) ? `<option value="${EVENT}">${esc(ev.name)} (your photos, ${ev.year})</option>` : "") +
      group("Hurricanes, United States: NOAA aerial photos", hurricanes.filter((s) => s.imagery === "noaa")) +
      group("Hurricanes worldwide: Maxar satellite photos", hurricanes.filter((s) => s.imagery === "maxar")) +
      [["wildfire", "Wildfires"], ["earthquake", "Earthquakes"], ["flood", "Floods"]].map(([k, label]) => group(label, shown.filter((s) => s.kind === k))).join("");
  }
  function showStorm(key) {
    // the Year menu follows the disaster on the map, so every menu says the same thing
    const s = storms.find((x) => x.key === key);
    if (!s) return showUnknown();
    state.event = null;
    $("year").value = year(s);
    stormMenu();
    $("storm").value = key;
  }
  function showEvent(name, y) {
    // a disaster the uploader described: on the menu for as long as it's on the map
    state.event = { name: name.charAt(0).toUpperCase() + name.slice(1), year: y };
    if (![...$("year").options].some((o) => o.value === y)) $("year").insertAdjacentHTML("beforeend", `<option>${esc(y)}</option>`);
    $("year").value = y;
    stormMenu();
    $("storm").value = EVENT;
  }
  function showUnknown() {
    state.event = null;
    $("year").value = "";
    stormMenu();
    $("storm").value = "";
  }
  state.menus = { showStorm, showEvent, showUnknown };
  stormMenu();
  // the upload form ties photos to a disaster on the menu when one matches
  state.storms = storms;
  state.toStorm = (key) => {
    showStorm(key);
    const s = storms.find((x) => x.key === key);
    if (s.imagery === "noaa") setFlight(null, true);
    else setStormTiles(s);
  };
  state.config = config;
  const at = params.get("at");
  const fit = params.get("fit");
  const pick = at || fit ? null : areas.find((a) => a.name === params.get("area")) || areas.find((a) => a.name === "cedar_key" && a.assessed) || null;
  const session = fetch("/api/sessions", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ area: pick && pick.assessed ? pick.name : null }) }).then((r) => r.json());
  state.area = pick && pick.assessed ? pick.name : null;
  const done = areas.filter((a) => a.assessed);
  // the shipped examples keep their plain names; areas people drew or
  // uploaded say so, numbered when one town has several
  const mine = (a) => /^(box|upload)_/.test(a.name);
  const seen = {};
  done.forEach((a) => {
    const base = a.label || title(a.name);
    if (!mine(a)) {
      areaLabels[a.name] = done.some((b) => b !== a && !mine(b) && (b.label || title(b.name)) === base) ? title(a.name) : base;
      return;
    }
    const kind = a.name.startsWith("upload_") ? "uploaded photos" : "drawn area";
    const k = `${base}|${kind}`;
    seen[k] = (seen[k] || 0) + 1;
    areaLabels[a.name] = `${base}, ${kind}${seen[k] > 1 ? ` ${seen[k]}` : ""}`;
  });
  const opts = (rows) => rows.map((a) => `<option value="${esc(a.name)}">${esc(areaLabel(a.name))}</option>`).join("");
  const ours = done.filter(mine);
  $("area").innerHTML = `<option value="">Pick one…</option><optgroup label="Examples">${opts(done.filter((a) => !mine(a)))}</optgroup>`
    + (ours.length ? `<optgroup label="Added by people">${opts(ours)}</optgroup>` : "");
  if (pick) $("area").value = pick.name;
  $("area").addEventListener("change", () => { if ($("area").value) location.search = withYear(`?area=${encodeURIComponent($("area").value)}`); });
  // picking a disaster goes there: its finished example area, else where its photos are
  const KIND_WORDS = {
    hurricane: "hurricane cyclone typhoon storm tropical wind",
    wildfire: "wildfire fire fires burn burned",
    earthquake: "earthquake quake quakes seismic",
    flood: "flood floods flooding flash river rain",
  };
  function matchDisasters(q) {
    const words = q.toLowerCase().split(/[\s,]+/).filter(Boolean);
    const text = (s) => `${s.name} ${s.where} ${year(s)} ${KIND_WORDS[s.kind || "hurricane"] || ""}`.toLowerCase();
    // every word must appear; "floods" also finds "flood"
    const has = (t, w) => t.includes(w) || (w.length > 4 && w.endsWith("s") && t.includes(w.slice(0, -1)));
    return storms.filter((s) => words.every((w) => has(text(s), w))).sort((a, b) => b.landfall.localeCompare(a.landfall));
  }
  function goToStorm(key) {
    const own = done.filter((a) => a.spec && a.spec.storm === key);
    const example = own.find((a) => !/^(box|upload)_/.test(a.name)) || own[0];
    if (example) { location.search = withYear(`?area=${encodeURIComponent(example.name)}`); return; }
    const s = storms.find((x) => x.key === key);
    if (s && s.view && s.view.length) location.search = withYear(`?at=${s.view.join(",")}&storm=${encodeURIComponent(key)}`);
  }
  $("storm").addEventListener("change", () => { if ($("storm").value && $("storm").value !== EVENT) goToStorm($("storm").value); });
  $("year").addEventListener("change", () => {
    stormMenu();
    const first = $("storm").querySelector(`option:not([value=""]):not([value="${EVENT}"])`);
    if ($("year").value && first) goToStorm(first.value);  // the year's newest disaster
  });
  // place search: go there, with the disaster that has photos of it
  $("place-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = $("place").value.trim();
    const list = $("place-results");
    if (!q) return;
    list.hidden = false;
    // disasters by name, kind, place or year ("Helene", "earthquake", "Spain flood", "2024"),
    // shown at once; places follow when the place search answers
    const hits = matchDisasters(q);
    const name = (key) => (storms.find((x) => x.key === key) || {}).name;
    const render = (rows, tail) => {
      list.innerHTML =
        hits.map((s) => `<li tabindex="0" data-storm="${esc(s.key)}">${esc(s.name)}<small>Disaster · ${esc(s.where)}, ${year(s)}</small></li>`).join("") +
        rows
          .map((p, i) => `<li tabindex="0" data-i="${i}">${esc(p.short || p.label)}<small>${p.disasters.length ? esc(p.disasters.map(name).join(", ")) : "no disaster photos here yet"}</small></li>`)
          .join("") +
        (tail ? `<li class="note">${tail}</li>` : "");
      list.querySelectorAll("li[tabindex]").forEach((li) => {
        const go = () => {
          if (li.dataset.storm) {
            $("year").value = "";  // the disaster's own year is set when the map opens
            goToStorm(li.dataset.storm);
            return;
          }
          const p = rows[+li.dataset.i];
          // the selected year's disaster if it has one here, else the newest
          const y = $("year").value;
          const key = p.disasters.find((k) => !y || year(storms.find((x) => x.key === k)) === y) || p.disasters[0];
          location.search = withYear(`?fit=${p.bbox.map((v) => v.toFixed(4)).join(",")}&place=${encodeURIComponent(p.short || q)}` + (key ? `&storm=${encodeURIComponent(key)}` : ""));
        };
        li.addEventListener("click", go);
        li.addEventListener("keydown", (ev) => { if (ev.key === "Enter") go(); });
      });
    };
    render([], "Searching places…");
    const r = await fetch(`/api/places?q=${encodeURIComponent(q)}`).catch(() => ({ ok: false }));
    if ($("place").value.trim() !== q) return;  // a newer search took over
    // the same town can come back twice (the town and its boundary): keep one
    let rows = (r.ok ? await r.json() : []).filter((p, i, all) => all.findIndex((o) => (o.short || o.label) === (p.short || p.label)) === i);
    // "earthquake" also names places somewhere; with disasters found, keep only places that have photos
    if (hits.length) rows = rows.filter((p) => p.disasters.length);
    if (!rows.length && !hits.length) { render([], r.ok ? "No place or disaster by that name." : "Search isn't answering; try again in a moment."); return; }
    render(rows, !r.ok && hits.length ? "Place search isn't answering right now." : "");
    if (rows.length + hits.length === 1) list.querySelector("li[tabindex]").click();
  });
  document.addEventListener("click", (e) => { if (!$("place-form").contains(e.target)) $("place-results").hidden = true; });
  if (pick) {
    state.spec = pick.spec;
    if (pick.spec && !pick.spec.storm && pick.spec.event_name) showEvent(pick.spec.event_name, pick.spec.landfall.slice(0, 4));
    else if (pick.spec) showStorm(pick.spec.storm);
    state.flight = pick.spec && pick.spec.noaa_flight;
    setFlight(state.flight);
    if (!state.flight) setOverlay(pick.name);  // start the photo tiles before the outlines
    await loadBuildings(true);
  } else {
    applyLayer();
  }
  const key = params.get("storm");
  if (at) {
    const [la, lo, z] = at.split(",").map(Number);
    map.setView([la, lo], z || 17);
  }
  if (fit) {
    const [w, s, e, n] = fit.split(",").map(Number);
    map.fitBounds([[s, w], [n, e]], { maxZoom: 16 });
    $("place").value = params.get("place") || "";
  }
  if ((at || fit) && key && storms.some((x) => x.key === key)) {
    showStorm(key);
    const s = storms.find((x) => x.key === key);
    if (s.imagery === "noaa") setFlight(null, true);  // every after-storm flight
    else setStormTiles(s);  // the after photos cut for this disaster
  } else if (at || fit) showUnknown();  // a place no disaster here has photos of
  state.sid = (await session).id;
  const ev = new EventSource(`/api/sessions/${state.sid}/events`);
  ev.onmessage = (m) => handle(JSON.parse(m.data));
  if (pick && pick.assessed) {
    agentMessage(introFor(pick));
    suggestions(["Which buildings should a field team check first?", "Write the draft damage summary", "How many households may be out of their homes?"]);
  } else if (fit) {
    const place = params.get("place") || "this place";
    const s = storms.find((x) => x.key === key);
    agentMessage(
      s
        ? `Here's ${place}, with photos from after ${/^(Hurricane|Cyclone|Typhoon)/.test(s.name) ? "" : "the "}${s.name} (${year(s)}). Zoom in and draw a box around the buildings you want assessed.`
        : `Here's ${place}. None of the disasters Landfall covers has photos here yet, so there's nothing to compare. Try another place, or pick a disaster from the menu.`
    );
  } else {
    agentMessage("Search for a place or pick a disaster, then draw an area on the map, and I'll draft FEMA damage categories for every building in it.");
  }
}
boot();
function introFor(pick) {
  // one format for every area: what was drafted, from which photos, and the tally
  const feats = Object.values(state.features).map((l) => l.feature.properties);
  const n = (c) => feats.filter((p) => p.category === c).length;
  const opt = $("storm").selectedOptions[0];
  // a disaster the uploader described isn't on the menu: use their name for it
  const named = pick.spec && !pick.spec.storm && pick.spec.event_name;
  const text = named || (opt ? opt.text : "");
  // "after Hurricane Helene", but "after the Myanmar earthquake"
  const storm = text ? (/^(Hurricane|Cyclone|Typhoon|the )/.test(text) ? "" : "the ") + text : "";
  const kind = pick.spec && pick.spec.imagery;
  const photos = kind === "maxar" ? "before and after satellite photos"
    : kind === "photos" ? (pick.archive_before ? "your uploaded photo and an archive photo of the spot from before" : pick.uploaded_before ? "your uploaded before and after photos" : "your uploaded photo")
    : "before and after aerial photos";
  const tally = ["destroyed", "major", "minor", "affected", "none"].map((c) => `${n(c)} ${LABEL[c].toLowerCase()}`).join(", ");
  const check = feats.filter((p) => p.needs_human).length;
  return `I've drafted damage categories for **${feats.length} buildings in ${areaLabel(pick.name)}** after ${storm.split(" (")[0] || "the disaster"}, from ${photos}: ${tally}. ${check} need a field check.\n\nAsk me about it, or draw a new area on the map and I'll assess that too.`;
}
function title(name) { return name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()); }
