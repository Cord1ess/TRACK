/* TRACK visualizer.

   Two traffic layers sit on a CARTO basemap: the captured Google tiles (raster)
   and TRACK's own model (vector). The model ships raw numbers rather than
   colours, so the weight and slowdown sliders re-style every road on the GPU
   with no round trip to the server.

   Layers are re-added idempotently on style.load and on idle, because switching
   theme reloads the basemap style and wipes every source we added. */

const STYLE_URL = {
  light: "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
  dark: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
};
const THEME_BG = { light: "#ffffff", dark: "#000000" };

const LADDER = [25, 55, 85, 105];                                   // the default weight scale
const RAMP = ["#16e098", "#ffcf43", "#d1352b", "#a92727"];          // Google's measured fills
const LEVELS = ["Green", "Yellow", "Red", "Dark red"];
const CLASS_NAMES = ["motorway", "trunk", "primary", "secondary", "tertiary",
                     "unclassified", "residential", "living street", "service"];
const DEFAULTS = { g: 25, o: 55, r: 85, d: 105, jam: 4, beta: 4 };
const MINOR_FADE = [10.4, 11.6];   // zoom range the side streets fade in over
const MAJOR_MS = 240;              // arterial network fades in this fast
const CASCADE_MS = 850;            // side streets grow outward over this long
const REVEAL_BAND = 0.22;          // width of the travelling edge of the cascade
const REVEAL_WAIT_MS = 2500;       // start revealing anyway if the data never reports ready
const EASE_MS = 420;               // eased travel when values jump rather than slide
const MODEL_LAYERS = ["model-minor", "model-major"];
const OURS = new Set(["dim", "traffic", ...MODEL_LAYERS]);
const isOurs = (id) => OURS.has(id) || id.startsWith("gj:");

const $ = (id) => document.getElementById(id);
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

const state = {
  minZoom: 8, captures: [], capture: null,
  layers: [], activeLayers: new Set(), layerData: {}, layerOpacity: 0.9,
  theme: "light", showMap: true, labels: true, dim: 0, origBg: null, pendingApply: false,
  traffic: { show: false, opacity: 1, pure: true },
  // intro and reveal default to 1 = fully visible. Nothing may ever leave a
  // layer at 0 without an animation loop running that is guaranteed to finish.
  model: { show: true, which: "all", opacity: 1, ready: false, minorReady: false,
           intro: { major: 1, minor: 1 }, reveal: { major: 1, minor: 1 } },
  dimPredicted: true,
  colourBy: "level", lineWidth: 1, minCls: 7, lodMinor: true,
  weights: { g: 25, o: 55, r: 85, d: 105 }, jam: 4, beta: 4,
  inspect: false,
  timeline: { start: null, slots: 672, index: 0, playing: false, available: new Map(), timer: null },
};

const map = new maplibregl.Map({
  container: "map", style: STYLE_URL.light, center: [90.4125, 23.8075], zoom: 11,
  attributionControl: { compact: true }, fadeDuration: 0,
});
window.__map = map;

/* ═════════════════════════ style expressions ═════════════════════════
   The model source carries w (weight on the default scale), s (source),
   c (coverage), h (road class rank) and f (free-flow km/h). Everything the
   sliders change is recomputed from those, inside the style, on the GPU. */

function remapWeight() {
  const w = state.weights;
  return ["interpolate", ["linear"], ["get", "w"],
    LADDER[0], w.g, LADDER[1], w.o, LADDER[2], w.r, LADDER[3], w.d];
}
function alphaOf() {
  // BPR alpha, pinned so the dark-red weight runs `jam` times slower than free flow
  const vc = Math.max(state.weights.d, 1) / 100;
  return clamp((state.jam - 1) / Math.pow(vc, state.beta), 0, 1e4);
}
function delayExpr() {
  return ["+", 1, ["*", alphaOf(), ["^", ["/", remapWeight(), 100], state.beta]]];
}
function interp(x, xs, ys) {
  if (x <= xs[0]) return ys[0];
  for (let i = 1; i < xs.length; i++) {
    if (x <= xs[i]) {
      const t = (x - xs[i - 1]) / (xs[i] - xs[i - 1]);
      return ys[i - 1] + t * (ys[i] - ys[i - 1]);
    }
  }
  return ys[ys.length - 1];
}
function delayAt(weight) {          // the same maths in plain JS, for the table and inspector
  const w = state.weights;
  const W = interp(weight, LADDER, [w.g, w.o, w.r, w.d]);
  return { weight: W, delay: 1 + alphaOf() * Math.pow(W / 100, state.beta) };
}
function colourExpr() {
  if (state.colourBy === "delay") {
    return ["interpolate", ["linear"], delayExpr(),
      1, RAMP[0], 1.4, RAMP[1], 2.2, RAMP[2], 3.5, RAMP[3]];
  }
  if (state.colourBy === "speed") {
    return ["interpolate", ["linear"], ["/", ["get", "f"], delayExpr()],
      5, RAMP[3], 12, RAMP[2], 25, RAMP[1], 45, RAMP[0]];
  }
  return ["interpolate", ["linear"], ["get", "w"],
    LADDER[0], RAMP[0], LADDER[1], RAMP[1], LADDER[2], RAMP[2], LADDER[3], RAMP[3]];
}
function widthExpr(minor) {
  const m = state.lineWidth * (minor ? 0.72 : 1);
  return ["interpolate", ["linear"], ["zoom"],
    10, 0.5 * m, 12, 0.9 * m, 14, 1.7 * m, 16, 3 * m, 18, 6 * m];
}

