// Landfall front end: one agent session per tab, streamed over SSE.
"use strict";

const CATS = ["none", "affected", "minor", "major", "destroyed"];
// plain words for FEMA's five damage categories (none, affected, minor, major, destroyed)
const ORDER = ["none", "affected", "minor", "major", "destroyed"];
const LABEL = { none: "No damage seen", affected: "Slightly damaged", minor: "Damaged", major: "Badly damaged", destroyed: "Destroyed" };
const FEMA = { none: "No visible damage", affected: "Affected", minor: "Minor", major: "Major", destroyed: "Destroyed" };
const STEP = {
  locate_imagery: "Finding photos from before and after",
  prepare_area: "Getting the photos and lining up each building",
  measure_structures: "Measuring how much of each building is left",
  measure_flood: "Checking how deep the water got",
  look_for_debris: "Looking for debris and roof tarps",
  draft_categories: "Judging the damage to each building",
  estimate_costs_and_needs: "Estimating repair costs and who lost their home",
  area_overview: "Adding up the area",
  find_buildings: "Looking through the buildings",
  get_building: "Opening a building",
  plan_inspections: "Planning a route to visit buildings",
  pda_totals: "Adding up the damage",
  repair_costs: "Adding up repair costs",
  urgent_needs: "Finding who needs help first",
  find_help: "Muse Spark is searching the web for where to get help",
};

const $ = (id) => document.getElementById(id);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const state = { config: {}, sid: null, area: null, box: null, upload: null, busy: false, drawing: false, layer: "after", features: {}, steps: [], spec: null, event: null };
const EVENT = "__upload";  // the Disaster menu's entry for a disaster only the uploaded photos know

// ---------------------------------------------------------------- map
const map = L.map("map", { zoomControl: false }).setView([29.1375, -83.0355], 16);
// keep the tiles already on screen while zooming and panning, instead of loading
// (and dropping) every in-between level, which flashed blank squares
L.GridLayer.mergeOptions({ updateWhenZooming: false, keepBuffer: 4 });
L.control.zoom({ position: "bottomright" }).addTo(map);
const streets = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 21, maxNativeZoom: 19, zIndex: 0, attribution: "© OpenStreetMap contributors",
});
let after = null;
// the before photo sits in its own pane, above the after photo and below the outlines,
// clipped to the left of the slider
map.createPane("before").style.zIndex = 410;
map.createPane("outlines").style.zIndex = 420;
const outlines = L.geoJSON(null, { style: styleFor, onEachFeature: bindFeature, pane: "outlines" }).addTo(map);
let boxLayer = null;

