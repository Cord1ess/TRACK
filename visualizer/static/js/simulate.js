/* The Simulate tab: run the city-scale algorithms from the map and show them.

   The page never runs an algorithm itself. "Run" asks the dev server to start
   algorithms/simulate.py, and the page then reads two kinds of file:

     /api/sim            what is running now (algorithm, message, done of total)
     /sim/<result>.json  a finished result: history, forecast, city, changes-<area>,
                         diversion (its player lives in diversion.js)

   On GitHub Pages there is no server, so the site shows the results that were
   published with it and the Run buttons are hidden.

   While a run goes the page also reads its live snapshots (live-routes,
   live-roads, live-csp) about twice a second and draws them: the routes A* has
   just found, how full the roads are after each batch lands, and the road
   layout the CSP is scoring with the routes the area's traffic takes under it.

   Two map layers come from the results:

     city replay   the roads the city simulation's trips used, coloured by how
                   full they are at each step: before, all at once, batch 1..N
     road changes  the area's circle and its candidate streets, with the
                   changes of the chosen layout highlighted */

import { $, RAMP, attempt, clock12, dayLabel, escapeHtml, map, state } from "./core.js";
import { SITE, STATIC, getManifest, loadManifest } from "./api.js";
import { hasLayer, hasSource, registerLayers, setLayout, setPaint, ensureLayers } from "./mapkit.js";
import { liveEnded, loadDiversion, showLive } from "./diversion.js";

const sim = state.sim = {
  results: {},          // name -> parsed JSON
  modified: {},         // name -> mtime the loaded copy came from
  running: false,
  task: "",
  step: 1,              // replay step shown: 0 before, 1 all at once, 2.. batches
  playing: 0,           // replay timer
  area: "farmgate",
  layout: 0,            // which of the area's best layouts is highlighted
  showCity: true,
  live: { stamps: {}, routes: null, roads: null, csp: null, framed: false, version: 0 },
};

const CITY = { src: "gj:sim-city", line: "gj:sim-city:line" };
const CHANGE = { src: "gj:sim-change", line: "gj:sim-change:line" };
const LIVE_ROADS = { src: "gj:sim-live-roads", line: "gj:sim-live-roads:line" };
const LIVE_ROUTES = { src: "gj:sim-live-routes", line: "gj:sim-live-routes:line" };
const EMPTY = { type: "FeatureCollection", features: [] };
const DHAKA_BOUNDS = [[90.326774, 23.711736], [90.499678, 23.902086]];   // the capture area
const fmt = (n, digits = 0) => Number(n).toLocaleString(undefined,
  { minimumFractionDigits: digits, maximumFractionDigits: digits });
const pct = (x) => `${(100 * x).toFixed(1)}%`;

/* ── fetching ─────────────────────────────────────────────────────────────── */

async function fetchResult(name) {
  try {
    if (STATIC) {
      const m = getManifest() || await loadManifest();
      const path = m && m.simulation && m.simulation[name];
      if (!path) return null;
      return await (await fetch(`${SITE}${path}`)).json();
    }
    const r = await fetch(`/sim/${name}.json`, { cache: "no-store" });
    return r.ok ? await r.json() : null;
  } catch { return null; }
}

async function refreshResults(status, finished = "") {
  const names = STATIC
    ? Object.keys((getManifest() || await loadManifest() || {}).simulation || {})
    : Object.keys(status.results || {});
  for (const name of names) {
    const stamp = STATIC ? 1 : status.results[name].modified;
    if (sim.modified[name] === stamp) continue;
    const data = await fetchResult(name);
    if (!data) continue;
    sim.results[name] = data;
    sim.modified[name] = stamp;
    if (name === "city") sim.step = Math.min(sim.step, data.replay.steps.length - 1);
    if (name === "diversion") loadDiversion(data, finished === "diversion");
    if (name.startsWith("changes-")) sim.layout = 0;
  }
  renderAll();
  ensureLayers();
  applySimLayers();
}

/* ── running ──────────────────────────────────────────────────────────────── */

async function run(body) {
  try {
    const r = await fetch("/api/sim/run", { method: "POST", body: JSON.stringify(body),
      headers: { "Content-Type": "application/json" } });
    const answer = await r.json();
    if (!r.ok) { $("simStatus").innerHTML = `<span class="warn">${escapeHtml(answer.error)}</span>`; return; }
    sim.running = true;
    sim.task = body.task;
    renderButtons();
    poll();
  } catch (err) {
    $("simStatus").innerHTML = `<span class="warn">could not reach the server</span>`;
  }
}