/* Opacity carries four separate ideas at once:
   - `intro`, a quick fade so a part that has just loaded appears rather than
     snapping into existence;
   - `reveal`, the cascade: each road carries `o`, its distance out from the
     arterial network, and a travelling band sweeps o from 0 to 1 so the side
     streets grow off the main roads like capillaries off an artery;
   - a zoom ramp for the side streets, so they arrive gradually rather than all
     switching on at one zoom level;
   - a dimming of predicted roads, so what Google measured always reads as the
     primary layer and our inference as an addition on top of it. */
function opacityExpr(part) {
  const base = state.model.opacity * state.model.intro[part];
  let perFeature = state.dimPredicted
    ? ["case", ["==", ["get", "s"], 0], base, base * 0.55]
    : base;

  const progress = state.model.reveal[part];
  if (progress < 1) {
    const hi = progress * (1 + REVEAL_BAND);      // roads at or past this are still hidden
    const lo = hi - REVEAL_BAND;                  // roads at or before this are fully in
    perFeature = ["*", perFeature,
      ["interpolate", ["linear"], ["get", "o"], lo, 1, hi, 0]];
  }

  if (part === "minor" && state.lodMinor) {
    // A "zoom" expression has to be the outermost one, so everything
    // per-feature rides in the interpolate's output stops rather than
    // multiplying the whole thing, which MapLibre rejects.
    return ["interpolate", ["linear"], ["zoom"], MINOR_FADE[0], 0, MINOR_FADE[1], perFeature];
  }
  return perFeature;
}
function modelFilter() {
  // major and minor live in separate sources, so the filter only has to carry
  // the user's own choices
  const f = ["all", ["<=", ["get", "h"], state.minCls]];
  if (state.model.which !== "all") {
    f.push(["==", ["get", "s"], state.model.which === "observed" ? 0 : 1]);
  }
  return f;
}

/* ═════════════════════════ layers ═════════════════════════ */

const WORLD = { type: "Feature", geometry: { type: "Polygon",
  coordinates: [[[-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85]]] } };

function firstGeoJsonLayerId() {
  const l = (map.getStyle()?.layers || []).find((x) => x.id.startsWith("gj:"));
  return l ? l.id : undefined;
}

function ensureLayers() {
  if (!map.isStyleLoaded()) return;
  let added = false;

  if (!map.getSource("dim")) {
    map.addSource("dim", { type: "geojson", data: WORLD });
    map.addLayer({ id: "dim", type: "fill", source: "dim",
      paint: { "fill-color": THEME_BG[state.theme], "fill-opacity": state.dim } });
    added = true;
  }
  if (state.capture && !map.getSource("traffic")) {
    const c = state.capture;
    map.addSource("traffic", { type: "raster", tileSize: 256, minzoom: state.minZoom, maxzoom: c.zoom,
      tiles: [`${location.origin}/tiles/${c.name}/{z}/{x}/{y}.png${state.traffic.pure ? "?clean=1" : ""}`] });
    map.addLayer({ id: "traffic", type: "raster", source: "traffic",
      paint: { "raster-opacity": state.traffic.opacity, "raster-resampling": "nearest",
               "raster-fade-duration": 220 },
      layout: { visibility: state.traffic.show ? "visible" : "none" } });
    added = true;
  }
  if (state.model.ready && !map.getSource("model-major-src")) { addModelPart("major"); added = true; }
  if (state.model.ready && state.model.minorReady && !map.getSource("model-minor-src")) {
    addModelPart("minor"); added = true;
  }
  for (const l of state.layers) {
    if (state.activeLayers.has(l.id) && !map.getSource(`gj:${l.id}`) && state.layerData[l.id]) {
      addGeoJsonLayer(l); added = true;
    }
  }
  if (added) applyAll();
}

function addModelPart(part) {
  const minor = part === "minor";
  const src = `model-${part}-src`;
  // maxzoom 14 on the source keeps client-side tiling cheap; lines stay crisp
  // above it because vectors are re-projected, not resampled.
  map.addSource(src, { type: "geojson", data: `/model.geojson?part=${part}`,
    maxzoom: 14, buffer: 32, tolerance: 0.375 });
  map.addLayer({ id: `model-${part}`, type: "line", source: src,
    minzoom: minor && state.lodMinor ? MINOR_FADE[0] - 0.2 : 0,
    filter: modelFilter(),
    layout: { "line-cap": "round", "line-join": "round",
              visibility: state.model.show ? "visible" : "none" },
    paint: { "line-color": colourExpr(), "line-width": widthExpr(minor),
             "line-opacity": opacityExpr(part) } });
  startReveal(part);
}

/* One self-contained animation per part. It waits for the data to be ready,
   but only up to REVEAL_WAIT_MS, and it always ends by putting the layer back
   to fully visible. There is no path that leaves a layer hidden. */