// One layer for every NOAA flight of the storm. Each map tile waits for all the
// flights that cover it and draws them in one go, the area's own flight last so it
// wins wherever it has photos. Separate layers per flight showed whichever flight
// loaded first, then swapped to another day's photo as the rest arrived.
// A flight's tile with no photo is a 1-pixel image; every tile inside it is empty
// too, so those are never asked for again (most flights cover a narrow strip).
const noaaEmpty = new Set();
function noaaKnownEmpty(name, z, x, y) {
  for (let up = 0; up <= 6 && z - up >= 0; up++) {
    if (noaaEmpty.has(`${name}/${z - up}/${x >> up}/${y >> up}`)) return true;
  }
  return false;
}
const NoaaStack = L.GridLayer.extend({
  createTile(coords, done) {
    const tile = document.createElement("canvas");
    const size = this.getTileSize();
    tile.width = size.x;
    tile.height = size.y;
    const tb = this._tileCoordsToBounds(coords);
    const fs = this.options.flights.filter((f) => (!f.bounds || L.latLngBounds(f.bounds).intersects(tb)) && !noaaKnownEmpty(f.name, coords.z, coords.x, coords.y));
    let left = fs.length;
    let shown = false;
    const draw = () => {
      const ctx = tile.getContext("2d");
      ctx.clearRect(0, 0, size.x, size.y);
      imgs.forEach((im) => { if (im.complete && im.naturalWidth > 1) ctx.drawImage(im, 0, 0, size.x, size.y); });
    };
    const show = () => { if (!shown) { shown = true; done(null, tile); } };
    const imgs = fs.map((f) => {
      const im = new Image();
      im.onload = im.onerror = () => {
        if (im.naturalWidth === 1) noaaEmpty.add(`${f.name}/${coords.z}/${coords.x}/${coords.y}`);
        draw();
        left--;
        // show the tile once all its flights are in, or soon after the first photo
        // arrives, so one slow flight doesn't hold up the whole map
        if (left === 0) show();
        else if (im.naturalWidth > 1) setTimeout(show, 800);
      };
      im.src = `https://stormscdn.ngs.noaa.gov/${f.name}/${coords.z}/${coords.x}/${coords.y}`;
      return im;
    });
    if (!fs.length) setTimeout(show);
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
  layerLabel("Photo after");
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
  layerLabel("Photo after");
  // shipped areas: map tiles, a wide after-storm layer under a sharp one; drawn areas: their own mosaic
  after = o.tiles
    ? tileGroup(o.tiles)
    : L.imageOverlay(`${o.url}?t=${Date.now()}`, o.bounds, { attribution: o.attribution || "Maxar Open Data (CC BY-NC 4.0)", zIndex: 2 });
  if (o.around) {
    // an uploaded photo: the archive's satellite photo of the place around it, not a street map
    after = L.layerGroup([L.tileLayer(o.around.tiles, { maxZoom: 21, maxNativeZoom: 19, zIndex: 1, attribution: o.around.attribution }), after]);
    layerLabel(`Photo after · older photo around it (${o.around.captured.slice(0, 7)})`);
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
  layerLabel("Photo after");
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
  layerLabel(`Photo before (${w.captured.slice(0, 7)})`);
  applyLayer();
}
function layerLabel(text) {
  document.querySelector('.seg button[data-layer="after"]').textContent = text;
}
function applyLayer() {
  // under the after-storm photos: a satellite photo of the places they don't
  // reach (dated in the corner), or the street map when that's what's picked
  const photos = state.layer !== "map";
  if (photos && aroundLayer) { aroundLayer.addTo(map); map.removeLayer(streets); }
  else { streets.addTo(map); if (aroundLayer) map.removeLayer(aroundLayer); }
  $("around-note").hidden = !(photos && aroundLayer && aroundNote);
  if (photos && after) after.addTo(map);
  else if (after) map.removeLayer(after);
  showCompare(state.layer === "compare");
  outlines.bringToFront();
  if (photos) loadAround();
}

// ---------------------------------------------------------------- satellite photo beyond the flights
// Esri's newest satellite photo, for the places the after-storm aerial photos
// don't cover (Gainesville after Helene, say). Its date is looked up for the
// spot in view and shown, saying whether it was taken after the disaster.
let aroundLayer = null, aroundNote = "", aroundKey = "";
function disasterDate() {
  const s = (state.storms || []).find((x) => x.key === $("storm").value);
  return s ? s.landfall.slice(0, 10) : "";
}
async function loadAround() {
  const c = map.getCenter(), date = disasterDate();
  const key = `${c.lng.toFixed(2)},${c.lat.toFixed(2)},${date}`;
  if (key === aroundKey) return;
  aroundKey = key;
  const r = await fetch(`/api/around?lon=${c.lng.toFixed(2)}&lat=${c.lat.toFixed(2)}&date=${date || "1900-01-01"}`).catch(() => null);
  if (key !== aroundKey) return;  // the map moved on while asking
  if (!r || !r.ok) { aroundNote = ""; $("around-note").hidden = true; if (!aroundLayer) aroundKey = ""; return; }
  const a = await r.json();
  if (!aroundLayer || aroundLayer._url !== a.tiles) {
    const old = aroundLayer;
    aroundLayer = L.tileLayer(a.tiles, { maxZoom: 21, maxNativeZoom: 19, zIndex: 0, attribution: a.attribution });
    if (old) map.removeLayer(old);
  }
  const month = new Date(a.captured + "T00:00:00Z").toLocaleDateString(undefined, { month: "short", year: "numeric", timeZone: "UTC" });
  aroundNote = `Where the storm photos end: satellite photo from ${month}` + (date ? (a.after ? ", after the disaster" : ", from before the disaster") : "");
  $("around-note").textContent = aroundNote;
  applyLayer();
}
map.on("moveend", () => { if (state.layer !== "map") loadAround(); });

// ---------------------------------------------------------------- before | after slider
// The before photo the drafts were made from, on the same grid as the after one;
// left of the handle is before, right is after.
const swipe = L.DomUtil.create("div", "swipe", map.getContainer());
swipe.hidden = true;
swipe.innerHTML = `<div class="swipe-line"></div><span class="swipe-tag left"></span><span class="swipe-tag right">After</span><input type="range" min="0" max="100" value="50" aria-label="Move to compare the photos before and after">`;
L.DomEvent.disableClickPropagation(swipe);
const swipeRange = swipe.querySelector("input");
let before = null;  // {area, info, layer}
async function checkBefore() {
  const btn = document.querySelector('.seg button[data-layer="compare"]');
  const area = state.area;
  if (before && before.area === area) { btn.hidden = !before.info; return; }
  if (before && before.layer) map.removeLayer(before.layer);
  before = { area, info: null, layer: null };
  const r = area ? await fetch(`/api/areas/${area}/before`).catch(() => null) : null;
  if (!r || !r.ok || state.area !== area) { btn.hidden = true; if (state.layer === "compare") document.querySelector('.seg button[data-layer="after"]').click(); return; }
  before.info = await r.json();
  btn.hidden = false;
  if (state.layer === "compare") showCompare(true);
}
function showCompare(on) {
  if (on && before && before.info) {
    if (!before.layer) before.layer = L.imageOverlay(before.info.url, before.info.bounds, { pane: "before", interactive: false });
    before.layer.addTo(map);
    const src = before.info.source.replace(/\s*\d{4}-\d{2}-\d{2}$/, "").replace(/^USDA /, "");
    const d = before.info.date ? new Date(before.info.date + "T00:00:00Z") : null;
    swipe.querySelector(".left").textContent = `Before · ${src}${d ? `, ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}` : ""}`;
    swipe.hidden = false;
    clipBefore();
  } else {
    if (before && before.layer) map.removeLayer(before.layer);
    swipe.hidden = true;
  }
}
function clipBefore() {
  if (swipe.hidden) return;
  const size = map.getSize();
  const x = (swipeRange.value / 100) * size.x;
  const nw = map.containerPointToLayerPoint([0, 0]);
  const se = map.containerPointToLayerPoint(size);
  const cx = map.containerPointToLayerPoint([x, 0]).x;
  map.getPane("before").style.clip = `rect(${nw.y}px, ${cx}px, ${se.y}px, ${nw.x}px)`;
  swipe.querySelector(".swipe-line").style.left = `${x}px`;
}
swipeRange.addEventListener("input", clipBefore);
map.on("move zoom resize zoomend", clipBefore);
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
    weight: p.needs_human ? 3 : 2.5,
    opacity: 1,
    dashArray: p.needs_human ? "4 3" : null,
    // outline only, so the roof itself shows through; the click still lands inside
    fillColor: c,
    fillOpacity: 0,
  };
}
function bindFeature(f, layer) {
  state.features[f.properties.id] = layer;
  layer.on("click", () => openBuilding(f.properties.id));
  // fetch the card's photos while the pointer is on the building, before the click
  layer.on("mouseover", () => {
    if (state.area) new Image().src = `/api/areas/${state.area}/pair/${f.properties.id}.jpg`;
    layer.setStyle({ fillOpacity: 0.25 });  // a light tint shows which building the pointer is on
  });
  layer.on("mouseout", () => layer.setStyle({ fillOpacity: 0 }));
  layer.bindTooltip(() => {
    const p = f.properties;
    return `<b>Building ${p.id}</b> ${p.category ? LABEL[p.category].toLowerCase() : "not checked yet"}${p.needs_human ? " · needs a visit" : ""}`;
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
  if (state.groups) { if (state.groups.area === state.area) colorGroups(); else clearGroups(); }
  // same framing for every area: all drafted buildings in view, never closer than street level
  if (fit && gj.features.length) map.fitBounds(outlines.getBounds(), { padding: [30, 30], maxZoom: 18 });
  checkBefore();
}
function paintBuilding(id, props) {
  const layer = state.features[id];
  if (!layer) return;
  Object.assign(layer.feature.properties, props);
  layer.setStyle(styleFor(layer.feature));
}

// what each level means, in everyday words (from FEMA's damage levels)
const MEANS = {
  none: "Nothing wrong can be seen from the air.",
  affected: "Small damage, like a few lost shingles or water in the yard. People can usually still live there.",
  minor: "Damage that can be repaired, like part of the roof torn off. It may not be safe to live in until it's fixed.",
  major: "Serious damage to the roof or walls. It needs big repairs before anyone can live there.",
  destroyed: "The building is gone or can't be repaired.",
};

function legend() {
  $("legend").innerHTML =
    CATS.map((c) => `<span><i style="--c:var(--${c})"></i>${LABEL[c]}</span>`).join("") +
    `<span><i class="dash"></i>Needs a visit to confirm</span>` +
    `<button type="button" class="linkish" id="levels-open">What do these mean?</button>`;
  $("levels-open").addEventListener("click", () => {
    $("levels").hidden = false;
    $("levels-close").focus();
  });
}
$("levels-list").innerHTML = CATS.map((c) => `<div class="level"><img src="/static/img/levels/${c}.webp" alt="" width="96" height="96" loading="lazy"><div><b><i style="--c:var(--${c})"></i>${LABEL[c]}</b><p>${MEANS[c]}</p></div></div>`).join("");
$("levels-close").addEventListener("click", () => { $("levels").hidden = true; });

// ---------------------------------------------------------------- 1-mile grid
// In the US, the square-mile sections of the Public Land Survey: the same
// 1-mile grid printed on USGS topo maps and county maps, fetched in fixed
// 0.1-degree cells so the lines never move as the map zooms. Where there is
// no land survey (Texas, the original colonies, other countries), square-mile
// cells anchored to fixed lines of latitude and longitude instead.
const MILE_M = 1609.344;
const SEC_CELL = 0.1;
const gridLayer = L.layerGroup();
const gridScale = L.control.scale({ position: "bottomright", metric: false, maxWidth: 320 });
const secCells = new Map();  // "i_j" -> Promise of section features (empty where no survey)
let gridOn = false;
let gridDraw = 0;
let gridFeats = [];  // the land-survey sections on screen, for picking one
try { gridOn = localStorage.getItem("landfall-grid") === "1"; } catch (e) { /* storage blocked */ }
// a light line under a dark dashed one reads on dark photos and light street maps alike
const GRID_STYLES = [{ color: "#fff", weight: 4, opacity: 0.75, fill: false, interactive: false }, { color: "#111", weight: 2, opacity: 0.9, dashArray: "8 6", fill: false, interactive: false }];
function sectionCell(i, j) {
  const key = `${i}_${j}`;
  if (!secCells.has(key)) {
    secCells.set(key, fetch(`/api/sections?i=${i}&j=${j}`).then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d) => d.features || [])
      .catch(() => { secCells.delete(key); return null; }));  // try again on the next move
  }
  return secCells.get(key);
}
function fixedGrid(b) {
  // the east-west spacing comes from a whole degree of latitude, not the map's
  // centre, so the lines stay in the same place at every zoom
  const dLat = MILE_M / 111320;
  const dLon = MILE_M / (111320 * Math.cos((Math.round(b.getCenter().lat) * Math.PI) / 180));
  const lat0 = Math.floor(b.getSouth() / dLat), lat1 = Math.ceil(b.getNorth() / dLat);
  const lon0 = Math.floor(b.getWest() / dLon), lon1 = Math.ceil(b.getEast() / dLon);
  if ((lat1 - lat0) + (lon1 - lon0) > 400) return;  // zoomed too far out to read
  const line = (pts) => GRID_STYLES.forEach((st) => gridLayer.addLayer(L.polyline(pts, st)));
  for (let i = lat0; i <= lat1; i++) line([[i * dLat, lon0 * dLon], [i * dLat, lon1 * dLon]]);
  for (let j = lon0; j <= lon1; j++) line([[lat0 * dLat, j * dLon], [lat1 * dLat, j * dLon]]);
}
async function drawGrid() {
  const token = ++gridDraw;
  if (!gridOn) { gridLayer.clearLayers(); return; }
  const b = map.getBounds();
  const is = [Math.floor(b.getWest() / SEC_CELL), Math.ceil(b.getEast() / SEC_CELL)];
  const js = [Math.floor(b.getSouth() / SEC_CELL), Math.ceil(b.getNorth() / SEC_CELL)];
  const want = [];
  if ((is[1] - is[0]) * (js[1] - js[0]) <= 36) {
    for (let i = is[0]; i < is[1]; i++) for (let j = js[0]; j < js[1]; j++) want.push(sectionCell(i, j));
  }
  if (!want.length) { gridLayer.clearLayers(); return; }  // too far out: the lines would be a blur
  const got = await Promise.all(want);
  if (token !== gridDraw) return;  // the map moved again while loading
  gridLayer.clearLayers();
  const seen = new Set();
  const feats = got.flat().filter((f) => f && !seen.has(f.properties.id) && seen.add(f.properties.id));
  gridFeats = feats;
  if (!feats.length) {
    // no land survey here: the fixed square-mile grid
    if (!got.some((g) => g === null)) fixedGrid(b);
    return;
  }
  const labels = map.getZoom() >= 14;
  for (const f of feats) {
    GRID_STYLES.forEach((st) => gridLayer.addLayer(L.geoJSON(f, { style: () => st, interactive: false })));
    if (labels && f.properties.label) {
      const c = L.geoJSON(f).getBounds().getCenter();
      if (b.contains(c)) gridLayer.addLayer(L.tooltip({ permanent: true, direction: "center", className: "sec-label", interactive: false }).setLatLng(c).setContent(f.properties.label));
    }
  }
}
function setGrid(on) {
  gridOn = on;
  $("grid").classList.toggle("on", on);
  $("grid").setAttribute("aria-pressed", String(on));
  if (on) { gridLayer.addTo(map); gridScale.addTo(map); } else { gridLayer.remove(); gridScale.remove(); }
  // close in, a whole screen fits inside one square mile and no line shows:
  // step back far enough to see a few cells
  if (on && userTurned) {
    // enough room for about three cells across, on a phone as on a desktop
    const fit = map.getBoundsZoom(map.getCenter().toBounds(3 * MILE_M));
    if (map.getZoom() > fit) map.setZoom(fit);
  }
  try { localStorage.setItem("landfall-grid", on ? "1" : "0"); } catch (e) { /* storage blocked */ }
  drawGrid();
}
let userTurned = false;
$("grid").addEventListener("click", () => { userTurned = true; setGrid(!gridOn); });
map.on("moveend", drawGrid);
setGrid(gridOn);

