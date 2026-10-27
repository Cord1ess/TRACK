/* The diversion player: two timelines on one clock.

   A diversion run starts at one capture and follows the real captures for an
   hour or two (algorithms/track_algos/simulation/diversion.py). Every step has
   two versions of the city:

     Actual       what Google recorded: the capture's own frame
     With TRACK   the same traffic steered around the jams: a frame the server
                  builds from the step's levels (/api/sim/frame)

   The player moves the main timeline to the step's capture, so the clock is
   always a real moment, and the Actual / With TRACK switch decides which
   colours the roads show (frames.js setOverride). A highlight layer outlines
   the roads where the two timelines differ at that step. */

import { $, RAMP, attempt, clock12, dayLabel, escapeHtml, map, state } from "./core.js";
import { clearOverride, repaint, setOverride } from "./frames.js";
import { setStop } from "./timeline.js";
import { hasLayer, hasSource, registerLayers, setLayout, setPaint } from "./mapkit.js";

const div = state.diversion = {
  result: null,       // the finished run (diversion.json)
  step: 0,            // the step shown
  mode: "track",      // "actual" or "track"
  playing: 0,         // play timer
  active: false,      // the player owns the map's colours
  highlight: true,
  frames: new Map(),  // step -> {v, codes}
};

const SRC = "gj:sim-div";
const CASING = "gj:sim-div:casing";
const LINE = "gj:sim-div:line";
const EMPTY = { type: "FeatureCollection", features: [] };
const LEVELS = ["green", "yellow", "red", "dark red"];
const fmt = (n) => Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 });
const timeOf = (name) => Date.parse(name.replace(/^dhaka-(\d{4}-\d\d-\d\d)T(\d\d)(\d\d)Z$/, "$1T$2:$3:00Z"));

/* ── frames ───────────────────────────────────────────────────────────────── */

async function trackFrame(step) {
  if (div.frames.has(step)) return div.frames.get(step);
  try {
    const r = await fetch(`/api/sim/frame?run=diversion&step=${step}`, { cache: "no-store" });
    if (!r.ok) return null;
    const f = await r.json();
    div.frames.set(step, f);
    return f;
  } catch { return null; }
}

async function showCapture(name) {
  // the main timeline follows the player, so its clock is the step's real moment
  const tl = state.timeline;
  const i = tl.stops.findIndex((s) => s.name === name);
  if (i >= 0 && i !== tl.index) await setStop(i, false);
  else repaint();
}

/* ── the player ───────────────────────────────────────────────────────────── */

export function loadDiversion(result, finishedNow) {
  div.result = result;
  div.frames.clear();
  div.step = Math.min(div.step, result.steps.length - 1);
  if (hasSource(SRC)) attempt("diversion data", () => map.getSource(SRC).setData(highlightData()));
  renderPlayer();
  if (finishedNow) { div.mode = "track"; goStep(result.steps.length - 1); }
}

export async function goStep(i) {
  const d = div.result;
  if (!d) return;
  div.step = Math.max(0, Math.min(i, d.steps.length - 1));
  div.active = true;
  const name = d.steps[div.step].capture;
  if (div.mode === "track") {
    const f = await trackFrame(div.step);
    if (!f || !setOverride(name, f.codes, f.v)) clearOverride();
  } else {
    clearOverride();
  }
  await showCapture(name);
  renderPlayer();
  applyLayers();
}

function stop() {
  clearInterval(div.playing);
  div.playing = 0;
}

function togglePlay() {
  if (div.playing) { stop(); renderPlayer(); return; }
  const d = div.result;
  if (!d) return;
  if (div.step >= d.steps.length - 1) div.step = 0;
  let busy = false;
  div.playing = setInterval(async () => {
    if (busy) return;                                  // never queue steps behind a slow one
    if (div.step >= d.steps.length - 1) { stop(); renderPlayer(); return; }
    busy = true;
    await goStep(div.step + 1);
    busy = false;
  }, 700);
  renderPlayer();
}

function leave() {
  stop();
  div.active = false;
  clearOverride();
  repaint();
  renderPlayer();
  applyLayers();
}

function clockOf(step, d) {
  const at = timeOf(step.capture);
  const r = d.rounds_per_capture;
  const part = step.round ? ` <small>round ${step.round} of ${r}</small>` : " <small>start</small>";
  return `${escapeHtml(dayLabel(at))} · ${escapeHtml(clock12(at))}${part}`;
}

function compareTable(step) {
  const a = step.actual, t = step.track;
  const rows = [3, 2, 1, 0].map((k) => {
    const diff = t.levels[k] - a.levels[k];
    const cls = !diff ? "" : (k >= 2 ? (diff < 0 ? "better" : "worse") : "");
    return `<tr><td><i style="background:${RAMP[k]}"></i>${LEVELS[k]} roads</td><td>${fmt(a.levels[k])}</td>
      <td>${fmt(t.levels[k])}</td><td class="${cls}">${diff > 0 ? "+" : ""}${fmt(diff)}</td></tr>`;
  }).join("");
  const dv = t.vehicle_hours - a.vehicle_hours;
  const pct = a.vehicle_hours ? (100 * dv) / a.vehicle_hours : 0;
  return `<table><tr><th></th><th>Actual</th><th>With TRACK</th><th>Change</th></tr>${rows}
    <tr><td>Vehicle-hours</td><td>${fmt(a.vehicle_hours)}</td><td>${fmt(t.vehicle_hours)}</td>
      <td class="${dv < 0 ? "better" : dv > 0 ? "worse" : ""}">${pct > 0 ? "+" : ""}${pct.toFixed(2)}%</td></tr></table>`;
}