function settleReveal(part) {
  state.model.intro[part] = 1;
  state.model.reveal[part] = 1;
  applyModelOpacity();
}
function startReveal(part) {
  const layerId = `model-${part}`;
  const srcId = `model-${part}-src`;
  const cascading = part === "minor";
  const dur = cascading ? CASCADE_MS : MAJOR_MS;
  if (cascading) state.model.reveal.minor = 0;
  else state.model.intro.major = 0;
  applyModelOpacity();

  const queued = performance.now();
  let begun = 0;
  const step = (now) => {
    if (!map.getLayer(layerId)) { settleReveal(part); return; }   // style reloaded under us
    if (!begun) {
      const ready = map.getSource(srcId) && map.isSourceLoaded(srcId);
      if (!ready && now - queued < REVEAL_WAIT_MS) { requestAnimationFrame(step); return; }
      begun = now;
    }
    const k = Math.min(1, (now - begun) / dur);
    if (cascading) state.model.reveal.minor = k;
    else state.model.intro.major = k * k * (3 - 2 * k);           // smoothstep
    applyModelOpacity();
    if (k < 1) requestAnimationFrame(step);
    else settleReveal(part);
  };
  requestAnimationFrame(step);
}

/* The side streets are the bulk of the data. Hold them back until the major
   network has drawn, so the map is useful in about a second instead of waiting
   on everything. */
function releaseMinor() {
  if (state.model.minorReady) return;
  state.model.minorReady = true;
  ensureLayers();
}
map.on("sourcedata", (e) => {
  if (e.sourceId === "model-major-src" && map.getSource("model-major-src")
      && map.isSourceLoaded("model-major-src")) {
    releaseMinor();
  }
});

map.on("style.load", () => {
  const bg = (map.getStyle()?.layers || []).find((l) => l.type === "background");
  state.origBg = bg ? (map.getPaintProperty(bg.id, "background-color") || THEME_BG[state.theme])
                    : THEME_BG[state.theme];
  ensureLayers(); applyAll();
});
map.on("idle", () => {
  ensureLayers();
  if (state.pendingApply && map.isStyleLoaded()) { state.pendingApply = false; applyAll(); }
});

/* ═════════════════════════ applying state ═════════════════════════ */

function applyBase() {
  if (!map.isStyleLoaded()) return;
  for (const l of (map.getStyle()?.layers || [])) {
    if (isOurs(l.id)) continue;
    try {
      if (l.type === "background") {
        map.setPaintProperty(l.id, "background-color", state.showMap ? state.origBg : THEME_BG[state.theme]);
        continue;
      }
      const vis = !state.showMap ? "none" : (l.type === "symbol" && !state.labels ? "none" : "visible");
      map.setLayoutProperty(l.id, "visibility", vis);
    } catch (e) { /* a style layer that does not take visibility */ }
  }
  if (map.getLayer("dim")) {
    map.setPaintProperty("dim", "fill-color", THEME_BG[state.theme]);
    map.setPaintProperty("dim", "fill-opacity", state.showMap ? state.dim : 0);
  }
  setRowEnabled("labels", state.showMap);
  setRowEnabled("dim", state.showMap);
}
function applyTraffic() {
  if (!map.getLayer("traffic")) return;
  map.setLayoutProperty("traffic", "visibility", state.traffic.show ? "visible" : "none");
  map.setPaintProperty("traffic", "raster-opacity", state.traffic.opacity);
}
function applyModelOpacity() {
  for (const part of ["major", "minor"]) {
    const id = `model-${part}`;
    if (map.getLayer(id)) map.setPaintProperty(id, "line-opacity", opacityExpr(part));
  }
}
function applyModel() {
  for (const part of ["major", "minor"]) {
    const id = `model-${part}`;
    if (!map.getLayer(id)) continue;
    map.setLayoutProperty(id, "visibility", state.model.show ? "visible" : "none");
    map.setLayerZoomRange(id, part === "minor" && state.lodMinor ? MINOR_FADE[0] - 0.2 : 0, 24);
  }
  applyModelOpacity();
}
function applyModelStyle() {          // the live part: colour, width and filter, no reload
  const c = colourExpr();
  for (const part of ["major", "minor"]) {
    const id = `model-${part}`;
    if (!map.getLayer(id)) continue;
    map.setPaintProperty(id, "line-color", c);
    map.setPaintProperty(id, "line-width", widthExpr(part === "minor"));
    map.setPaintProperty(id, "line-opacity", opacityExpr(part));
    map.setFilter(id, modelFilter());
  }
  renderLegend();
  renderCalc();
}

/* Dragging a slider fires far faster than the map can repaint 60 k lines, and
   every event would queue another full restyle. Collapse them to at most one
   per animation frame: the numbers update on the spot, the map keeps up. */
let styleFrame = 0;
function requestStyleApply() {
  renderLegend();
  renderCalc();
  if (styleFrame) return;
  styleFrame = requestAnimationFrame(() => { styleFrame = 0; applyModelStyle(); });
}

/* When a value jumps rather than slides (reset, reload), walk there over a few
   hundred milliseconds so the city flows into its new colours. */