let pollTimer = 0;
async function poll() {
  clearTimeout(pollTimer);
  if (STATIC) return;
  let status = null;
  try { status = await (await fetch("/api/sim", { cache: "no-store" })).json(); } catch { /* retry */ }
  if (status) {
    const was = sim.running;
    sim.running = status.running;
    sim.task = status.task || sim.task;
    renderStatus(status);
    renderButtons(status.can_run);
    if (status.running) await refreshLive(status.live || {});
    else if (was || sim.live.version) clearLive();
    if (!status.running) await refreshResults(status, was ? sim.task : "");
    else if (!was) renderButtons(status.can_run);
  }
  pollTimer = setTimeout(poll, sim.running ? 600 : 5000);
}

function renderStatus(status) {
  const p = status.progress;
  const bar = $("simBar");
  if (!p) {
    $("simStatus").textContent = "Nothing running. Pick a step below and press Run.";
    bar.style.width = "0";
    return;
  }
  const share = p.total ? Math.min(1, p.done / p.total) : (p.running ? 0.05 : 1);
  bar.style.width = `${(100 * share).toFixed(1)}%`;
  const what = p.algorithm ? `<b>${escapeHtml(p.algorithm)}</b> · ` : "";
  const time = `${Math.round(p.elapsed_s || 0)} s`;
  if (p.running) {
    $("simStatus").innerHTML = `${escapeHtml(p.task)}: ${what}${escapeHtml(p.message)} · ${time}`;
  } else if (p.ok) {
    $("simStatus").innerHTML = `Last run: ${escapeHtml(p.message)}`;
  } else {
    $("simStatus").innerHTML = `<span class="warn">${escapeHtml(p.task)} failed: ${escapeHtml(p.message)}</span>`;
  }
}

function renderButtons(canRun = true) {
  for (const id of ["simRunForecast", "simRunCity", "simRunChanges", "simRunDiversion"]) {
    const b = $(id);
    if (!b) continue;
    b.hidden = STATIC || !canRun;
    b.disabled = sim.running;
  }
}

/* ── live: what the running simulation is doing right now ─────────────────── */

async function refreshLive(stamps) {
  let changed = false;
  for (const [name, stamp] of Object.entries(stamps)) {
    if (sim.live.stamps[name] === stamp) continue;
    const data = await fetchResult(name);
    if (!data) continue;                                  // mid-write: the next poll gets it
    sim.live[name.slice("live-".length)] = data;
    sim.live.stamps[name] = stamp;
    changed = true;
  }
  if (!changed) return;
  sim.live.version += 1;
  if (sim.live.diversion) { await showLive(sim.live.diversion, $("simLive")); return; }
  renderLive();
  frameLive();
  applySimLayers();
}

function clearLive() {
  if (sim.live.diversion) liveEnded();
  sim.live = { stamps: {}, routes: null, roads: null, csp: null, framed: false, version: 0 };
  $("simLive").innerHTML = "";
  applySimLayers();
}

const liveOn = () => sim.running && sim.live.version > 0;
// The city replay and the road-change drawing are results to look at in this
// tab. Left on the map elsewhere they covered the traffic, and the Google and
// TRACK switches could not turn them off.
const simTabOpen = () => !!document.querySelector('.tabpanel[data-panel="simulate"].on');
const signed = (x) => `${x > 0 ? "+" : ""}${x.toFixed(2)}%`;