export function renderPlayer() {
  const box = $("simDiversion");
  if (!box) return;
  const d = div.result;
  if (!d) { box.innerHTML = ""; return; }
  const step = d.steps[div.step];
  const last = d.steps[d.steps.length - 1];
  const darkFewer = last.actual.levels[3] - last.track.levels[3];
  box.innerHTML = `
    <p class="note"><b>${escapeHtml(d.from)} to ${escapeHtml(d.to)}</b> · ${d.captures.length} real captures ·
      ${fmt(d.detours)} detours found by ${fmt(d.searches)} A* searches · ${fmt(d.changed.edges.length)} roads ever
      differ · ${fmt(d.seconds)} s</p>
    <div class="divplayer">
      <div class="clock">${clockOf(step, d)}</div>
      <input type="range" id="divStep" min="0" max="${d.steps.length - 1}" value="${div.step}">
      <div class="controls">
        <div class="seg full" id="divMode">
          <button data-mode="actual" class="${div.mode === "actual" ? "on" : ""}">Actual</button>
          <button data-mode="track" class="${div.mode === "track" ? "on" : ""}">With TRACK</button>
        </div>
        <button class="btn sm" id="divPlay">${div.playing ? "Pause" : "Play"}</button>
      </div>
      ${compareTable(step)}
      <label class="row"><span>Outline the roads that differ</span>
        <label class="switch sm"><input type="checkbox" id="divHighlight" ${div.highlight ? "checked" : ""}><span></span></label></label>
      <p class="note">At ${escapeHtml(d.to)}: ${darkFewer > 0 ? `${fmt(darkFewer)} fewer dark red roads` : "no fewer dark red roads"},
        ${fmt(last.diverted_vph)} vehicles an hour on detours. Most detours around a jam run into another jammed
        main road, so at rush hour the street network has little room to absorb the traffic.</p>
      ${div.active ? '<button class="btn sm" id="divLeave">Back to the real data</button>' : ""}
    </div>`;
  $("divStep").oninput = (e) => { stop(); goStep(Number(e.target.value)); };
  box.querySelectorAll("#divMode button").forEach((b) => b.onclick = () => { div.mode = b.dataset.mode; goStep(div.step); });
  $("divPlay").onclick = togglePlay;
  $("divHighlight").onchange = (e) => { div.highlight = e.target.checked; applyLayers(); };
  if ($("divLeave")) $("divLeave").onclick = leave;
}

/* ── while a run is going ─────────────────────────────────────────────────── */

/** The running diversion's newest step: paint its with-TRACK colours on its
    capture, and say where it is. */
export async function showLive(live, box) {
  try {
    const r = await fetch("/api/sim/frame?run=live", { cache: "no-store" });
    if (r.ok) {
      const f = await r.json();
      if (setOverride(f.capture, f.codes, f.v)) await showCapture(f.capture);
    }
  } catch { /* the next poll tries again */ }
  const at = timeOf(live.capture);
  box.innerHTML = `
    <div class="phase"><span class="dot"></span>Diversion: step ${live.step + 1} of ${live.total} ·
      ${escapeHtml(clock12(at))}</div>
    <p class="note">${fmt(live.detours)} detours found by ${fmt(live.searches)} A* searches ·
      ${fmt(live.diverted_vph)} vehicles an hour moved. The map shows the with-TRACK timeline as it is computed.</p>
    <div class="divplayer">${compareTable(live)}</div>`;
}

/** A run ended: the map goes back to the real colours until the result loads. */
export function liveEnded() {
  clearOverride();
  repaint();
}

/* ── the highlight layer ──────────────────────────────────────────────────── */

function highlightData() {
  const d = div.result;
  if (!d) return EMPTY;
  const c = d.changed;
  const n = c.actual.length;
  return { type: "FeatureCollection", features: c.geometry.map((coords, j) => {
    let a = "", t = "";
    for (let s = 0; s < n; s++) { a += c.actual[s][j]; t += c.track[s][j]; }
    return { type: "Feature", properties: { a, t }, geometry: { type: "LineString", coordinates: coords } };
  }) };
}

function levelAt(key) {
  return ["slice", ["get", key], div.step, div.step + 1];
}

function differs() {
  return ["!=", levelAt("a"), levelAt("t")];
}

function addLayers() {
  let added = false;
  if (!hasSource(SRC)) { map.addSource(SRC, { type: "geojson", data: highlightData() }); added = true; }
  if (!hasLayer(CASING)) {
    map.addLayer({ id: CASING, type: "line", source: SRC, layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#0a84ff", "line-opacity": 0,
               "line-width": ["interpolate", ["linear"], ["zoom"], 10, 4, 14, 8, 17, 13] } });
    added = true;
  }
  if (!hasLayer(LINE)) {
    map.addLayer({ id: LINE, type: "line", source: SRC, layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": RAMP[0], "line-opacity": 0,
               "line-width": ["interpolate", ["linear"], ["zoom"], 10, 1.6, 14, 3.6, 17, 7] } });
    added = true;
  }
  return added;
}

function applyLayers() {
  const on = div.active && div.highlight && div.result && !!document.querySelector('.tabpanel[data-panel="simulate"].on');
  const level = levelAt(div.mode === "track" ? "t" : "a");
  setPaint(LINE, "line-color", ["match", level, "0", RAMP[0], "1", RAMP[1], "2", RAMP[2], "3", RAMP[3], "#9e9e9e"]);
  setPaint(LINE, "line-opacity", ["case", differs(), 1, 0]);
  setPaint(CASING, "line-opacity", ["case", differs(), 0.85, 0]);
  setLayout(LINE, "visibility", on ? "visible" : "none");
  setLayout(CASING, "visibility", on ? "visible" : "none");
}

registerLayers({ build: addLayers, apply: applyLayers });
window.addEventListener("tabchange", () => applyLayers());