let easeFrame = 0;
function easeWeightsTo(target, dur = EASE_MS) {
  cancelAnimationFrame(easeFrame);
  const from = { ...state.weights, jam: state.jam, beta: state.beta };
  const t0 = performance.now();
  const step = (now) => {
    const k = Math.min(1, (now - t0) / dur);
    const e = 1 - Math.pow(1 - k, 3);                       // ease out
    for (const key of ["g", "o", "r", "d"]) {
      state.weights[key] = from[key] + (target[key] - from[key]) * e;
    }
    state.jam = from.jam + (target.jam - from.jam) * e;
    state.beta = from.beta + (target.beta - from.beta) * e;
    syncWeightInputs();
    applyModelStyle();
    if (k < 1) easeFrame = requestAnimationFrame(step);
  };
  easeFrame = requestAnimationFrame(step);
}
function syncWeightInputs() {
  document.querySelectorAll(".wgt").forEach((row) => {
    const v = state.weights[row.dataset.k];
    row.querySelector("input").value = v;
    row.querySelector("em").textContent = Math.round(v);
  });
  $("jam").value = state.jam;
  $("jamv").textContent = `${state.jam.toFixed(1)}x`;
  $("beta").value = state.beta;
  $("betav").textContent = state.beta.toFixed(1);
}
function applyLayerOpacity() {
  for (const id of state.activeLayers) {
    const ids = geoIds(id);
    if (map.getLayer(ids.line)) map.setPaintProperty(ids.line, "line-opacity", state.layerOpacity);
    if (map.getLayer(ids.pt)) map.setPaintProperty(ids.pt, "circle-opacity", state.layerOpacity);
    if (map.getLayer(ids.fill)) map.setPaintProperty(ids.fill, "fill-opacity", state.layerOpacity * 0.3);
  }
}
function orderLayers() {
  if (!map.isStyleLoaded()) return;
  try {
    const before = firstGeoJsonLayerId();
    for (const id of ["traffic", "model-minor", "model-major"]) {
      if (map.getLayer(id)) map.moveLayer(id, before);
    }
  } catch (e) { /* mid style reload */ }
}
function applyAll() {
  applyBase(); applyTraffic(); applyModel(); applyModelStyle(); applyLayerOpacity(); orderLayers();
}
function setRowEnabled(inputId, on) {
  const row = $(inputId)?.closest(".row");
  if (row) row.classList.toggle("off", !on);
}

/* ═════════════════════════ legend + result table ═════════════════════════ */

function renderLegend() {
  const items = state.colourBy === "delay"
    ? [["1.0x", RAMP[0]], ["1.4x", RAMP[1]], ["2.2x", RAMP[2]], ["3.5x+", RAMP[3]]]
    : state.colourBy === "speed"
      ? [["45+ km/h", RAMP[0]], ["25", RAMP[1]], ["12", RAMP[2]], ["5", RAMP[3]]]
      : [["free", RAMP[0]], ["slow", RAMP[1]], ["heavy", RAMP[2]], ["jam", RAMP[3]]];
  $("legendItems").innerHTML = items
    .map(([t, c]) => `<div><i style="background:${c}"></i>${t}</div>`).join("");
}
function renderCalc() {
  $("calcBody").innerHTML = LADDER.map((base, i) => {
    const { weight, delay } = delayAt(base);
    return `<tr><td><i style="background:${RAMP[i]}"></i>${LEVELS[i]}</td>` +
      `<td>${weight.toFixed(0)}</td><td>${(weight / 100).toFixed(2)}</td>` +
      `<td>${delay.toFixed(2)}x</td><td>${(50 / delay).toFixed(0)} km/h</td></tr>`;
  }).join("");
}

/* ═════════════════════════ algorithm GeoJSON layers ═════════════════════════ */

const geoIds = (id) => ({ src: `gj:${id}`, line: `gj:${id}:line`, pt: `gj:${id}:pt`, fill: `gj:${id}:fill` });
function addGeoJsonLayer(l) {
  const ids = geoIds(l.id);
  if (map.getSource(ids.src)) return;
  map.addSource(ids.src, { type: "geojson", data: state.layerData[l.id] });
  const color = ["case", ["has", "color"], ["get", "color"],
    ["has", "cls"], ["match", ["get", "cls"], 1, RAMP[0], 2, RAMP[1], 3, RAMP[2], 4, RAMP[3], "#9e9e9e"],
    "#0071e3"];
  map.addLayer({ id: ids.fill, type: "fill", source: ids.src, filter: ["==", ["geometry-type"], "Polygon"],
    paint: { "fill-color": color, "fill-opacity": state.layerOpacity * 0.3 } });
  map.addLayer({ id: ids.line, type: "line", source: ids.src,
    filter: ["any", ["==", ["geometry-type"], "LineString"], ["==", ["geometry-type"], "Polygon"]],
    paint: { "line-color": color, "line-width": ["case", ["has", "width"], ["get", "width"], 3],
             "line-opacity": state.layerOpacity },
    layout: { "line-cap": "round", "line-join": "round" } });
  map.addLayer({ id: ids.pt, type: "circle", source: ids.src, filter: ["==", ["geometry-type"], "Point"],
    paint: { "circle-color": color, "circle-radius": 5, "circle-opacity": state.layerOpacity,
             "circle-stroke-color": "#fff", "circle-stroke-width": 1.5 } });
}
function removeGeoJsonLayer(id) {
  const ids = geoIds(id);
  for (const k of [ids.pt, ids.line, ids.fill]) if (map.getLayer(k)) map.removeLayer(k);
  if (map.getSource(ids.src)) map.removeSource(ids.src);
}
async function loadLayerList() {
  try { state.layers = await (await fetch("/api/layers")).json(); } catch { state.layers = []; }
  const box = $("layers");
  if (!state.layers.length) {
    box.innerHTML = '<p class="hint">none yet — pipeline outputs appear here</p>';
    return;
  }
  box.innerHTML = "";
  for (const l of state.layers) {
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML = `<span>${l.name || l.id}</span>
      <label class="switch sm"><input type="checkbox"><span></span></label>`;
    const cb = row.querySelector("input");
    cb.onchange = async () => {
      if (cb.checked) {
        state.activeLayers.add(l.id);
        if (!state.layerData[l.id]) state.layerData[l.id] = await (await fetch(l.url)).json();
        ensureLayers();
      } else {
        state.activeLayers.delete(l.id);
        removeGeoJsonLayer(l.id);
      }
    };
    box.appendChild(row);
  }
}

