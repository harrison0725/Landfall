// Landfall front end: one agent session per tab, streamed over SSE.
"use strict";

const CATS = ["none", "affected", "minor", "major", "destroyed"];
const LABEL = { none: "No visible damage", affected: "Affected", minor: "Minor", major: "Major", destroyed: "Destroyed" };
const STEP = {
  locate_imagery: "Finding storm imagery",
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
const state = { sid: null, area: null, box: null, busy: false, drawing: false, layer: "after", features: {}, steps: [], spec: null };

// ---------------------------------------------------------------- map
const map = L.map("map", { zoomControl: false }).setView([29.1375, -83.0355], 16);
L.control.zoom({ position: "bottomright" }).addTo(map);
const streets = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 21, maxNativeZoom: 19, attribution: "© OpenStreetMap contributors",
});
let after = null;
const outlines = L.geoJSON(null, { style: styleFor, onEachFeature: bindFeature }).addTo(map);
let boxLayer = null;

function setFlight(flight) {
  if (after) map.removeLayer(after);
  after = flight
    ? L.tileLayer(`https://stormscdn.ngs.noaa.gov/${flight}/{z}/{x}/{y}`, {
        maxZoom: 21, maxNativeZoom: 20, attribution: `NOAA Emergency Response Imagery (${flight})`,
      })
    : null;
  applyLayer();
}
async function setOverlay(area) {
  // storms outside NOAA's coverage: show the after-storm mosaic Landfall built
  const r = await fetch(`/api/areas/${area}/after`);
  if (!r.ok) return;
  const o = await r.json();
  if (after) map.removeLayer(after);
  after = L.imageOverlay(`${o.url}?t=${Date.now()}`, o.bounds, { attribution: "Maxar Open Data (CC BY-NC 4.0)" });
  applyLayer();
}
function applyLayer() {
  if (state.layer === "after" && after) {
    map.removeLayer(streets);
    after.addTo(map);
  } else {
    if (after) map.removeLayer(after);
    streets.addTo(map);
  }
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
  if (fit && gj.features.length) map.fitBounds(outlines.getBounds(), { padding: [30, 30] });
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
  $("draw").textContent = on ? "Drag on the map…" : "Draw an area";
  map.dragging[on ? "disable" : "enable"]();
  map.getContainer().style.cursor = on ? "crosshair" : "";
}
map.on("mousedown", (e) => {
  if (!state.drawing) return;
  start = e.latlng;
  if (boxLayer) map.removeLayer(boxLayer);
  boxLayer = L.rectangle([start, start], { color: css("--accent"), weight: 2, fillOpacity: 0.08 }).addTo(map);
});
map.on("mousemove", (e) => {
  if (state.drawing && start) boxLayer.setBounds([start, e.latlng]);
});
map.on("mouseup", (e) => {
  if (!state.drawing || !start) return;
  const b = L.latLngBounds(start, e.latlng);
  start = null;
  setDrawing(false);
  const w = b.getWest(), s = b.getSouth(), ea = b.getEast(), n = b.getNorth();
  const km2 = ((ea - w) * 111.32 * Math.cos(((s + n) / 2) * Math.PI / 180)) * ((n - s) * 110.57);
  if (km2 < 0.005) { map.removeLayer(boxLayer); return; }
  state.box = [w, s, ea, n];
  $("context").textContent = `Box drawn: ${km2.toFixed(2)} km². Send to assess it.`;
  if (!$("input").value.trim()) $("input").value = "Assess the hurricane damage in this area.";
  $("input").focus();
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
  if (failed) stepLine(result.error);
  current = null;
}

function describeProgress(ev) {
  switch (ev.event) {
    case "buildings": return `${ev.found} footprints in the box; assessing ${ev.kept}`;
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
  const msg = div("msg user");
  msg.textContent = text;
  if (state.box) msg.insertAdjacentHTML("beforeend", `<span class="boxnote">+ the box on the map</span>`);
  add(msg);
  $("input").value = "";
  $("context").textContent = "";
  setBusy(true);
  const body = { text, box: state.box, storm: state.box ? $("storm").value : null };
  if (state.box) { outlines.clearLayers(); state.features = {}; }
  state.box = null;
  const r = await fetch(`/api/sessions/${state.sid}/turn`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) { add(div("msg error", esc((await r.json()).detail || r.statusText))); setBusy(false); }
}
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
  const cat = a ? a.category : p.category;
  const trace = (a && a.trace) || [];
  const rows = [
    ["Confidence", a && a.confidence != null ? a.confidence.toFixed(2) : "–"],
    ["Decided by", a ? a.decided_by : "–"],
    ["Structure missing", a && a.structure_loss != null ? `${Math.round(a.structure_loss * 100)}%` : "not measured"],
    ["Water above ground", a && a.flood_ft != null ? `${a.flood_ft.toFixed(1)} ft` : "unknown"],
    ...(a && a.demolished ? [["Note", "gone before the storm (demolished)"]] : []),
    ["Debris in yard", a && a.debris_share ? `${Math.round(a.debris_share * 100)}%` : "none found"],
    ...(a && a.record ? [["First floor", a.record.basis || "unknown"]] : []),
    ...(a && a.need && a.need.water_inside_ft[1] > 0 ? [["Water inside", `${a.need.water_inside_ft[0].toFixed(1)}–${a.need.water_inside_ft[1].toFixed(1)} ft (estimate)`]] : []),
    ...(a && a.need ? [["Households", a.need.households ? `${a.need.households}${a.need.likely_displaced ? ", likely displaced" : a.need.possibly_displaced ? ", possibly displaced" : ""}` : "none (not a home)"]] : []),
    ...(a && a.cost ? [["Repair cost", `${usd(a.cost.cost_low_usd)}–${usd(a.cost.cost_high_usd)} of ${usd(a.cost.value_usd)}`]] : []),
    ...(a && a.cost && a.cost.substantial_damage_possible ? [["FEMA 50% rule", a.cost.substantial_damage_likely ? "likely substantially damaged" : "possibly substantially damaged"]] : []),
  ];
  $("card-body").innerHTML = `
    <h2>${esc(id)} ${cat ? `<span class="pill ${cat}" style="--c:var(--${cat})">${LABEL[cat]}</span>` : ""}${a && a.needs_human ? `<span class="pill" style="--c:var(--review)">Field check</span>` : ""}</h2>
    <img src="/api/areas/${state.area}/pair/${id}.png" alt="Before and after aerial photos of ${esc(id)}">
    <div class="cap"><span>Before (${esc(a && a.photos ? a.photos.before.split(" (")[0] : "NAIP")})</span><span>After (${esc(a && a.photos ? a.photos.after.split(" (")[0] : "NOAA")})</span></div>
    <p>${a ? esc(a.evidence) : "Not drafted yet."}</p>
    <dl class="kv">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
    ${a ? `<p><a class="packet" href="/api/areas/${state.area}/packet/${id}" target="_blank" rel="noopener">Evidence packet for a claim (printable)</a></p>` : ""}
    ${trace.length ? `<div class="trace"><b>How the call was made</b>${trace.map(traceRow).join("")}</div>` : ""}`;
  $("card").hidden = false;
}
function usd(x) {
  if (x == null) return "?";
  return x >= 1e6 ? `$${(x / 1e6).toFixed(1)}M` : `$${Math.round(x / 1e3)}k`;
}
function traceRow(t) {
  if (t.model === "satellite rule") return `<div class="t">Satellite photos: damage calls go to a field check.</div>`;
  if (t.model === "flood rule") return `<div class="t">Flood rule raised ${esc(t.raised_from)} to ${esc(t.to)}.</div>`;
  if (t.error) return `<div class="t">${esc(t.model)}: unreadable answer, asked again.</div>`;
  const a = t.answer || {};
  const obj = (t.verifier || []).map((o) => `<div class="obj">Checker: ${esc(o)}</div>`).join("");
  return `<div class="t"><div><b>${esc(String(t.model).split("/").pop())}</b> said ${esc(String(a.category || "").toLowerCase())} (${a.confidence})</div>${obj || "<div>Checker: no objection.</div>"}</div>`;
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
async function boot() {
  legend();
  const storms = await (await fetch("/api/storms")).json();
  const group = (label, rows) => `<optgroup label="${label}">${rows.map((s) => `<option value="${s.key}">${esc(s.name)} (${esc(s.where)}, ${s.landfall.slice(0, 4)})</option>`).join("")}</optgroup>`;
  $("storm").innerHTML = group("United States: NOAA aerial photos", storms.filter((s) => s.imagery === "noaa")) + group("Worldwide: Maxar satellite photos", storms.filter((s) => s.imagery === "maxar"));
  const areas = await (await fetch("/api/areas")).json();
  const params = new URLSearchParams(location.search);
  const at = params.get("at");
  const pick = at ? null : areas.find((a) => a.name === params.get("area")) || areas.find((a) => a.name === "cedar_key" && a.assessed) || null;
  const s = await (await fetch("/api/sessions", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ area: pick && pick.assessed ? pick.name : null }) })).json();
  state.sid = s.id;
  state.area = s.area;
  if (pick) {
    state.spec = pick.spec;
    if (pick.spec) $("storm").value = pick.spec.storm;
    state.flight = pick.spec && pick.spec.noaa_flight;
    setFlight(state.flight);
    await loadBuildings(true);
    if (!state.flight) setOverlay(pick.name);
  } else {
    applyLayer();
  }
  if (at) { const [la, lo, z] = at.split(",").map(Number); map.setView([la, lo], z || 17); }
  const ev = new EventSource(`/api/sessions/${state.sid}/events`);
  ev.onmessage = (m) => handle(JSON.parse(m.data));
  if (pick && pick.assessed) {
    agentMessage(
      `I've drafted damage categories for **${title(pick.name)}**. Ask me about it, or draw a new area on the map and I'll assess that too.`
    );
    suggestions(["Which houses should a field team check first?", "Write the draft PDA summary", "Which flooded houses could really be Major?"]);
  } else {
    agentMessage("Draw an area on the map after a hurricane, and I'll draft FEMA damage categories for every building in it.");
  }
}
boot();
function title(name) { return name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()); }