// ---------------------------------------------------------------- picking a grid square, grouping its buildings
// With the grid on, a click on the map (not on a building) picks the square
// under it; "Group the buildings here" asks the agent to group the buildings
// in it by age, how they were built and the green around them.
const cellLayer = L.layerGroup().addTo(map);
function inRing(pt, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > pt[1]) !== (yj > pt[1]) && pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
function squareAt(ll) {
  const pt = [ll.lng, ll.lat];
  for (const f of gridFeats) {
    const g = f.geometry;
    const polys = g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : [];
    for (const poly of polys) if (inRing(pt, poly[0]) && !poly.slice(1).some((h) => inRing(pt, h))) return { label: f.properties.label || "this square", polygon: poly[0] };
  }
  if (gridFeats.length) return null;
  // no land survey here: the fixed square-mile cell under the click
  const dLat = MILE_M / 111320;
  const dLon = MILE_M / (111320 * Math.cos((Math.round(map.getCenter().lat) * Math.PI) / 180));
  const i = Math.floor(ll.lat / dLat), j = Math.floor(ll.lng / dLon);
  const [s, n, w, e] = [i * dLat, (i + 1) * dLat, j * dLon, (j + 1) * dLon];
  return { label: `the square mile at ${((s + n) / 2).toFixed(4)}, ${((w + e) / 2).toFixed(4)}`, polygon: [[w, s], [e, s], [e, n], [w, n], [w, s]] };
}
map.on("click", (e) => {
  if (!gridOn || state.drawing) return;
  const t = e.originalEvent && e.originalEvent.target;
  if (t && t.closest && t.closest(".leaflet-interactive, .leaflet-popup, .leaflet-control")) return;
  const sq = squareAt(e.latlng);
  cellLayer.clearLayers();
  if (!sq) return;
  state.cell = { ...sq, pending: false };
  cellLayer.addLayer(L.polygon(sq.polygon.map(([x, y]) => [y, x]), { color: "#ff9f1c", weight: 4, fill: true, fillOpacity: 0.06, interactive: false }));
  const box = document.createElement("div");
  box.className = "cellpick";
  box.innerHTML = `<b>${esc(sq.label)}</b><button type="button" class="send">Group buildings and estimate damage</button><small>Groups every building here by age, how it was built and the green around it, and gives each group its chance of damage from past disasters with buildings like them. No photos needed.</small>`;
  box.querySelector("button").addEventListener("click", () => {
    map.closePopup();
    if (state.busy) return;
    state.cell.pending = true;
    const kind = ((state.storms || []).find((x) => x.key === $("storm").value) || {}).kind || "hurricane";
    $("input").value = `Group the buildings in ${sq.label} by age, how they were built and the green around them, and estimate each group's chance of damage in a ${kind} from past disasters with similar buildings.`;
    send();
  });
  // kept clear of the panel on the right, which would cover it
  L.popup({ closeButton: true, autoPan: true, autoPanPaddingTopLeft: [20, 160], autoPanPaddingBottomRight: [$("groups").hidden ? 20 : 330, 20] }).setLatLng(e.latlng).setContent(box).openOn(map);
});