/* ═════════════════════════ captures ═════════════════════════ */

function removeTraffic() {
  if (map.getLayer("traffic")) map.removeLayer("traffic");
  if (map.getSource("traffic")) map.removeSource("traffic");
}
function fitCapture() {
  const c = state.capture;
  if (!c || !c.bbox) return;
  map.fitBounds([[c.bbox.west, c.bbox.south], [c.bbox.east, c.bbox.north]], { padding: 40, duration: 600 });
}
function selectCapture(name, fit) {
  state.capture = state.captures.find((c) => c.name === name) || null;
  removeTraffic(); ensureLayers();
  const c = state.capture;
  if (!c) { $("capInfo").textContent = "no captures found"; return; }
  const when = (c.captured_utc || "").replace("T", " ").replace("Z", "");
  $("capInfo").innerHTML = `${c.status} · z${c.zoom} · ${c.coverage_pct}% coverage · ` +
    `${c.tiles_nonempty ?? "?"} of ${c.expected_tiles ?? "?"} tiles carry traffic<br>${when} UTC`;
  if ($("capture").value !== name) $("capture").value = name;
  if (fit) fitCapture();
}
async function loadCaptures() {
  try { state.captures = await (await fetch("/api/captures")).json(); } catch { state.captures = []; }
  const sel = $("capture");
  sel.innerHTML = "";
  for (const c of state.captures) {
    const o = document.createElement("option");
    o.value = c.name; o.textContent = c.name;
    sel.appendChild(o);
  }
  if (state.captures.length) {
    // newest by capture time, not by name order
    const newest = state.captures.reduce((a, b) =>
      (Date.parse(b.captured_utc) || 0) > (Date.parse(a.captured_utc) || 0) ? b : a);
    sel.value = newest.name;
    selectCapture(newest.name, true);
  }
}

/* ═════════════════════════ model ═════════════════════════ */

async function loadModelInfo() {
  let m = { ready: false, error: "server unreachable" };
  try { m = await (await fetch("/api/model")).json(); } catch {}
  state.model.ready = !!m.ready;

  for (const id of ["showModel", "modelOpacity", "abFlip", "lineWidth", "minCls", "lodMinor"]) {
    const el = $(id);
    if (el) el.disabled = !m.ready;
  }
  document.querySelectorAll("#modelWhich button, #colourBy button")
    .forEach((b) => { b.disabled = !m.ready; });

  if (m.ready) {
    const pct = m.lines ? Math.round((100 * m.observed) / m.lines) : 0;
    $("modelInfo").innerHTML =
      `${m.lines.toLocaleString()} roads · ${m.observed.toLocaleString()} observed (${pct}%) · ` +
      `${m.predicted.toLocaleString()} predicted<br>` +
      `${m.edges.toLocaleString()} directed edges · ${(m.bytes / 1048576).toFixed(1)} MB from ${m.weights}`;
    setTimeout(releaseMinor, 3500);          // in case the major source never reports ready
  } else {
    state.model.show = false;
    state.traffic.show = true;
    $("modelInfo").innerHTML = `no model data: ${m.error || "not built"}.<br>` +
      `Run <b>python pipeline.py</b> in the algorithms folder.`;
  }
  $("showModel").checked = state.model.show;
  $("showTraffic").checked = state.traffic.show;
  ensureLayers();
  applyAll();
}

/* ═════════════════════════ inspector ═════════════════════════ */