function renderLive() {
  const box = $("simLive");
  const { roads, routes, csp } = sim.live;
  if (csp) {
    const best = csp.best.map((b) => `<li><b class="${b.change_pct < 0 ? "best" : ""}">${signed(b.change_pct)}</b>
        ${b.describe.map(escapeHtml).join(" + ")}</li>`).join("");
    box.innerHTML = `
      <div class="phase"><span class="dot"></span>CSP: scoring layout ${fmt(csp.scored)} of ${fmt(csp.total)} with A*</div>
      <div class="testing">Testing: <b>${csp.describe.map(escapeHtml).join(" + ")}</b><br>
        ${fmt(csp.routes.length)} routes through ${escapeHtml(csp.area.name)} ·
        <b class="${csp.change_pct < 0 ? "best" : ""}">${signed(csp.change_pct)}</b> vehicle-hours
        against today (${fmt(csp.baseline_vehicle_hours)})</div>
      <p class="note"><b>Best so far</b></p><ul>${best}</ul>`;
    return;
  }
  if (!roads && !routes) { box.innerHTML = ""; return; }
  const phase = (routes && routes.phase) || (roads && roads.phase) || "";
  const steps = (roads && roads.steps) || [];
  const rows = steps.map((m, i) => `<tr class="${i === steps.length - 1 ? "now" : ""}"><td>${escapeHtml(m.label)}</td>
      <td>${fmt(m.vehicle_hours)}</td><td>${fmt(m.roads_over_capacity)}</td>
      <td>${m.mean_trip_minutes ? m.mean_trip_minutes.toFixed(1) : "—"}</td></tr>`).join("");
  box.innerHTML = `
    <div class="phase"><span class="dot"></span>${escapeHtml(phase)}</div>
    <p class="note">${fmt(routes ? routes.found : 0)} routes found by A* so far. Blue lines: the newest
      routes. Roads: how full they are with the trips added so far.</p>
    <table><tr><th></th><th>Vehicle-hours</th><th>Over capacity</th><th>Trip min</th></tr>${rows}</table>`;
}

function frameLive() {
  if (sim.live.framed) return;
  if (sim.live.csp) {
    const a = sim.live.csp.area;
    map.flyTo({ center: [a.lon, a.lat], zoom: 14.6, duration: 900 });
  } else {
    map.fitBounds(DHAKA_BOUNDS, { padding: 40, duration: 900 });
  }
  sim.live.framed = true;
}

function liveRoadsData() {
  const r = sim.live.roads;
  if (!r || !liveOn()) return EMPTY;
  return { type: "FeatureCollection", features: r.geometry.map((coords, i) => ({
    type: "Feature", properties: { lv: Number(r.levels[i]) }, geometry: { type: "LineString", coordinates: coords } })) };
}

function liveRoutesData() {
  if (!liveOn()) return EMPTY;
  const list = sim.live.csp ? sim.live.csp.routes : (sim.live.routes ? sim.live.routes.routes : []);
  const n = list.length;
  return { type: "FeatureCollection", features: list.filter((c) => c.length > 1).map((coords, i) => ({
    // city: newest route brightest; CSP: every route of the layout being scored, equally
    type: "Feature", properties: { fresh: sim.live.csp ? 0.6 : (n > 1 ? i / (n - 1) : 1) },
    geometry: { type: "LineString", coordinates: coords } })) };
}

/* ── the three result panels ──────────────────────────────────────────────── */

function renderForecast() {
  const box = $("simForecast");
  const f = sim.results.forecast;
  const h = sim.results.history;
  if (!f) {
    box.innerHTML = h ? `<p class="note">${fmt(h.captures)} captures ready, ${escapeHtml(h.from)} to ${escapeHtml(h.to)}.</p>` : "";
    return;
  }
  const ev = f.evaluation;
  const models = Object.entries(ev.models);
  const top = Math.max(...models.map(([, m]) => m.right_level));
  const rows = models.map(([name, m]) => `<tr><td>${escapeHtml(name)}</td>
      <td class="${m.right_level === top ? "best" : ""}">${pct(m.right_level)}</td><td>${pct(m.too_green)}</td></tr>`).join("");
  const scen = f.scenarios.scenarios.map((s) => `<div class="scen"><b>${escapeHtml(s.name)}</b> ·
      ${pct(s.share)} of captures · mostly ${s.common_hours.map(escapeHtml).join(", ")} ·
      ${pct(s.weekend_share)} weekend</div>`).join("");
  box.innerHTML = `
    <table><tr><th>Forecaster</th><th>Right level</th><th>Too green</th></tr>${rows}</table>
    <p class="note">Learned from ${fmt(ev.train.captures)} captures (${escapeHtml(ev.train.from)} to
      ${escapeHtml(ev.train.to)}), tested on ${fmt(ev.test.captures)} later ones (${escapeHtml(ev.test.from)}
      to ${escapeHtml(ev.test.to)}). Scored on the roads Google painted. "Too green" is the error that
      would send traffic into a jam.</p>
    <p class="note"><b>K-means scenarios</b> (k = ${f.scenarios.k}), lightest first:</p>${scen}`;
}