const GROUP_COLORS = ["#4cc9f0", "#f72585", "#b5e48c", "#ffd166", "#9b5de5", "#06d6a0", "#ff8c42", "#f1f1f1"];
const FACTOR_NAME = { chance: "Chance of damage", severe: "Chance of bad damage", loss: "Repair cost (% of value)", flooded: "Chance of water inside", all: "All three", age: "Age (building rules)", construction: "How it was built", green: "Green around it" };
// Each square estimated stays on the map, split into 16 blocks of about a
// quarter mile, each shaded by its buildings' average chance of damage, so
// blocks and squares can be compared at a glance. The panel lists every
// square side by side; each building's dot is one click away.
const squaresLayer = L.layerGroup().addTo(map);
const SQ = {};  // square label -> {ev, layer, dots, stats}
let sqShown = null;  // the square last picked
let sqColorBy = "chance";
// the number a block or dot is shaded by when coloring by odds
const METRIC = { chance: "chance_damaged", severe: "chance_severe", loss: "repair", flooded: "flooded" };
const isMetric = (by) => by in METRIC;
const metricOf = (p) => p[METRIC[sqColorBy] || "chance_damaged"];
// "blocks": each quarter-mile block shaded as a whole; "buildings": every
// building in every square as its own dot, block lines kept faint for reference
let sqView = "blocks";
// the disaster type and category the odds are for ("hurricane:cat3"); every
// square carries every scenario's odds, so switching needs no new request
let sqScenario = null;
let sqMenu = [];
const BLOCKS = 4;
// a smooth scale, so blocks a few points apart still look different:
// teal at 0%, yellow at 30%, red at 70%, dark red at 100%
const CHANCE_STOPS = [[0, [42, 157, 143]], [0.15, [138, 201, 38]], [0.3, [233, 196, 106]], [0.5, [244, 162, 97]], [0.7, [230, 57, 70]], [1, [128, 15, 47]]];
function chanceColor(c) {
  if (c == null) return "#9a9a9a";
  const v = Math.max(0, Math.min(1, c));
  for (let k = 1; k < CHANCE_STOPS.length; k++) {
    const [x1, c1] = CHANCE_STOPS[k], [x0, c0] = CHANCE_STOPS[k - 1];
    if (v <= x1) { const t = (v - x0) / (x1 - x0); return `rgb(${c0.map((a, i) => Math.round(a + (c1[i] - a) * t)).join(",")})`; }
  }
  return "rgb(128,15,47)";
}
function factorsOf(p) {
  return { age: p.code_era || "year built unknown", construction: (p.material || p.foundation) ? `${p.material || "unknown material"} on ${p.foundation || "unknown foundation"}` : "construction unknown", green: p.green || "green unknown" };
}
function mostCommon(list) { const c = {}; let best = null; for (const v of list) { c[v] = (c[v] || 0) + 1; if (!best || c[v] > c[best]) best = v; } return [best, best ? c[best] : 0]; }
function showSquare(ev) {
  const ll = ev.polygon.map(([x, y]) => [y, x]);
  const bounds = L.latLngBounds(ll);
  const [w, s, e, n] = [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()];
  const blocks = {};
  for (const p of ev.points) {
    const i = Math.min(BLOCKS - 1, Math.floor(((p.lon - w) / (e - w)) * BLOCKS)), j = Math.min(BLOCKS - 1, Math.floor(((p.lat - s) / (n - s)) * BLOCKS));
    (blocks[`${i},${j}`] = blocks[`${i},${j}`] || { i, j, pts: [] }).pts.push(p);
  }
  if (ev.scenarios) sqMenu = ev.scenarios;
  if (ev.scenario) sqScenario = ev.scenario;
  if (SQ[ev.square]) squaresLayer.removeLayer(SQ[ev.square].layer);
  const layer = L.layerGroup();
  layer.addLayer(L.polygon(ll, { color: "#ff9f1c", weight: 3, fill: false, interactive: false }));
  const cells = [];
  for (const b of Object.values(blocks)) {
    const bw = w + ((e - w) * b.i) / BLOCKS, bs = s + ((n - s) * b.j) / BLOCKS;
    const bb = [[bs, bw], [bs + (n - s) / BLOCKS, bw + (e - w) / BLOCKS]];
    b.rect = L.rectangle(bb, { color: "#fff", weight: 1, fillOpacity: 0.5 }).addTo(layer);
    b.tag = L.tooltip({ permanent: true, direction: "center", className: "sec-label blk-label", interactive: false }).setLatLng(L.latLngBounds(bb).getCenter());
    layer.addLayer(b.tag);
    cells.push(b);
  }
  const title = L.tooltip({ permanent: true, direction: "top", className: "sec-label sq-label", interactive: false }).setLatLng([n, (w + e) / 2]);
  layer.addLayer(title);
  squaresLayer.addLayer(layer);
  SQ[ev.square] = { ev, layer, cells, stats: {}, bounds, title };
  cellLayer.clearLayers();
  state.groups = null;
  paintSquares();
  sqShown = ev.square;
  showDots();
  sqPanel();
  map.fitBounds(bounds, { padding: [30, 30], maxZoom: 17 });
}
function avgOf(pts, key = "chance_damaged") {
  const ks = pts.filter((p) => p[key] != null);
  return ks.length ? ks.reduce((t, p) => t + p[key], 0) / ks.length : null;
}
// each building's chance in the chosen disaster type and category
function applyScenario(sq) {
  const ev = sq.ev;
  for (const p of ev.points) {
    const c = ev.classes && ev.classes[p.cls];
    if (c && sqScenario && c.odds[sqScenario]) { p.chance_damaged = c.odds[sqScenario][0]; p.chance_severe = c.odds[sqScenario][1]; p.repair = c.odds[sqScenario][2] ?? null; p.flooded = c.odds[sqScenario][3] ?? null; p.measured = (c.measured || {})[sqScenario] || null; }
  }
  for (const b of sq.cells) { b.avg = avgOf(b.pts); b.val = avgOf(b.pts, METRIC[sqColorBy] || "chance_damaged"); }
  sq.stats = { buildings: ev.points.length, avg: avgOf(ev.points), severe: avgOf(ev.points, "chance_severe"), repair: avgOf(ev.points, "repair") };
  sq.title.setContent(`${esc(ev.square)}: ${sq.stats.buildings.toLocaleString()} buildings${sq.stats.avg == null ? "" : `, ${Math.round(sq.stats.avg * 100)}% average chance`}`);
}
const pct = (v) => (v == null ? "–" : Math.round(v * 100) + "%");
function scenarioParts() {
  const [t, c] = (sqScenario || ":").split(":");
  const type = sqMenu.find((x) => x.type === t);
  const cat = type && type.categories.find((x) => x.id === c);
  return { t, c, type, cat };
}
function paintSquares() {
  for (const sq of Object.values(SQ)) applyScenario(sq);
  for (const sq of Object.values(SQ)) {
    for (const b of sq.cells) {
      let color, text, tip;
      const n = b.pts.length;
      if (isMetric(sqColorBy)) {
        color = chanceColor(b.val);
        text = b.val == null ? "?" : `${Math.round(b.val * 100)}%`;
      } else {
        const [v, c] = mostCommon(b.pts.map((p) => factorsOf(p)[sqColorBy]));
        color = groupColor(v);
        text = `${Math.round((100 * c) / n)}%`;
      }
      const [top] = mostCommon(b.pts.map((p) => p.group));
      const br = avgOf(b.pts, "repair"), bs = avgOf(b.pts, "chance_severe");
      tip = `<b>${n} building${n === 1 ? "" : "s"}</b>${b.avg == null ? "" : `, ${Math.round(b.avg * 100)}% average chance of damage`}${bs == null ? "" : `<br>${Math.round(bs * 100)}% chance of bad damage`}${br == null ? "" : `<br>Expected repairs: ${Math.round(br * 100)}% of building value`}<br>Most common: ${esc(top || "")}`;
      const blocks = sqView === "blocks";
      b.rect.setStyle({ fillColor: color, fillOpacity: blocks ? 0.5 : 0, weight: blocks ? 1 : 0.6, opacity: blocks ? 1 : 0.5 });
      const re = b.rect.getElement && b.rect.getElement();
      if (re) re.style.pointerEvents = blocks ? "" : "none";  // let hovers reach the dots
      b.rect.unbindTooltip();
      if (blocks) b.rect.bindTooltip(tip, { sticky: true });
      b.tag.setContent(`${text}<small>${n}</small>`);
      const el = b.tag.getElement && b.tag.getElement();
      if (el) el.style.display = blocks ? "" : "none";
    }
  }
}
const groupColorMap = {};
function groupColor(v) {
  if (!(v in groupColorMap)) groupColorMap[v] = GROUP_COLORS[Object.keys(groupColorMap).length % (GROUP_COLORS.length - 1)];
  return groupColorMap[v];
}
function showDots() {
  groupPoints.clearLayers();
  if (sqView !== "buildings") return;
  for (const sq of Object.values(SQ)) for (const p of sq.ev.points) {
    const f = factorsOf(p);
    const m = p.measured;
    const range = m ? ` (${Math.round(m[0] * 100)} to ${Math.round(m[1] * 100)}%, from ${m[2]} look-alikes)` : "";
    L.circleMarker([p.lat, p.lon], { renderer: pointRenderer, radius: 4.5, weight: 1, color: "#111", fillOpacity: 0.95, fillColor: isMetric(sqColorBy) ? chanceColor(metricOf(p)) : groupColor(f[sqColorBy]) })
      .bindTooltip(`<b>${p.chance_damaged == null ? "No odds" : `${Math.round(p.chance_damaged * 100)}% chance of damage`}</b>${esc(range)}${p.chance_severe != null ? `<br>${Math.round(p.chance_severe * 100)}% chance of bad damage or worse` : ""}${p.repair != null ? `<br>Expected repairs: ${Math.round(p.repair * 100)}% of its value` : ""}${surgeNote(p)}<br>${esc(f.age)}<br>${esc(f.construction)}<br>${esc(f.green)}${p.year_built ? `<br>Built ${p.year_built} (${esc(p.year_source || "")})` : ""}`)
      .addTo(groupPoints);
  }
}
function surgeNote(p) {
  const { t, c } = scenarioParts();
  const ft = t === "hurricane" && p.surge ? p.surge[["cat1", "cat2", "cat3", "cat4", "cat5"].indexOf(c)] : 0;
  return (ft ? `<br>Storm surge here (worst case): about ${ft} ft above the ground` : "") + (p.flooded ? `<br>${Math.round(p.flooded * 100)}% chance of water inside (flooding, not counted as damage)` : "");
}
// how hard a hurricane of the chosen category hits each square
function siteNote() {
  const { t, c, cat } = scenarioParts();
  if (t !== "hurricane") return "";
  const lines = Object.entries(SQ).filter(([, sq]) => sq.ev.site).map(([k, sq]) => {
    const st = sq.ev.site;
    const wet = sq.ev.points.filter((p) => p.surge && p.surge[["cat1", "cat2", "cat3", "cat4", "cat5"].indexOf(c)]).length;
    const inside = Math.round(sq.ev.points.reduce((a, p) => a + (p.flooded || 0), 0));
    const surge = !st.surge_checked ? "too far inland for storm surge" : `${wet.toLocaleString()} building${wet === 1 ? "" : "s"} in the worst-case surge, about ${inside.toLocaleString()} with water inside`;
    return `${esc(k)}: ${st.coast_miles} mi from the coast, about ${st.gusts[c]} mph gusts here (${cat ? cat.intensity : "?"} at landfall${st.gusts_slow && st.gusts_slow[c] !== st.gusts_fast[c] ? `; ${st.gusts_slow[c]} to ${st.gusts_fast[c]} mph if the storm moves at ${st.slow_mph} to ${st.fast_mph} mph instead of ${st.forward_mph}` : ""}); ${surge}.`;
  });
  return lines.length ? `<p class="gnote">${lines.join("<br>")}</p>` : "";
}
function sqPanel() {
  const panel = $("groups");
  panel.hidden = false;
  const rows = Object.entries(SQ).map(([k, sq]) => `<tr data-sq="${esc(k)}"><td>${esc(k)}</td><td>${sq.stats.buildings.toLocaleString()}</td><td>${pct(sq.stats.avg)}</td><td>${pct(sq.stats.severe)}</td><td>${pct(sq.stats.repair)}</td></tr>`).join("");
  let legend;
  if (isMetric(sqColorBy)) {
    const what = { chance: "chance of damage", severe: "chance of bad damage or worse", loss: "expected repair cost (share of building value)", flooded: "chance of water above the first floor (flooding, not counted as damage)" }[sqColorBy];
    legend = `<div class="gradbar" style="background:linear-gradient(to right, ${[0, 0.2, 0.4, 0.6, 0.8, 1].map(chanceColor).join(",")})"></div><div class="gradlab"><span>0%</span><span>50%</span><span>100%</span></div><p class="gnote">${sqView === "blocks" ? `Average ${what} of the buildings in each block.` : `Each building's own ${what}. Hover a dot for its details.`}${sqColorBy === "loss" ? " Tornadoes and the measured scenarios have no repair cost data (shown gray)." : sqColorBy === "flooded" ? " Only hurricanes and floods have it (others gray)." : ""}</p>`;
  } else {
    const vals = new Set();
    for (const sq of Object.values(SQ)) for (const p of sq.ev.points) vals.add(factorsOf(p)[sqColorBy]);
    legend = [...vals].map((v) => `<div class="grow"><i style="background:${groupColor(v)}"></i><span>${esc(v)}</span></div>`).join("") + `<p class="gnote">${sqView === "blocks" ? "Each block shows its most common kind and what share of its buildings that is." : "Each building colored by its own kind."}</p>`;
  }
  const { t: curT, c: curC, type: curType, cat: curCat } = scenarioParts();
  const typeOpts = sqMenu.map((x) => `<option value="${esc(x.type)}"${x.type === curT ? " selected" : ""}>${esc(x.name)}</option>`).join("");
  const catOpts = curType ? curType.categories.map((x) => `<option value="${esc(x.id)}"${x.id === curC ? " selected" : ""}>${esc(x.label)}</option>`).join("") : "";
  const measured = curType && curType.categories.every((x) => x.intensity == null);
  const where = !curType ? "past disasters" : measured ? `look-alike buildings inspected after a real disaster (${esc(curType.note.replace(/^measured: /, ""))})` : esc(curType.source || "damage curves");
  panel.innerHTML = `<div class="ghead"><b>Compare squares</b><button type="button" class="linkish" id="sq-clear">Clear all</button></div>
    ${sqMenu.length ? `<div class="sqscen"><label>Disaster <select id="sq-type">${typeOpts}</select></label><label>Category <select id="sq-cat"${curType && curType.categories.length < 2 ? " disabled" : ""}>${catOpts}</select></label></div>${curType && !measured ? `<p class="gnote">${curCat && curCat.intensity != null && curCat.unit !== "ft of water above the ground" ? `About ${curCat.intensity} ${esc(curCat.unit)}${curT === "hurricane" ? " at landfall" : ""}. ` : ""}${esc(curType.note.charAt(0).toUpperCase() + curType.note.slice(1))}.</p>` : ""}` : ""}
    <table class="sqtable"><thead><tr><th>Square</th><th>Buildings</th><th title="Average chance of damage">Damaged</th><th title="Average chance of bad damage or worse">Bad damage</th><th title="Expected repair cost as a share of building value">Repairs</th></tr></thead><tbody>${rows}</tbody></table>${siteNote()}
    <div class="sqview" role="group" aria-label="Show">${[["blocks", "Blocks"], ["buildings", "Individual buildings"]].map(([v, t]) => `<button type="button" data-view="${v}" class="${v === sqView ? "on" : ""}" aria-pressed="${v === sqView}">${t}</button>`).join("")}</div>
    <label>${sqView === "blocks" ? "Shade blocks by" : "Color buildings by"} <select id="sq-by">${["chance", "severe", "loss", "flooded", "age", "construction", "green"].map((b) => `<option value="${b}"${b === sqColorBy ? " selected" : ""}>${FACTOR_NAME[b]}</option>`).join("")}</select></label>
    <div>${legend}</div>
    <p class="gnote">Each square is split into 16 blocks of about a quarter mile; the number under each is its building count. Odds${curCat ? ` for ${esc(curType.name.toLowerCase())}, ${esc(curCat.label)},` : ""} come from ${where}; rough, not a forecast. Pick another square to add it here.</p>`;
  const rescore = () => { paintSquares(); showDots(); sqPanel(); };
  if ($("sq-type")) {
    $("sq-type").addEventListener("change", (e) => { const ty = sqMenu.find((x) => x.type === e.target.value); sqScenario = `${ty.type}:${ty.default}`; rescore(); });
    $("sq-cat").addEventListener("change", (e) => { sqScenario = `${curT}:${e.target.value}`; rescore(); });
  }
  $("sq-by").addEventListener("change", (e) => { sqColorBy = e.target.value; paintSquares(); showDots(); sqPanel(); });
  panel.querySelectorAll("button[data-view]").forEach((bt) => bt.addEventListener("click", () => { sqView = bt.dataset.view; paintSquares(); showDots(); sqPanel(); }));
  $("sq-clear").addEventListener("click", () => { for (const k of Object.keys(SQ)) delete SQ[k]; squaresLayer.clearLayers(); groupPoints.clearLayers(); $("groups").hidden = true; });
  panel.querySelectorAll("tr[data-sq]").forEach((tr) => tr.addEventListener("click", () => { const k = tr.dataset.sq; sqShown = k; sqPanel(); map.fitBounds(SQ[k].bounds, { padding: [30, 30], maxZoom: 17 }); }));
}
const groupPoints = L.layerGroup().addTo(map);
const pointRenderer = L.canvas({ padding: 0.3 });
const CHANCE_BINS = [[0.1, "#2a9d8f", "under 10%"], [0.25, "#e9c46a", "10 to 25%"], [0.4, "#f4a261", "25 to 40%"], [1.01, "#e63946", "40% or more"]];
function showGroups(ev) {
  if (ev.points) return showSquare(ev);
  state.groups = ev;
  if (ev.polygon) {
    cellLayer.clearLayers();
    cellLayer.addLayer(L.polygon(ev.polygon.map(([x, y]) => [y, x]), { color: "#ff9f1c", weight: 4, fill: false, interactive: false }));
  }
  const panel = $("groups");
  panel.hidden = false;
  const opts = (ev.points ? ev.by : ["all", ...ev.by]).map((b) => `<option value="${b}">${FACTOR_NAME[b] || b}</option>`).join("");
  panel.innerHTML = `<div class="ghead"><b>Groups in ${esc(ev.square)}</b><button type="button" class="linkish" id="groups-close">Back to damage colors</button></div>
    <label>Color by <select id="groups-by">${opts}</select></label><div id="groups-list"></div>`;
  $("groups-by").addEventListener("change", colorGroups);
  $("groups-close").addEventListener("click", clearGroups);
  colorGroups();
  // bring the grouped buildings into view
  const ids = Object.keys(ev.factors).filter((id) => state.features[id]);
  if (ev.polygon) map.fitBounds(L.polygon(ev.polygon.map(([x, y]) => [y, x])).getBounds(), { padding: [30, 30], maxZoom: 17 });
  else if (ids.length) map.fitBounds(L.featureGroup(ids.map((id) => state.features[id])).getBounds(), { padding: [40, 40], maxZoom: 18 });
}
function colorGroups() {
  const ev = state.groups;
  if (!ev) return;
  const by = $("groups-by").value;
  const keyOf = (id) => (by === "all" ? ev.building_group[id] : (ev.factors[id] || {})[by]);
  const count = {};
  for (const id of Object.keys(ev.factors)) { const k = keyOf(id); if (k) count[k] = (count[k] || 0) + 1; }
  const top = Object.entries(count).sort((a, b) => b[1] - a[1]);
  const color = {};
  top.forEach(([k], i) => { color[k] = i < GROUP_COLORS.length - 1 ? GROUP_COLORS[i] : "#9a9a9a"; });
  for (const [id, layer] of Object.entries(state.features)) {
    const k = ev.factors[id] ? keyOf(id) : null;
    layer.setStyle(k ? { color: color[k], weight: 3, opacity: 1, dashArray: null, fillColor: color[k], fillOpacity: 0 } : { color: "#777", weight: 1, opacity: 0.6, dashArray: null, fillOpacity: 0 });
  }
  const shown = top.slice(0, GROUP_COLORS.length - 1);
  const rest = top.slice(GROUP_COLORS.length - 1).reduce((n, [, c]) => n + c, 0);
  $("groups-list").innerHTML = shown.map(([k, n]) => `<div class="grow"><i style="background:${color[k]}"></i><span>${esc(k)}</span><em>${n}</em></div>`).join("")
    + (rest ? `<div class="grow"><i style="background:#9a9a9a"></i><span>Smaller groups</span><em>${rest}</em></div>` : "");
}
function clearGroups() {
  state.groups = null;
  $("groups").hidden = true;
  cellLayer.clearLayers();
  for (const layer of Object.values(state.features)) layer.setStyle(styleFor(layer.feature));
}