function setInspect(on) {
  state.inspect = on;
  $("inspectBtn").setAttribute("aria-pressed", String(on));
  map.getCanvas().style.cursor = on ? "crosshair" : "";
  if (!on) $("inspect").hidden = true;
}
function rampColour(w) {
  const i = LADDER.findIndex((v) => w <= v);
  return RAMP[i < 0 ? RAMP.length - 1 : i];
}
map.on("click", (e) => {
  if (!state.inspect) return;
  const layers = MODEL_LAYERS.filter((id) => map.getLayer(id));
  if (!layers.length) return;
  const hits = map.queryRenderedFeatures(
    [[e.point.x - 6, e.point.y - 6], [e.point.x + 6, e.point.y + 6]], { layers });
  if (!hits.length) { $("inspect").hidden = true; return; }
  const p = hits[0].properties;
  const base = Number(p.w);
  const { weight, delay } = delayAt(base);
  const changed = weight.toFixed(0) !== base.toFixed(0);
  $("inspectBody").innerHTML =
    `<h4><i style="background:${rampColour(base)}"></i>${CLASS_NAMES[p.h] || "road"}</h4>
     <dl>
       <dt>Source</dt><dd><span class="tag">${Number(p.s) === 0 ? "observed" : "predicted"}</span></dd>
       <dt>Weight</dt><dd>${base.toFixed(0)}${changed ? ` → ${weight.toFixed(0)}` : ""}</dd>
       ${p.wb !== undefined ? `<dt>Other way</dt><dd>${Number(p.wb).toFixed(0)}` +
          `${p.sb ? ' <span class="tag">predicted</span>' : ""}</dd>` : ""}
       <dt>v/c ratio</dt><dd>${(weight / 100).toFixed(2)}</dd>
       <dt>Coverage</dt><dd>${(Number(p.c) * 100).toFixed(0)}%</dd>
       <dt>Free flow</dt><dd>${Number(p.f).toFixed(0)} km/h</dd>
       <dt>Slowdown</dt><dd>${delay.toFixed(2)}x</dd>
       <dt>Effective</dt><dd>${(Number(p.f) / delay).toFixed(0)} km/h</dd>
       <dt>Edge id</dt><dd>${p.i}</dd>
     </dl>`;
  $("inspect").hidden = false;
});

/* ═════════════════════════ timeline ═════════════════════════
   A week of 15-minute slots. Only the slots with a real capture carry data;
   the rest are the shape the weekly collection will fill in. */

const SLOT_MS = 15 * 60 * 1000;
const DHAKA_OFFSET = 6 * 3600 * 1000;          // captures are stamped UTC, Dhaka is UTC+6
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function buildTimeline() {
  const tl = state.timeline;
  const stamps = state.captures.map((c) => Date.parse(c.captured_utc)).filter((t) => !isNaN(t));
  const anchor = stamps.length ? Math.max(...stamps) : Date.now();
  const d = new Date(anchor + DHAKA_OFFSET);                       // Dhaka wall clock
  const dow = (d.getUTCDay() + 6) % 7;                             // Monday = 0
  const monday = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) - dow * 86400000;
  tl.start = monday - DHAKA_OFFSET;                                // back to real UTC

  tl.available.clear();
  for (const c of state.captures) {
    const t = Date.parse(c.captured_utc);
    if (isNaN(t)) continue;
    const idx = Math.round((t - tl.start) / SLOT_MS);
    if (idx >= 0 && idx < tl.slots) tl.available.set(idx, c.name);
  }
  // start on the capture that is currently loaded, else the newest slot we have
  const here = [...tl.available.entries()].find(([, n]) => n === state.capture?.name);
  const newest = [...tl.available.keys()].sort((a, b) => b - a)[0];
  tl.index = here ? here[0] : (newest ?? Math.floor(tl.slots / 2));

  $("trackDays").innerHTML = DAYS.map((x) => `<div>${x}</div>`).join("");
  $("trackMarks").innerHTML = [...tl.available.keys()]
    .map((i) => `<i style="left:${(i / (tl.slots - 1)) * 100}%"></i>`).join("");
  renderTimeline();
}
function slotDate(i) { return new Date(state.timeline.start + i * SLOT_MS + DHAKA_OFFSET); }
function renderTimeline() {
  const tl = state.timeline;
  $("trackHead").style.left = `${(tl.index / (tl.slots - 1)) * 100}%`;
  const d = slotDate(tl.index);
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  $("slotLabel").textContent = `${DAYS[(d.getUTCDay() + 6) % 7]} ${d.getUTCDate()} · ${hh}:${mm}`;
  const st = $("slotState");
  if (tl.available.has(tl.index)) { st.textContent = "capture loaded"; st.className = "live"; }
  else { st.textContent = `${tl.available.size} of ${tl.slots} slots collected`; st.className = ""; }
}
function setSlot(i, fromUser) {
  const tl = state.timeline;
  tl.index = clamp(Math.round(i), 0, tl.slots - 1);
  const name = tl.available.get(tl.index);
  if (name && name !== state.capture?.name) selectCapture(name, false);
  renderTimeline();
  if (fromUser && tl.playing) stopPlay();
}
function startPlay() {
  const tl = state.timeline;
  tl.playing = true;
  $("playIcon").innerHTML = '<path d="M4.5 3h2.6v10H4.5zM8.9 3h2.6v10H8.9z" fill="currentColor"/>';
  $("playBtn").title = "Pause (space)";
  tl.timer = setInterval(() => setSlot((tl.index + 1) % tl.slots), 120);
}
function stopPlay() {
  const tl = state.timeline;
  tl.playing = false;
  clearInterval(tl.timer);
  $("playIcon").innerHTML = '<path d="M5 3.2l7.2 4.8-7.2 4.8z" fill="currentColor"/>';
  $("playBtn").title = "Play (space)";
}
(function bindTrack() {
  const track = $("track");
  const toSlot = (ev) => {
    const r = track.getBoundingClientRect();
    setSlot(((ev.clientX - r.left) / r.width) * (state.timeline.slots - 1), true);
  };
  let dragging = false;
  track.addEventListener("pointerdown", (e) => {
    dragging = true; track.setPointerCapture(e.pointerId); toSlot(e);
  });
  track.addEventListener("pointermove", (e) => { if (dragging) toSlot(e); });
  track.addEventListener("pointerup", (e) => {
    dragging = false;
    try { track.releasePointerCapture(e.pointerId); } catch (err) {}
  });
  track.addEventListener("wheel", (e) => {
    e.preventDefault();
    setSlot(state.timeline.index + Math.sign(e.deltaY || e.deltaX), true);
  }, { passive: false });
})();