function renderCity() {
  const box = $("simCity");
  const c = sim.results.city;
  if (!c) { box.innerHTML = ""; return; }
  const r = c.results;
  const row = (label, m, cls = "") => `<tr class="${cls}"><td>${label}</td><td>${fmt(m.vehicle_hours)}</td>
      <td>${fmt(m.roads_over_capacity)}</td><td>${m.mean_trip_minutes ? m.mean_trip_minutes.toFixed(1) : "—"}</td></tr>`;
  const saved = r.all_at_once.vehicle_hours - r.in_batches.vehicle_hours;
  const added = r.all_at_once.vehicle_hours - r.before.vehicle_hours;
  const steps = c.replay.steps.map((s, i) => `<button data-step="${i}" class="${i === sim.step ? "on" : ""}">${escapeHtml(s)}</button>`).join("");
  box.innerHTML = `
    <p class="note"><b>${escapeHtml(c.when.label)}</b>, forecast with ${escapeHtml(c.model)} · ${fmt(c.trips)} trips
      × ${fmt(c.vehicles_per_trip)} vehicles = ${fmt(c.vehicles)} vehicles · ${fmt(c.searches)} A* searches
      in ${fmt(c.seconds)} s</p>
    <table><tr><th></th><th>Vehicle-hours</th><th>Over capacity</th><th>Trip min</th></tr>
      ${row("Before the trips", r.before)}${row("All at once", r.all_at_once)}${row("In batches", r.in_batches, "best")}</table>
    <p class="note">Routing in batches saves <b>${fmt(saved)}</b> vehicle-hours
      (${added > 0 ? `${(100 * saved / added).toFixed(0)}% of the delay the new trips add` : "—"}).
      The trips are synthetic: Google shows how busy roads are, not where people travel.</p>
    <label class="row"><span>Show on the map</span>
      <label class="switch sm"><input type="checkbox" id="simShowCity" ${sim.showCity ? "checked" : ""}><span></span></label></label>
    <div class="steps" id="simSteps">${steps}<button data-play="1">${sim.playing ? "Pause" : "Play"}</button></div>`;
  $("simShowCity").onchange = (e) => { sim.showCity = e.target.checked; applySimLayers(); };
  box.querySelectorAll("#simSteps button[data-step]").forEach((b) => b.onclick = () => {
    stopReplay(); setStep(Number(b.dataset.step));
  });
  box.querySelector("#simSteps button[data-play]").onclick = toggleReplay;
}

function renderChanges() {
  const box = $("simChanges");
  const ch = sim.results[`changes-${sim.area}`];
  if (!ch) { box.innerHTML = ""; return; }
  const s = ch.csp;
  const layouts = ch.best.map((b, i) => `<div class="layout ${i === sim.layout ? "on" : ""}" data-i="${i}">
      <b class="${b.change_pct < 0 ? "best" : ""}">${b.change_pct > 0 ? "+" : ""}${b.change_pct.toFixed(2)}%</b>
      vehicle-hours<ul>${b.changes.map((c) => `<li>${escapeHtml(c.text)}</li>`).join("")}</ul></div>`).join("");
  box.innerHTML = `
    <p class="note"><b>${escapeHtml(ch.area.name)}</b>, ${escapeHtml(ch.when.label)} · ${s.variables} options
      (${ch.options.filter((o) => o.kind === "street").length} side streets,
      ${ch.options.filter((o) => o.kind === "u-turn").length} U-turn gaps)</p>
    <table><tr><th>CSP search</th><th></th></tr>
      <tr><td>Choices tried</td><td>${fmt(s.choices_tried)}</td></tr>
      <tr><td>Values crossed out by forward checking</td><td>${fmt(s.values_crossed_out)}</td></tr>
      <tr><td>Dead ends backtracked</td><td>${fmt(s.dead_ends)}</td></tr>
      <tr><td>Valid layouts</td><td>${fmt(s.valid_layouts)}</td></tr>
      <tr><td>Scored with A*</td><td>${fmt(ch.layouts_scored)}</td></tr>
      <tr><td>Better than today</td><td>${fmt(ch.layouts_better)}</td></tr></table>
    <p class="note">${fmt(ch.trips_through)} journeys (${fmt(ch.vehicles_through)} vehicles) cross the area;
      today they take ${fmt(ch.baseline_vehicle_hours)} vehicle-hours there. Rules:
      ${ch.rules.map(escapeHtml).join("; ")}.</p>
    ${layouts || '<p class="note">No valid layout beats today.</p>'}`;
  box.querySelectorAll(".layout").forEach((el) => el.onclick = () => {
    sim.layout = Number(el.dataset.i);
    renderChanges();
    applySimLayers();
    flyToArea();
  });
}