// ---------------------------------------------------------------- phone: the whole screen for the map
$("mapsize").addEventListener("click", () => {
  const big = document.querySelector(".shell").classList.toggle("bigmap");
  $("mapsize").textContent = big ? "Back to chat" : "Full map";
  $("mapsize").setAttribute("aria-pressed", String(big));
  setTimeout(() => map.invalidateSize(), 50);
});

// ---------------------------------------------------------------- drawing a box
let start = null;
$("draw").addEventListener("click", () => setDrawing(!state.drawing));
function setDrawing(on) {
  state.drawing = on;
  $("draw").classList.toggle("on", on);
  $("draw").textContent = on ? (matchMedia("(pointer: coarse)").matches ? "Drag a finger on the map…" : "Drag on the map…") : "Draw a box";
  map.dragging[on ? "disable" : "enable"]();
  map.touchZoom[on ? "disable" : "enable"]();
  map.getContainer().style.cursor = on ? "crosshair" : "";
  map.getContainer().style.touchAction = on ? "none" : "";  // a finger draws instead of scrolling the page
  if (on) $("context").textContent = "Drag a box over the buildings you want checked (up to about 60). Click the button again or press Esc to cancel.";
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
  if (document.querySelector(".shell.bigmap")) $("mapsize").click();  // back to the chat, to send it
  if (state.upload) {
    // the photos go where the box is: show the after photo there to check the fit
    if (photoPreview) map.removeLayer(photoPreview);
    photoPreview = L.imageOverlay(state.upload.url, b, { opacity: 0.85, interactive: false }).addTo(map);
    $("context").innerHTML = `Your photos will be laid over this box, north up. <button type="button" class="linkish" id="clear-box">Draw again</button>`;
    $("clear-box").addEventListener("click", () => { clearBox(); setDrawing(true); });
    if (!$("input").value.trim()) $("input").value = "Check the damage in my photos.";
    $("input").focus();
    return;
  }
  const cap = Math.min(60, state.config.max_buildings || 60);  // the agent's default, capped by this server
  $("context").innerHTML = `Box drawn: ${km2 < 0.1 ? Math.round(km2 * 1e6).toLocaleString() + " m²" : km2.toFixed(2) + " km²"}${
    cap ? `. I'll check up to ${cap} buildings in it, nearest the middle first` : ""}. <button type="button" class="linkish" id="clear-box">Clear box</button>`;
  $("clear-box").addEventListener("click", () => { clearBox(); $("context").textContent = ""; });
  if (!$("input").value.trim()) $("input").value = "Check the damage in this area.";
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
const putUpload = (file, q) => fetch(`/api/uploads?${q}`, { method: "POST", body: file }).then(async (r) => {
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
});
// the after photo goes up (and Muse Wonder looks at it) as soon as it's picked,
// while the rest of the form is filled in, so pressing the button doesn't wait on it
let early = null;
$("upload-after").addEventListener("change", () => {
  $("upload-anyway").hidden = true;
  $("upload-status").textContent = "";
  const file = $("upload-after").files[0];
  early = file ? { file, sent: putUpload(file, "side=after") } : null;
  if (early) early.sent.catch(() => {});
});
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
  const anyway = e.submitter && e.submitter.id === "upload-anyway";
  status.textContent = anyway ? "Uploading…" : "Uploading… checking the photo was taken from above.";
  const put = putUpload;
  try {
    const a = early && early.file === after ? await early.sent.catch(() => put(after, "side=after")) : await put(after, anyway ? "side=after&check=false" : "side=after");
    // a photo from the street or a screenshot can't be found on the map: say so
    // now, not after a minute of searching, and let them go on if they disagree
    if (a.check && !a.check.usable && !anyway) {
      status.textContent = `${a.check.model || "Muse Wonder"} says this photo probably won't work: ${a.check.reason || "it doesn't look like it was taken from above."} I need one taken looking down from a drone, plane or satellite.`;
      $("upload-anyway").hidden = false;
      return;
    }
    $("upload-anyway").hidden = true;
    if (a.check && a.check.shows) $("upload").title = `${a.check.model || "Muse Wonder"}: ${a.check.shows}`;
    if (before) await put(before, `side=before&id=${a.id}`);
    state.upload = { id: a.id, url: URL.createObjectURL(after), before: !!before, event, gps: a.gps, check: a.check };
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
  $("input").value = `Find where my ${before ? "photos were" : "photo was"} taken in ${place.short || place.label} and check the buildings in ${before ? "them" : "it"}`;
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
  // web search answers bring outside text and links: strip any markup that isn't text
  const html = window.marked ? marked.parse(text || "") : esc(text);
  el.innerHTML = window.DOMPurify ? DOMPurify.sanitize(html) : window.marked ? esc(text) : html;
  el.querySelectorAll("a[href^='http'], a[href^='/api/']").forEach((a) => { a.target = "_blank"; a.rel = "noopener noreferrer"; });
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

// The run log: when the agent assesses a new area, a plan card lists its steps
// and ticks them off as they finish, so anyone watching sees where it is and
// what it has done; each step's card below holds the details.
const PLAN = ["locate_imagery", "prepare_area", "measure_structures", "measure_flood", "look_for_debris", "draft_categories", "estimate_costs_and_needs"];
let plan = null;
function planStep(name, status) {
  if (!PLAN.includes(name)) return;
  if (!plan || (name === PLAN[0] && status === "running")) {
    const el = div("plan");
    el.innerHTML = `<div class="plan-head"><b>My plan</b><span class="meta"></span></div><ol>${PLAN.map((t) => `<li data-tool="${t}"><span class="mark"></span>${esc(STEP[t])}</li>`).join("")}</ol>`;
    plan = { el, t0: performance.now(), sam: 0, wonder: state.upload && state.upload.check ? state.upload.check.model || "Muse Wonder" : null, timer: setInterval(() => { el.querySelector(".meta").textContent = `${Math.round((performance.now() - plan.t0) / 1000)}s`; }, 1000) };
    add(el);
  }
  const li = plan.el.querySelector(`li[data-tool="${name}"]`);
  li.className = status;
  if (status !== "running" && (name === PLAN[PLAN.length - 1] || status === "failed")) {
    clearInterval(plan.timer);
    metaUsed(plan);
    plan = null;
  }
}
// at the end of a run, which of Meta's models did what in it
function metaUsed(p) {
  const tr = p.trails || [];
  const by = (name) => tr.filter((t) => t.some((x) => modelName(x.model) === name)).length;
  const parts = [
    p.wonder && `${p.wonder} checked the photo was taken from above`,
    p.sam && `Segment Anything 3.1 outlined buildings, debris and tarps in ${p.sam} images`,
    tr.length && `Muse Spark Open judged ${by("Muse Spark Open")} buildings and ran this plan`,
    by("Muse Spark") && `Muse Spark gave a second opinion on ${by("Muse Spark")} hard ${by("Muse Spark") === 1 ? "one" : "ones"}`,
    tr.length && "Muse Spark is rewriting each note in everyday words",
  ].filter(Boolean);
  if (parts.length) p.el.insertAdjacentHTML("beforeend", `<p class="meta-used"><b>Meta models in this run:</b> ${esc(parts.join("; "))}.</p>`);
}
const SHORT = { "flood rule": "the flood rule", "satellite rule": "the satellite rule", "photo rule": "the one-photo rule", "cloud check": "the cloud check" };
function trailText(id, trail) {
  // "b094: Muse Spark Open said destroyed, the second check objected; Muse Spark said badly damaged"
  const said = (c) => (LABEL[c] || c).toLowerCase();
  const parts = trail.map((t, i) => t.error ? `${modelName(t.model)} didn't answer`
    : SHORT[t.model] ? `${SHORT[t.model]} ${t.said ? `raised it to ${said(t.said)}` : "sent it for a visit"}`
    : i && trail[i - 1].model === t.model ? `it changed its answer to ${said(t.said)}${t.objected ? ", and the second check still objected" : ""}`
    : `${modelName(t.model)} said ${said(t.said)}${t.objected ? ", the second check objected" : ""}`);
  return `${id}: ${parts.join("; ")}`;
}
// only the buildings where something happened beyond one clean answer
const notable = (trail) => trail.length > 1 || trail.some((t) => t.objected || t.error);

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
  current = { el: d, t0: performance.now(), trails: [] };
  planStep(name, "running");
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
  if (current.trails.length) {
    // the draft step's tally: who judged, how often the stronger model was called, how often the checker pushed back
    const tr = current.trails;
    const by = (name) => tr.filter((t) => t.some((x) => modelName(x.model) === name)).length;
    const objected = tr.filter((t) => t.some((x) => x.objected)).length;
    const changed = tr.filter((t) => new Set(t.filter((x) => x.said).map((x) => x.said)).size > 1).length;
    stepLine(`${by("Muse Spark Open")} buildings judged by Muse Spark Open; ${by("Muse Spark")} hard ones also went to Muse Spark; the second check pushed back on ${objected}, and ${changed} ${changed === 1 ? "call was" : "calls were"} changed because of it.`);
  }
  planStep(current.el.dataset.tool, failed ? "failed" : "done");
  current = null;
}

function describeProgress(ev) {
  switch (ev.event) {
    case "buildings": return state.upload ? `Found ${ev.found} buildings in the photo; checking ${ev.kept}` : `Found ${ev.found} buildings in the box; checking ${ev.kept}`;
    case "photos": return `Photos: ${ev.note}`;
    case "detect": return `Looking for buildings in the photo, piece by piece (${ev.tiles} pieces)`;
    case "imagery": return `${ev.side === "post" ? "After" : "Before"}: ${ev.source}`;
    case "aligned": return `Matched ${ev.matched} buildings between the two photos, ${ev.from_neighbors} more using the houses next door; ${ev.unaligned} couldn't be matched`;
    case "sam":
      if (plan) plan.sam += ev.calls || 0;
      return `Segment Anything 3.1 is outlining ${ev.prompts.join(", ")} in ${ev.calls} images`;
    case "structure_loss": return `Measured ${ev.measured} of ${ev.of} buildings. Mostly gone: ${ev.over_60.join(", ") || "none"}. Partly gone: ${ev.over_25.join(", ") || "none"}`;
    case "flood_source": return ev.error ? ev.error : `${ev.source}: ${ev.elev_ft} ft NAVD88`;
    case "flood": return ev.error ? ev.error : `Flood water reached ${ev.flooded} of ${ev.of} buildings, up to ${ev.max_ft} ft deep`;
    case "debris": return `Debris piles at: ${ev.debris.join(", ") || "none found"}. Roof tarps at: ${ev.tarps.join(", ") || "none found"}`;
    case "assess_plan": return `Judging ${ev.buildings} buildings, worst-looking first: ${ev.first.join(", ")}…`;
    case "assessed": return `Done: ${Object.entries(ev.counts).map(([k, v]) => `${v} ${(LABEL[k] || k).toLowerCase()}`).join(", ")}; ${ev.review} need a visit to confirm`;
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
        if (current && ev.trail) {
          current.trails.push(ev.trail);
          if (plan) (plan.trails = plan.trails || []).push(ev.trail);
          if (notable(ev.trail)) stepLine(trailText(ev.id, ev.trail));
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
      // the pages a web search read: listed under the answer, straight from the search
      if (ev.name === "find_help" && ev.result && ev.result.sources) state.sources = (state.sources || []).concat(ev.result.sources);
      endStep(ev.result);
      break;
    case "groups":
      showGroups(ev);
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
        const kind = ev.area.startsWith("upload_") ? "your photos" : "your box";
        areaLabels[ev.area] = areaLabels[ev.area] || `${$("place").value.trim() || "New area"}, ${kind}`;
        $("area").insertAdjacentHTML("beforeend", `<option value="${esc(ev.area)}">${esc(areaLabels[ev.area])}</option>`);
        // the server names the place in a moment (from the map's address search): use that name
        const area = ev.area;
        setTimeout(() => fetch("/api/areas").then((r) => r.json()).then((rows) => {
          const row = rows.find((x) => x.name === area);
          const opt = [...$("area").options].find((o) => o.value === area);
          if (row && row.label && opt) opt.textContent = areaLabels[area] = `${row.label}, ${kind}`;
        }).catch(() => {}), 4000);
      }
      if (ev.area) $("area").value = ev.area;
      if (state.upload) {
        // found and laid out: the photos are used; if not found they stay, to drag a box over the spot
        state.upload = null;
        $("upload").textContent = "Use my photos";
        $("upload").classList.remove("on");
      }
      loadBuildings(true);
      if (!state.flight) setOverlay(ev.area);
      break;
    case "message":
      if (ev.area && ev.area !== state.area) { state.area = ev.area; }
      loadBuildings(false).then(() => {
        const el = agentMessage(ev.text);
        const src = (state.sources || []).filter((x) => /^https:\/\//.test(x.url));
        state.sources = null;
        if (src.length) {
          el.insertAdjacentHTML("beforeend", `<p class="sources"><b>Sources (web search):</b></p><ul class="sources">${src.map((x) => `<li><a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(x.title || x.url)}</a></li>`).join("")}</ul>`);
          scroll();
        }
      });
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
    // no code: the server replays the nearest finished example instead (mock mode, no AI calls)
  }
  const storm = $("storm").value === EVENT ? null : $("storm").value || null;
  const cell = state.cell && state.cell.pending ? { label: state.cell.label, polygon: state.cell.polygon, storm: $("storm").value || "" } : null;
  if (state.cell) state.cell.pending = false;
  const body = { text, cell, lang: lang(), box: state.box, storm: state.box ? storm : null, access_code: code, upload: state.box && state.upload ? state.upload.id : null, event: state.box && state.upload ? state.upload.event : null };
  if (state.box) { outlines.clearLayers(); state.features = {}; }
  if (state.box && state.upload && !state.findUpload) {
    // the photos are used once; the map shows the area's own copy when it's ready
    state.upload = null;
    $("upload").textContent = "Use my photos";
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
    const box = add(div("msg agent codeask", `<p>Assessing a new area runs the AI models, which costs money, so this public copy needs an access code. No code? Press Skip to watch a replay of a recorded run instead (no AI models are called).</p>
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
    ["How sure", a && a.confidence != null ? `${Math.round(a.confidence * 100)}%` : "–"],
    ["Judged by", a ? modelName(a.decided_by) : "–"],
    ["Building missing", a && a.structure_loss != null ? `${Math.round(a.structure_loss * 100)}%` : "not measured"],
    ["Debris in yard", a && a.debris_share ? `${Math.round(a.debris_share * 100)}%` : "none found"],
    ["Flooded", a && a.flood ? a.flood.label.replace(/^Flooded: /, "yes: ") : a && a.flood_ft != null && a.flood_ft >= 0.5 ? "yes (not counted as damage)" : "no"],
    ["Flood water outside", a && a.flood_ft != null ? `${a.flood_ft.toFixed(1)} ft deep` : us ? "none measured here" : "unknown (not measured here)"],
    ["Built", a && a.built && (a.built.year_built || a.built.block_median_year_built) ? (a.built.year_built ? String(a.built.year_built) : `around ${a.built.block_median_year_built} (typical for the block)`) + (a.built.code_era ? `, ${a.built.code_era}` : "") : us ? "unknown (no property record)" : "unknown (no property records)"],
    ["Made of", a && a.built && a.built.material ? a.built.material + (a.built.material_source && /Inventory/.test(a.built.material_source) ? " (national inventory, often wrong)" : "") : "unknown"],
    ...(a && a.built && a.built.fire && Object.keys(a.built.fire).length ? [["Fire features", Object.entries(a.built.fire).map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`).join("; ") + " (CAL FIRE inspection)"]] : []),
    ["Stands on", a && a.built && a.built.foundation ? a.built.foundation + (a.built.stories ? `, ${a.built.stories} ${a.built.stories === 1 ? "story" : "stories"}` : "") + (a.built.foundation_source && /Inventory/.test(a.built.foundation_source) ? " (national inventory, often wrong)" : "") : "unknown"],
    ["Floor height", a && a.record && a.record.basis ? a.record.basis : us ? "unknown" : "unknown (no property records)"],
    ["Water inside", !us ? "unknown" : need && need.water_inside_ft[1] > 0 ? `${need.water_inside_ft[0].toFixed(1)}–${need.water_inside_ft[1].toFixed(1)} ft (estimate)` : "none"],
    ["Homes", need ? (need.households ? `${need.households}${need.likely_displaced ? ", likely can't live there now" : need.possibly_displaced ? ", may not be able to live there now" : ""}` : "none (not a home)") : "–"],
    ["Repair cost", cost ? (cost.cost_high_usd ? `${usd(cost.cost_low_usd)}–${usd(cost.cost_high_usd)} (building worth ${usd(cost.value_usd)})` : `none seen (building worth ${usd(cost.value_usd)})`) : "–"],
    ["Repairs over half its worth?", !us ? "US rule, not used here" : cost ? (cost.substantial_damage_likely ? "likely: rebuilding may have to meet flood rules" : cost.substantial_damage_possible ? "possibly: rebuilding may have to meet flood rules" : "no") : "–"],
    ...(a && a.demolished ? [["Note", "gone before the disaster (demolished)"]] : []),
  ];
  $("card-body").innerHTML = `
    <h2>Building ${esc(id)} ${cat ? `<span class="pill ${cat}" style="--c:var(--${cat})" title="FEMA category: ${FEMA[cat]}">${LABEL[cat]}</span>` : ""}${a && (a.flood || (a.flood_ft || 0) >= 0.5) ? `<span class="pill" style="--c:var(--flood, #2b6cb0)" title="Flood water stood here. Flooding is stated on its own and not counted in the damage category.">Flooded</span>` : ""}${a && a.needs_human ? `<span class="pill" style="--c:var(--review)" title="A person should visit to confirm">Needs a visit</span>` : ""}</h2>
    <img src="/api/areas/${state.area}/pair/${id}.jpg" alt="Before and after photos of ${esc(id)}">
    <div class="cap"><span>Before${a && a.photos ? ": " + esc(shortSource(a.photos.before)) : ""}</span><span>After${a && a.photos ? ": " + esc(shortSource(a.photos.after)) : ""}</span></div>
    <p>${a ? esc(a.plain || a.evidence) : gone ? "This area was lost when the site restarted. Draw the box again to redo it." : "Not checked yet. The check of this area is still running, or it stopped before it reached this building; the chat shows which step it is on."}</p>
    ${a ? `<dl class="kv">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("")}</dl>` : ""}
    ${a ? `<p><a class="packet" href="${packetUrl(state.area, id)}" target="_blank" rel="noopener">Printable page for an insurance or aid claim</a></p>` : ""}
    ${trace.length ? `<div class="trace"><b>How this was decided</b>${a.plain ? `<div class="t">The AI's own note: ${esc(a.evidence)}</div>` : ""}${trace.map(traceRow).join("")}</div>` : ""}`;
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
  if (t.model === "satellite rule") return `<div class="t">Satellite photos aren't sharp enough to be sure, so a person should visit.</div>`;
  if (t.model === "flood rule") return `<div class="t">Flood water stood around it, so it was raised from ${esc(String(t.raised_from).toLowerCase())} to ${esc(String(t.to).toLowerCase())}.</div>`;
  if (t.error) return `<div class="t">${esc(modelName(t.model))}: answer couldn't be read, so it was asked again.</div>`;
  const a = t.answer || {};
  const obj = (t.verifier || []).map((o) => `<div class="obj">Second check: ${esc(o)}</div>`).join("");
  return `<div class="t"><div><b>${esc(modelName(t.model))}</b> said ${esc((LABEL[String(a.category || "").toLowerCase()] || String(a.category || "")).toLowerCase())} (${Math.round((a.confidence || 0) * 100)}% sure)</div>${obj || "<div>Second check: agreed.</div>"}</div>`;
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
    $("context").textContent = "Turning your voice into text…";
    try {
      const wav = await toWav16k(new Blob(chunks));
      const r = await fetch("/api/transcribe", { method: "POST", headers: { "content-type": "audio/wav", "x-sample-rate": "16000" }, body: wav });
      const { text } = await r.json();
      $("context").textContent = "";
      if (text) { $("input").value = text; send(); }
      else $("context").textContent = "Didn't catch that.";
    } catch (err) { $("context").textContent = "Couldn't make out the recording. Try again, or type your question."; }
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
      group("Hurricanes in the US (photos from planes)", hurricanes.filter((s) => s.imagery === "noaa")) +
      group("Hurricanes elsewhere (satellite photos)", hurricanes.filter((s) => s.imagery === "maxar")) +
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
    + (ours.length ? `<optgroup label="Added by visitors">${opts(ours)}</optgroup>` : "");
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
    suggestions(["Which buildings should be visited first?", "Write a short damage summary", "How many families may have lost their homes?", "Where can people here get help?"]);
  } else if (fit) {
    const place = params.get("place") || "this place";
    const s = storms.find((x) => x.key === key);
    agentMessage(
      s
        ? `Here's ${place}, with photos from after ${/^(Hurricane|Cyclone|Typhoon)/.test(s.name) ? "" : "the "}${s.name} (${year(s)}). Zoom in and draw a box around the buildings you want checked.`
        : `Here's ${place}. None of the disasters Landfall covers has photos here yet, so there's nothing to compare. Try another place, or pick a disaster from the menu.`
    );
  } else {
    agentMessage("Search for a place or pick a disaster, then draw a box on the map, and I'll show how badly each building in it was damaged.");
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
  const photos = kind === "maxar" ? "satellite photos from before and after"
    : kind === "photos" ? (pick.archive_before ? "your uploaded photo and an archive photo of the spot from before" : pick.uploaded_before ? "your uploaded before and after photos" : "your uploaded photo")
    : "photos taken from planes before and after";
  const tally = ["destroyed", "major", "minor", "affected", "none"].map((c) => `${n(c)} ${c === "none" ? "with no damage seen" : LABEL[c].toLowerCase()}`).join(", ");
  const check = feats.filter((p) => p.needs_human).length;
  return `I've checked **${feats.length} buildings in ${areaLabel(pick.name)}** after ${storm.split(" (")[0] || "the disaster"}, using ${photos}: ${tally}. ${check} should be visited by a person to confirm.`
    + checkedOwnWork(feats) + claimPage(feats, pick.name)
    + "\n\nAsk me anything about it in everyday words, by typing or with the microphone, or draw a new box on the map and I'll check that too.";
}
// what sets the drafts apart, said up front: every call was checked, and some changed because of it
function checkedOwnWork(feats) {
  const tr = feats.filter((p) => p.trail && p.trail.length);
  if (!tr.length) return "";
  const objected = tr.filter((p) => p.trail.some((t) => t.objected)).length;
  const second = tr.filter((p) => p.trail.some((t) => modelName(t.model) === "Muse Spark")).length;
  const first = (p) => (p.trail.find((t) => t.said) || {}).said;
  const changed = tr.filter((p) => first(p) && first(p) !== p.category);
  const sev = (c) => ORDER.indexOf(c);
  const ex = changed.slice().sort((a, b) => Math.max(sev(b.category), sev(first(b))) - Math.max(sev(a.category), sev(first(a))))[0];
  return `\n\n**I checked my own work.** A second check compared each of my calls with the measurements and questioned ${objected}; ${second} hard ones also went to a stronger model for a second opinion. `
    + (ex ? `I changed ${changed.length} ${changed.length === 1 ? "call" : "calls"} because of it, for example ${ex.id}: first ${LABEL[first(ex)].toLowerCase()}, now ${LABEL[ex.category].toLowerCase()}. ` : "")
    + "Click any building to see how its call was made.";
}
function claimPage(feats, area) {
  const worst = feats.filter((p) => p.category).sort((a, b) => ORDER.indexOf(b.category) - ORDER.indexOf(a.category))[0];
  if (!worst || ORDER.indexOf(worst.category) < ORDER.indexOf("minor")) return "";
  return `\n\n**Ready for a claim:** every building has a printable page for an insurance or aid claim, with its photos and the reasons. [Open the page for the most damaged building](${packetUrl(area, worst.id)}).`;
}
function title(name) { return name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()); }

// ---------------------------------------------------------------- language
// Many families in the storm areas speak Spanish or Haitian Creole: the agent
// answers in the chosen language and claim pages open translated (by Muse Spark,
// with the English original linked). The rest of the page stays in English.
const SAID = {
  es: "Ahora respondo en español, y las páginas para reclamos se abren en español. Puede escribir o hablar en español.",
  ht: "Kounye a m ap reponn an kreyòl ayisyen, epi paj pou reklamasyon yo ap louvri an kreyòl. Ou ka ekri oswa pale an kreyòl.",
};
function lang() { return $("lang").value; }
function packetUrl(area, id) {
  return `/api/areas/${encodeURIComponent(area)}/packet/${id}${lang() === "en" ? "" : `?lang=${lang()}`}`;
}
try { const saved = localStorage.getItem("landfall-lang"); if (saved && SAID[saved]) $("lang").value = saved; } catch (e) {}
$("lang").addEventListener("change", () => {
  try { localStorage.setItem("landfall-lang", lang()); } catch (e) {}
  if (SAID[lang()]) agentMessage(SAID[lang()]);
  else agentMessage("I'll answer in English, and claim pages open in English.");
});