/* ═════════════════════════ theme ═════════════════════════ */

function setTheme(theme) {
  if (theme === state.theme) return;
  state.theme = theme;
  document.body.classList.toggle("dark", theme === "dark");
  document.querySelectorAll("#theme button")
    .forEach((b) => b.classList.toggle("on", b.dataset.theme === theme));
  state.pendingApply = true;
  map.setStyle(STYLE_URL[theme], { diff: false });   // style.load re-adds our layers
}

/* ═════════════════════════ controls ═════════════════════════ */

document.querySelectorAll(".tabs button").forEach((b) => b.onclick = () => {
  document.querySelectorAll(".tabs button").forEach((o) => o.classList.toggle("on", o === b));
  document.querySelectorAll(".tabpanel")
    .forEach((p) => p.classList.toggle("on", p.dataset.panel === b.dataset.tab));
});
document.querySelectorAll("#theme button").forEach((b) => b.onclick = () => setTheme(b.dataset.theme));
$("dockBtn").onclick = () => document.body.classList.toggle("dock-hidden");
$("helpBtn").onclick = () => { $("help").hidden = false; };
$("helpClose").onclick = () => { $("help").hidden = true; };
$("help").onclick = (e) => { if (e.target.id === "help") $("help").hidden = true; };

$("zoomIn").onclick = () => map.zoomIn();
$("zoomOut").onclick = () => map.zoomOut();
$("fitBtn").onclick = fitCapture;
$("northBtn").onclick = () => map.easeTo({ bearing: 0, pitch: 0, duration: 400 });
$("inspectBtn").onclick = () => setInspect(!state.inspect);
$("inspectClose").onclick = () => { $("inspect").hidden = true; };

$("capture").onchange = (e) => selectCapture(e.target.value, true);
$("showTraffic").onchange = (e) => { state.traffic.show = e.target.checked; applyTraffic(); };
$("pure").onchange = (e) => { state.traffic.pure = e.target.checked; removeTraffic(); ensureLayers(); };
$("opacity").oninput = (e) => {
  state.traffic.opacity = +e.target.value;
  $("ov").textContent = `${Math.round(state.traffic.opacity * 100)}%`;
  applyTraffic();
};
$("showModel").onchange = (e) => { state.model.show = e.target.checked; applyModel(); };
$("modelOpacity").oninput = (e) => {
  state.model.opacity = +e.target.value;
  $("mov").textContent = `${Math.round(state.model.opacity * 100)}%`;
  applyModel();
};
document.querySelectorAll("#modelWhich button").forEach((b) => b.onclick = () => {
  state.model.which = b.dataset.which;
  document.querySelectorAll("#modelWhich button").forEach((o) => o.classList.toggle("on", o === b));
  applyModelStyle();
});
document.querySelectorAll("#colourBy button").forEach((b) => b.onclick = () => {
  state.colourBy = b.dataset.mode;
  document.querySelectorAll("#colourBy button").forEach((o) => o.classList.toggle("on", o === b));
  $("colourHint").textContent = {
    level: "Traffic level on the measured Google ramp. A fixed reference.",
    delay: "How many times slower than an empty road. Reacts to the Weights tab.",
    speed: "Effective speed: free-flow speed divided by the slowdown.",
  }[state.colourBy];
  applyModelStyle();
});
$("showMap").onchange = (e) => { state.showMap = e.target.checked; applyBase(); };
$("labels").onchange = (e) => { state.labels = e.target.checked; applyBase(); };
$("dim").oninput = (e) => {
  state.dim = +e.target.value;
  $("dimv").textContent = `${Math.round(state.dim * 100)}%`;
  applyBase();
};
$("lineWidth").oninput = (e) => {
  state.lineWidth = +e.target.value;
  $("lwv").textContent = `${state.lineWidth.toFixed(1)}x`;
  requestStyleApply();
};
$("minCls").oninput = (e) => {
  state.minCls = +e.target.value;
  $("minclsv").textContent = CLASS_NAMES[state.minCls];
  requestStyleApply();
};
$("lodMinor").onchange = (e) => { state.lodMinor = e.target.checked; applyModel(); };
$("dimPredicted").onchange = (e) => { state.dimPredicted = e.target.checked; applyModelOpacity(); };
$("layerOpacity").oninput = (e) => {
  state.layerOpacity = +e.target.value;
  $("lov").textContent = `${Math.round(state.layerOpacity * 100)}%`;
  applyLayerOpacity();
};