function renderAll() {
  renderForecast();
  renderCity();
  renderChanges();
}

/* ── the map ──────────────────────────────────────────────────────────────── */

function cityData() {
  const c = sim.results.city;
  if (!c) return null;
  return { type: "FeatureCollection", features: c.replay.geometry.map((coords, i) => ({
    type: "Feature", properties: { lv: c.replay.levels[i] },
    geometry: { type: "LineString", coordinates: coords } })) };
}

function cityColour() {
  // each road carries one digit per replay step; read the one for this step
  const level = ["slice", ["get", "lv"], sim.step, sim.step + 1];
  return ["match", level, "0", RAMP[0], "1", RAMP[1], "2", RAMP[2], "3", RAMP[3], "#9e9e9e"];
}

const CHANGE_COLOURS = { "closed": "#e5484d", "one-way forward": "#7c3aed", "one-way backward": "#7c3aed" };

function changeData() {
  const live = liveOn() && sim.live.csp;
  const ch = live ? { area: sim.live.csp.area, options: sim.live.csp.options } : sim.results[`changes-${sim.area}`];
  if (!ch) return null;
  const chosen = {};
  if (live) Object.assign(chosen, sim.live.csp.layout);
  else for (const c of (ch.best[sim.layout] || { changes: [] }).changes) chosen[c.option] = c.value;
  const ring = [];
  const { lon, lat, change_radius_m: r } = ch.area;
  for (let a = 0; a <= 64; a++) {
    const t = (2 * Math.PI * a) / 64;
    ring.push([lon + (r / (111320 * Math.cos((lat * Math.PI) / 180))) * Math.sin(t), lat + (r / 110540) * Math.cos(t)]);
  }
  const features = [{ type: "Feature", properties: { colour: "#6b7280", width: 1.5 },
    geometry: { type: "LineString", coordinates: ring } }];
  for (const o of ch.options) {
    const value = chosen[o.key];
    const colour = value ? (o.kind === "u-turn" ? "#f59e0b" : CHANGE_COLOURS[value]) : "#94a3b8";
    features.push({ type: "Feature", properties: { colour, width: value ? 7 : 3 },
      geometry: { type: "LineString", coordinates: o.geometry } });
  }
  return { type: "FeatureCollection", features };
}

function addLayers() {
  let added = false;
  const city = cityData();
  if (city && !hasSource(CITY.src)) { map.addSource(CITY.src, { type: "geojson", data: city }); added = true; }
  if (city && !hasLayer(CITY.line)) {
    map.addLayer({ id: CITY.line, type: "line", source: CITY.src,
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": cityColour(),
               "line-width": ["interpolate", ["linear"], ["zoom"], 10, 1.2, 14, 3.2, 17, 6] } });
    added = true;
  }
  const change = changeData();
  if (change && !hasSource(CHANGE.src)) { map.addSource(CHANGE.src, { type: "geojson", data: change }); added = true; }
  if (change && !hasLayer(CHANGE.line)) {
    map.addLayer({ id: CHANGE.line, type: "line", source: CHANGE.src,
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": ["get", "colour"], "line-width": ["get", "width"], "line-opacity": 0.95 } });
    added = true;
  }
  if (!hasSource(LIVE_ROADS.src)) { map.addSource(LIVE_ROADS.src, { type: "geojson", data: EMPTY }); added = true; }
  if (!hasLayer(LIVE_ROADS.line)) {
    map.addLayer({ id: LIVE_ROADS.line, type: "line", source: LIVE_ROADS.src,
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": ["match", ["get", "lv"], 0, RAMP[0], 1, RAMP[1], 2, RAMP[2], 3, RAMP[3], "#9e9e9e"],
               "line-width": ["interpolate", ["linear"], ["zoom"], 10, 1.4, 14, 3.4, 17, 6] } });
    added = true;
  }
  if (!hasSource(LIVE_ROUTES.src)) { map.addSource(LIVE_ROUTES.src, { type: "geojson", data: EMPTY }); added = true; }
  if (!hasLayer(LIVE_ROUTES.line)) {
    map.addLayer({ id: LIVE_ROUTES.line, type: "line", source: LIVE_ROUTES.src,
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#0a84ff",
               "line-width": ["interpolate", ["linear"], ["zoom"], 10, 1.6, 14, 3, 17, 5],
               "line-opacity": ["interpolate", ["linear"], ["get", "fresh"], 0, 0.25, 1, 0.95] } });
    added = true;
  }
  return added;
}