document.querySelectorAll(".wgt").forEach((row) => {
  const input = row.querySelector("input");
  const out = row.querySelector("em");
  input.oninput = () => {
    cancelAnimationFrame(easeFrame);
    state.weights[row.dataset.k] = +input.value;
    out.textContent = input.value;
    requestStyleApply();
  };
});
$("jam").oninput = (e) => {
  cancelAnimationFrame(easeFrame);
  state.jam = +e.target.value;
  $("jamv").textContent = `${state.jam.toFixed(1)}x`;
  requestStyleApply();
};
$("beta").oninput = (e) => {
  cancelAnimationFrame(easeFrame);
  state.beta = +e.target.value;
  $("betav").textContent = state.beta.toFixed(1);
  requestStyleApply();
};
$("resetWeights").onclick = () => easeWeightsTo({ ...DEFAULTS });

/* The fallback. If the map ever looks out of step with the sliders, or the
   pipeline has been re-run behind it, this pulls the data again and repaints
   from the current settings without a page reload. */
async function reloadModel() {
  const btn = $("reloadModel");
  const label = btn.textContent;
  cancelAnimationFrame(easeFrame);
  cancelAnimationFrame(styleFrame);
  styleFrame = 0;
  btn.disabled = true;
  btn.textContent = "Reloading…";
  try {
    await loadModelInfo();
    const v = Date.now();
    for (const part of ["major", "minor"]) {
      const src = map.getSource(`model-${part}-src`);
      if (src) {
        src.setData(`/model.geojson?part=${part}&v=${v}`);
        startReveal(part);
      }
    }
    applyModel();
    applyModelStyle();
    map.triggerRepaint();
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}
$("reloadModel").onclick = reloadModel;

function abFlip() {
  if (!state.model.ready) return;
  const toModel = !state.model.show;
  state.model.show = toModel;
  state.traffic.show = !toModel;
  $("showModel").checked = toModel;
  $("showTraffic").checked = !toModel;
  applyModel(); applyTraffic();
}
$("abFlip").onclick = abFlip;
$("playBtn").onclick = () => (state.timeline.playing ? stopPlay() : startPlay());

const toggle = (id) => { const el = $(id); el.checked = !el.checked; el.onchange({ target: el }); };
document.addEventListener("keydown", (e) => {
  if (/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const k = e.key.toLowerCase();
  if (k === "t") toggle("showTraffic");
  else if (k === "m") toggle("showModel");
  else if (k === "p") toggle("pure");
  else if (k === "l") toggle("labels");
  else if (k === "b") toggle("showMap");
  else if (k === "c") abFlip();
  else if (k === "f") fitCapture();
  else if (k === "i") setInspect(!state.inspect);
  else if (k === "n") map.easeTo({ bearing: 0, pitch: 0, duration: 400 });
  else if (k === "r") reloadModel();
  else if (k === "1") setTheme("light");
  else if (k === "2") setTheme("dark");
  else if (k === "\\") document.body.classList.toggle("dock-hidden");
  else if (k === "?") $("help").hidden = !$("help").hidden;
  else if (k === "escape") { $("help").hidden = true; $("inspect").hidden = true; }
  else if (k === " ") { state.timeline.playing ? stopPlay() : startPlay(); }
  else if (k === "arrowleft" || k === "arrowright") {
    // plain arrows stay with the map for panning; the timeline takes them only
    // when the track has focus or shift is held
    if (document.activeElement !== $("track") && !e.shiftKey) return;
    setSlot(state.timeline.index + (k === "arrowleft" ? -1 : 1), true);
  }
  else return;
  e.preventDefault();
});

/* ═════════════════════════ status bar ═════════════════════════ */

function distance(metres) {
  return metres >= 1000 ? `${(metres / 1000).toFixed(metres < 10000 ? 1 : 0)} km`
                        : `${Math.round(metres / 10) * 10} m`;
}
map.on("move", () => {
  const c = map.getCenter();
  // MapLibre defines zoom against 512 px tiles, so ground metres per CSS pixel
  // is half the familiar 256 px figure. Using the 256 constant made the scale
  // readout report twice the real distance.
  const mpp = 78271.516964 * Math.cos((c.lat * Math.PI) / 180) / Math.pow(2, map.getZoom());
  // report the ground width actually on screen: unambiguous, and it needs no
  // drawn bar to be meaningful
  const across = distance(mpp * map.getCanvas().clientWidth);
  $("coords").textContent =
    `${c.lat.toFixed(5)}, ${c.lng.toFixed(5)}  ·  z${map.getZoom().toFixed(1)}  ·  ${across} across`;
});

/* ═════════════════════════ boot ═════════════════════════ */

map.on("load", async () => {
  try {
    const cfg = await (await fetch("/api/config")).json();
    state.minZoom = cfg.min_zoom ?? state.minZoom;
  } catch {}
  await loadCaptures();
  buildTimeline();
  await loadLayerList();
  await loadModelInfo();
  renderLegend();
  renderCalc();
  map.fire("move");
});