let shownCity = null;
let shownChange = "";
let shownLive = -1;
function applySimLayers() {
  const city = sim.results.city;
  if (hasSource(CITY.src) && city && shownCity !== city) {
    attempt("sim city data", () => map.getSource(CITY.src).setData(cityData()));
    shownCity = city;
  }
  setPaint(CITY.line, "line-color", cityColour());
  setLayout(CITY.line, "visibility", sim.showCity && city && !liveOn() && simTabOpen() ? "visible" : "none");
  if (hasSource(LIVE_ROADS.src) && shownLive !== sim.live.version) {
    attempt("sim live roads", () => map.getSource(LIVE_ROADS.src).setData(liveRoadsData()));
    attempt("sim live routes", () => map.getSource(LIVE_ROUTES.src).setData(liveRoutesData()));
    shownLive = sim.live.version;
  }
  const key = liveOn() && sim.live.csp ? `live|${sim.live.version}`
    : `${sim.area}|${sim.layout}|${sim.modified[`changes-${sim.area}`] || ""}`;
  const data = changeData();
  if (hasSource(CHANGE.src) && data && key !== shownChange) {
    attempt("sim change data", () => map.getSource(CHANGE.src).setData(data));
    shownChange = key;
  }
  setLayout(CHANGE.line, "visibility", data && simTabOpen() ? "visible" : "none");
}

function flyToArea() {
  const ch = sim.results[`changes-${sim.area}`];
  if (ch) map.flyTo({ center: [ch.area.lon, ch.area.lat], zoom: 15, duration: 900 });
}

/* ── replay ───────────────────────────────────────────────────────────────── */

function setStep(i) {
  const c = sim.results.city;
  if (!c) return;
  sim.step = Math.max(0, Math.min(i, c.replay.steps.length - 1));
  document.querySelectorAll("#simSteps button[data-step]").forEach((b) =>
    b.classList.toggle("on", Number(b.dataset.step) === sim.step));
  applySimLayers();
}

function stopReplay() {
  clearInterval(sim.playing);
  sim.playing = 0;
  const b = document.querySelector("#simSteps button[data-play]");
  if (b) b.textContent = "Play";
}

function toggleReplay() {
  if (sim.playing) { stopReplay(); return; }
  const c = sim.results.city;
  if (!c) return;
  setStep(0);
  sim.playing = setInterval(() => {
    if (sim.step >= c.replay.steps.length - 1) { stopReplay(); return; }
    setStep(sim.step + 1);
  }, 900);
  document.querySelector("#simSteps button[data-play]").textContent = "Pause";
}

/* ── wiring ───────────────────────────────────────────────────────────────── */

export async function startSimulate() {
  const value = (id) => $(id).value;
  $("simRunForecast").onclick = () => run({ task: "forecast" });
  $("simRunCity").onclick = () => run({ task: "city", day: value("simDay"), time: value("simTime"),
    trips: Number(value("simTrips")), batches: Number(value("simBatches")),
    vehicles: Number(value("simVehicles")), model: value("simModel") });
  $("simRunChanges").onclick = () => run({ task: "changes", area: value("simArea"),
    max_changes: Number(value("simMaxChanges")) });
  $("simRunDiversion").onclick = () => run({ task: "diversion",
    capture: state.capture ? state.capture.name : "", minutes: Number(value("simDivMinutes")) });
  // the run starts from whatever the timeline shows: say which moment that is
  setInterval(() => {
    const c = state.capture;
    const at = c ? Date.parse(c.captured_utc) : NaN;
    const text = isNaN(at) ? "pick a capture on the timeline" : `${dayLabel(at)} · ${clock12(at)}`;
    if ($("simDivStart").textContent !== text) $("simDivStart").textContent = text;
  }, 1000);
  $("simArea").onchange = () => {
    sim.area = value("simArea");
    sim.layout = 0;
    renderChanges();
    applySimLayers();
    flyToArea();
  };
  if (STATIC) {
    $("simRunDiversion").closest(".group").hidden = true;      // its frames need the dev server
    renderButtons(false);
    $("simStatus").textContent = "Results published with the site. Running a simulation needs the dev server.";
    await refreshResults({});
    return;
  }
  poll();
}

registerLayers({ build: addLayers, apply: applySimLayers });
window.addEventListener("tabchange", () => applySimLayers());
