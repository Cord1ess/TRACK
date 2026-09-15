/* TRACK visualizer.

   Two traffic layers sit on a CARTO basemap: the captured Google tiles (raster)
   and TRACK's own model (vector). The model ships raw numbers rather than
   colours, so the weight and slowdown sliders re-style every road on the GPU.

   Reliability rules. Each exists because breaking it made roads vanish:

   1. Never gate map edits on map.isStyleLoaded(). It is false whenever ANY tile
      is still loading, which is most of the time while panning, so layer adds
      silently did nothing until the next idle. Gate on the style spec having
      loaded instead (styleReady).
   2. Never wipe our sources to change theme. setStyle carries our sources and
      layers into the new style (transformStyle + diff), so the roads stay drawn
      and nothing is downloaded again. Wiping them left the map without roads
      for about 2.5 s on every switch.
   3. Never leave a layer at opacity 0 waiting for an event. Every animation has
      a timer that forces it visible, runs inside try/catch, and is finished at
      once when the tab is hidden, where animation frames stop.
   4. Never tear a layer down to change what it shows. A capture switch swaps the
      tile URL on the existing source; a data refresh swaps the data, and the old
      roads stay drawn until the new ones are ready.
   5. A watchdog re-checks every second and repairs anything missing or out of
      step with the controls, so an unforeseen failure heals itself. */

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

/* Motion budget. Short on purpose: the map should feel immediate, and motion
   only exists to show where things came from. */
const T = {
  majorFade: 110,     // arterial network fades in
  cascade: 340,       // side streets grow outward from the arteries
  cascadeBand: 0.35,  // width of the travelling edge of the cascade
  ease: 150,          // value jumps such as Reset
  camera: 260,        // fit and reset-bearing moves
  rasterFade: 80,     // captured tiles
  waitForData: 1200,  // start the reveal anyway if the data never reports ready
  watchdog: 1000,     // self-repair interval
  infoPoll: 5000,     // how often to look for new data on the server
};
const MINOR_FADE = [9.6, 10.6];      // zoom range the side streets fade in over
const NODE_FADE = [11.6, 13.0];      // junctions fade in over this zoom range
const LINK_FADE = [10.4, 11.6];      // graph edges fade in over this zoom range
const ARROW_MIN_ZOOM = 15;           // symbol placement is the slowest thing on the map
const PARTS = ["major", "minor"];
const GRAPH_PARTS = ["nodes", "links"];

/* Four palettes for the graph. Each is ordered low to high, and every colour
   scale below indexes into the one the menu selected. */
const PALETTES = {
  vivid: ["#0071e3", "#00b894", "#f5a524", "#e5484d", "#8b5cf6"],
  cool:  ["#164e63", "#0e7490", "#22d3ee", "#a5f3fc", "#e0f2fe"],
  warm:  ["#7c2d12", "#c2410c", "#f59e0b", "#fcd34d", "#fef3c7"],
  mono:  ["#2b2b2f", "#5a5a63", "#8c8c96", "#bdbdc6", "#e9e9ef"],
};
const layerId = (part) => `model-${part}`;
const sourceId = (part) => `model-${part}-src`;
const graphLayerId = (part) => `graph-${part}`;
const graphSourceId = (part) => `graph-${part}-src`;
const isOurs = (id) => id === "dim" || id === "traffic" || id.startsWith("model-")
  || id.startsWith("graph-") || id.startsWith("gj:");

const $ = (id) => document.getElementById(id);
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const finite = (v, fallback) => (Number.isFinite(v) ? v : fallback);
const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const state = {
  minZoom: 8, captures: [], capture: null,
  layers: [], activeLayers: new Set(), layerData: {}, layerOpacity: 0.9,
  theme: "light", appliedTheme: "light", themePending: false,
  showMap: true, labels: true, dim: 0,
  origBg: THEME_BG.light, baseLayers: [], baseHidden: new Set(),
  traffic: { show: false, opacity: 1, pure: true },
  model: {
    show: true, which: "all", opacity: 1,
    ready: false, version: "", summary: "", minorReady: false,
    // intro and reveal rest at 1 = fully visible. Only a running animation,
    // which is guaranteed to finish, may hold them lower.
    intro: { major: 1, minor: 1 },
    reveal: { major: 1, minor: 1 },
    revealed: { major: false, minor: false },
    anim: { major: 0, minor: 0 },
    retry: { major: 0, minor: 0 },
  },
  dimPredicted: true,
  colourBy: "level", lineWidth: 1, minCls: 7, lodMinor: true,
  graph: {
    show: false, ready: false, version: "", summary: "",
    nodes: true, links: true, hideCuts: true, arrows: true,
    breathe: true, breatheSpeed: 1, breatheDepth: 0.6, phase: 0,
    linkColour: "class", nodeColour: "degree", palette: "vivid",
    nodeSize: 1, linkWidth: 1, opacity: 1,
  },
  weights: { g: 25, o: 55, r: 85, d: 105 }, jam: 4, beta: 4,
  inspect: false, reloading: false, booted: false,
  timeline: { start: null, slots: 672, index: 0, playing: false, available: new Map(), timer: null },
};

const map = new maplibregl.Map({
  container: "map", style: STYLE_URL.light, center: [90.4125, 23.8075], zoom: 11,
  attributionControl: { compact: true }, fadeDuration: 0,
});
window.__map = map;

/* ═════════════════════════ safety helpers ═════════════════════════ */

const warnings = [];
const events = [];
function note(type, detail) {
  events.push({ t: Math.round(performance.now()), type, ...detail });
  if (events.length > 200) events.shift();
}
function warn(where, err) {
  const msg = `${where}: ${err && err.message ? err.message : err}`;
  warnings.push(msg);
  if (warnings.length > 50) warnings.shift();
  console.warn(`[track] ${msg}`);
}
function attempt(where, fn) {
  try { return fn(); } catch (err) { warn(where, err); return undefined; }
}
function styleReady() {
  const style = map.style;
  if (!style) return false;
  // MapLibre 4 sets `_loaded` once the style spec is in, independent of tiles.
  return typeof style._loaded === "boolean" ? style._loaded : map.loaded();
}
function hasLayer(id) {
  return styleReady() && !!attempt(`getLayer ${id}`, () => map.getLayer(id));
}
function hasSource(id) {
  return styleReady() && !!attempt(`getSource ${id}`, () => map.getSource(id));
}
function setPaint(id, prop, value) {
  if (hasLayer(id)) attempt(`paint ${id}.${prop}`, () => map.setPaintProperty(id, prop, value));
}
function setLayout(id, prop, value) {
  if (hasLayer(id)) attempt(`layout ${id}.${prop}`, () => map.setLayoutProperty(id, prop, value));
}

/* ═════════════════════════ style expressions ═════════════════════════
   The model source carries w (weight on the default scale), s (source),
   c (coverage), h (road class rank), f (free-flow km/h) and o (reveal order).
   Every read has a fallback, so a feature missing a field still draws. */

const num = (key, fallback) => ["to-number", ["coalesce", ["get", key], fallback], fallback];

function remapWeight() {
  const w = state.weights;
  return ["interpolate", ["linear"], num("w", LADDER[0]),
    LADDER[0], finite(w.g, DEFAULTS.g), LADDER[1], finite(w.o, DEFAULTS.o),
    LADDER[2], finite(w.r, DEFAULTS.r), LADDER[3], finite(w.d, DEFAULTS.d)];
}
function alphaOf() {
  // BPR alpha, pinned so the dark-red weight runs `jam` times slower than free flow
  const beta = finite(state.beta, DEFAULTS.beta);
  const vc = Math.max(finite(state.weights.d, DEFAULTS.d), 1) / 100;
  return clamp((finite(state.jam, DEFAULTS.jam) - 1) / Math.pow(vc, beta), 0, 1e4);
}
function delayExpr() {
  return ["+", 1, ["*", alphaOf(), ["^", ["/", remapWeight(), 100], finite(state.beta, DEFAULTS.beta)]]];
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
  const W = interp(finite(weight, LADDER[0]), LADDER, [w.g, w.o, w.r, w.d].map((v, i) =>
    finite(v, [DEFAULTS.g, DEFAULTS.o, DEFAULTS.r, DEFAULTS.d][i])));
  return { weight: W, delay: 1 + alphaOf() * Math.pow(Math.max(W, 0) / 100, finite(state.beta, 4)) };
}
function colourExpr() {
  if (state.colourBy === "delay") {
    return ["interpolate", ["linear"], delayExpr(),
      1, RAMP[0], 1.4, RAMP[1], 2.2, RAMP[2], 3.5, RAMP[3]];
  }
  if (state.colourBy === "speed") {
    return ["interpolate", ["linear"], ["/", num("f", 30), delayExpr()],
      5, RAMP[3], 12, RAMP[2], 25, RAMP[1], 45, RAMP[0]];
  }
  return ["interpolate", ["linear"], num("w", LADDER[0]),
    LADDER[0], RAMP[0], LADDER[1], RAMP[1], LADDER[2], RAMP[2], LADDER[3], RAMP[3]];
}
function widthExpr(minor) {
  const m = clamp(finite(state.lineWidth, 1), 0.2, 6) * (minor ? 0.72 : 1);
  return ["interpolate", ["linear"], ["zoom"],
    10, 0.5 * m, 12, 0.9 * m, 14, 1.7 * m, 16, 3 * m, 18, 6 * m];
}

/* Opacity carries four ideas at once:
   - intro: a quick fade the first time the arterial network appears;
   - reveal: the cascade. Each road carries `o`, its rank by distance out from
     the arteries, and a band sweeps o from 0 to 1 so the side streets grow off
     the main roads instead of switching on at once;
   - a zoom ramp for the side streets, so they arrive gradually;
   - dimming of predicted roads, so what Google measured reads as primary. */
function opacityExpr(part) {
  const base = clamp(finite(state.model.opacity, 1), 0, 1) * clamp(state.model.intro[part], 0, 1);
  let perFeature = state.dimPredicted
    ? ["case", ["==", num("s", 1), 0], base, base * 0.55]
    : base;

  const progress = clamp(state.model.reveal[part], 0, 1);
  if (progress < 1) {
    const hi = progress * (1 + T.cascadeBand);   // roads at or past this are still hidden
    const lo = hi - T.cascadeBand;               // roads at or before this are fully in
    perFeature = ["*", perFeature, ["interpolate", ["linear"], num("o", 0), lo, 1, hi, 0]];
  }

  if (part === "minor" && state.lodMinor) {
    // A "zoom" expression must be the outermost one, so everything per-feature
    // rides in the interpolate's output stops. Multiplying the whole thing
    // instead is rejected by MapLibre, which silently drops the layer.
    return ["interpolate", ["linear"], ["zoom"], MINOR_FADE[0], 0, MINOR_FADE[1], perFeature];
  }
  return perFeature;
}
function modelFilter() {
  const f = ["all", ["<=", num("h", 6), state.minCls]];
  if (state.model.which !== "all") {
    f.push(["==", num("s", 1), state.model.which === "observed" ? 0 : 1]);
  }
  return f;
}
const minzoomFor = (part) => (part === "minor" && state.lodMinor ? MINOR_FADE[0] - 0.2 : 0);

/* ═════════════════════════ layers ═════════════════════════ */

const WORLD = { type: "Feature", geometry: { type: "Polygon",
  coordinates: [[[-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85]]] } };

const trafficUrl = (c) =>
  `${location.origin}/tiles/${encodeURIComponent(c.name)}/{z}/{x}/{y}.png${state.traffic.pure ? "?clean=1" : ""}`;
const modelUrl = (part) =>
  `${location.origin}/model.geojson?part=${part}&v=${encodeURIComponent(state.model.version || "0")}`;

let ensuring = false;
/* Add whatever should exist and does not. Idempotent, safe to call any time. */
function ensureLayers() {
  if (ensuring || !styleReady()) return false;
  ensuring = true;
  let added = false;
  try {
    if (!hasSource("dim")) map.addSource("dim", { type: "geojson", data: WORLD });
    if (!hasLayer("dim")) {
      map.addLayer({ id: "dim", type: "fill", source: "dim",
        paint: { "fill-color": THEME_BG[state.theme], "fill-opacity": state.showMap ? state.dim : 0 } });
      added = true;
    }
    if (state.capture) added = addTraffic() || added;
    if (state.model.ready) {
      added = addModelPart("major") || added;
      if (state.model.minorReady) added = addModelPart("minor") || added;
    }
    if (state.graph.ready && state.graph.show) {
      for (const part of GRAPH_PARTS) added = addGraphPart(part) || added;
    }
    for (const l of state.layers) {
      if (state.activeLayers.has(l.id) && state.layerData[l.id] && !hasLayer(geoIds(l.id).line)) {
        addGeoJsonLayer(l);
        added = true;
      }
    }
  } catch (err) {
    warn("ensureLayers", err);
  } finally {
    ensuring = false;
  }
  if (added) reconcile();
  return added;
}

function addTraffic() {
  const c = state.capture;
  let added = false;
  if (!hasSource("traffic")) {
    map.addSource("traffic", { type: "raster", tileSize: 256, minzoom: state.minZoom,
      maxzoom: c.zoom || 17, tiles: [trafficUrl(c)] });
    added = true;
  }
  if (!hasLayer("traffic")) {
    map.addLayer({ id: "traffic", type: "raster", source: "traffic",
      paint: { "raster-opacity": clamp(state.traffic.opacity, 0, 1), "raster-resampling": "nearest",
               "raster-fade-duration": T.rasterFade },
      layout: { visibility: state.traffic.show ? "visible" : "none" } });
    added = true;
  }
  return added;
}

/* Point the capture layer at another capture, or at the pure-colour variant,
   without removing it. The current tiles stay on screen until the new ones
   arrive; removing and re-adding left frames with no capture at all. */
function syncTrafficTiles() {
  if (!styleReady() || !state.capture) return;
  const src = attempt("getSource traffic", () => map.getSource("traffic"));
  if (!src) { ensureLayers(); return; }
  const url = trafficUrl(state.capture);
  if (src.maxzoom === (state.capture.zoom || 17) && typeof src.setTiles === "function") {
    const current = Array.isArray(src.tiles) ? src.tiles[0] : null;
    if (current !== url) attempt("setTiles", () => src.setTiles([url]));
    return;
  }
  // a capture at a different native zoom needs a new source
  attempt("rebuild traffic", () => {
    if (map.getLayer("traffic")) map.removeLayer("traffic");
    if (map.getSource("traffic")) map.removeSource("traffic");
  });
  ensureLayers();
}

function addModelPart(part) {
  let added = false;
  if (!hasSource(sourceId(part))) {
    // maxzoom 14 keeps client-side tiling cheap; lines stay crisp above it
    // because vectors are re-projected, not resampled.
    map.addSource(sourceId(part), { type: "geojson", data: modelUrl(part),
      maxzoom: 14, buffer: 32, tolerance: 0.375 });
    added = true;
  }
  if (!hasLayer(layerId(part))) {
    const firstShow = !state.model.revealed[part];
    if (firstShow) primeReveal(part);
    map.addLayer({ id: layerId(part), type: "line", source: sourceId(part),
      minzoom: minzoomFor(part), filter: modelFilter(),
      layout: { "line-cap": "round", "line-join": "round",
                visibility: state.model.show ? "visible" : "none" },
      paint: { "line-color": colourExpr(), "line-width": widthExpr(part === "minor"),
               "line-opacity": opacityExpr(part) } });
    added = true;
    if (firstShow) playReveal(part);
  }
  return added;
}

/* ═════════════════════════ road graph ═════════════════════════
   The network A* actually walks: junctions, and one line per directed edge. */

const graphUrl = (part) =>
  `${location.origin}/graph.geojson?part=${part}&v=${encodeURIComponent(state.graph.version || "0")}`;
const pal = () => PALETTES[state.graph.palette] || PALETTES.vivid;

/* A pulse travelling along each segment in its direction of travel.

   This is a moving DASH, not a gradient. `line-gradient` would be the obvious
   way to do it, but it requires `lineMetrics` on the source, which makes
   MapLibre measure distance along all 209,847 coordinates of the graph, and
   re-writing the gradient each frame kept that work running so the map never
   settled. A dash pattern costs nothing per coordinate: shifting the gap
   through a short cycle makes the dashes run along each line, and because the
   line's coordinates are stored in its direction of travel, they run that way.

   PULSE_STEPS positions is all a dash cycle can show, so the animation steps
   between them ~20 times a second instead of once per frame. */
const PULSE_STEPS = 8;
function dashFor(step, depth) {
  // dash, gap, dash, gap ... in line widths. A long lit run with a short dark
  // gap that advances one slot each step, which reads as movement.
  const gap = 0.6 + 2.4 * clamp(depth, 0, 1);      // deeper = more visible gap
  const lit = 5;
  const cycle = lit + gap;
  const offset = (step % PULSE_STEPS) / PULSE_STEPS * cycle;
  const head = Math.max(0.05, lit - offset);
  return offset <= 0.1 ? [lit, gap] : [head, gap, Math.max(0.1, offset), 0.1];
}
function linkColourExpr() {
  const c = pal();
  const mode = state.graph.linkColour;
  if (mode === "flat") return c[0];
  if (mode === "direction") {       // one-way against two-way
    return ["case", ["==", ["to-number", ["coalesce", ["get", "o"], 0], 0], 1], c[3], c[1]];
  }
  if (mode === "length") {
    return ["interpolate", ["linear"], ["to-number", ["coalesce", ["get", "l"], 0], 0],
      20, c[0], 60, c[1], 110, c[2], 150, c[3]];
  }
  return ["interpolate", ["linear"], ["to-number", ["coalesce", ["get", "r"], 5], 5],
    0, c[4], 2, c[3], 4, c[2], 6, c[1], 7, c[0]];
}
function nodeColourExpr() {
  const c = pal();
  const mode = state.graph.nodeColour;
  if (mode === "flat") return c[2];
  if (mode === "kind") {
    return ["case", ["==", ["to-number", ["coalesce", ["get", "x"], 0], 0], 1], c[0], c[3]];
  }
  if (mode === "class") {
    return ["interpolate", ["linear"], ["to-number", ["coalesce", ["get", "r"], 5], 5],
      0, c[4], 2, c[3], 4, c[2], 6, c[1], 7, c[0]];
  }
  return ["interpolate", ["linear"], ["to-number", ["coalesce", ["get", "d"], 1], 1],
    1, c[0], 2, c[1], 3, c[2], 4, c[3], 5, c[4]];
}
function graphFilter(part) {
  if (part === "nodes" && state.graph.hideCuts) {
    return ["!=", ["to-number", ["coalesce", ["get", "x"], 0], 0], 1];
  }
  return null;
}
function graphOpacityExpr(part) {
  const base = clamp(finite(state.graph.opacity, 1), 0, 1);
  const fade = part === "nodes" ? NODE_FADE : LINK_FADE;
  return ["interpolate", ["linear"], ["zoom"], fade[0], 0, fade[1], base];
}
function nodeRadiusExpr() {
  const m = clamp(finite(state.graph.nodeSize, 1), 0.2, 6);
  return ["interpolate", ["linear"], ["zoom"],
    11, 0.8 * m, 13, 1.8 * m, 15, 3.2 * m, 17, 5.5 * m];
}
function graphWidthExpr() {
  const m = clamp(finite(state.graph.linkWidth, 1), 0.2, 6);
  return ["interpolate", ["linear"], ["zoom"], 10, 0.5 * m, 13, 1.2 * m, 16, 2.6 * m, 18, 4.5 * m];
}

function addGraphPart(part) {
  let added = false;
  const sid = graphSourceId(part);
  if (!hasSource(sid)) {
    // no lineMetrics: measuring distance along every coordinate is what made
    // the animated version of this layer unable to settle
    map.addSource(sid, { type: "geojson", data: graphUrl(part), maxzoom: 14, buffer: 32,
      tolerance: 0.375 });
    added = true;
  }
  const lid = graphLayerId(part);
  if (!hasLayer(lid)) {
    const visible = state.graph.show && state.graph[part] ? "visible" : "none";
    if (part === "nodes") {
      map.addLayer({ id: lid, type: "circle", source: sid, minzoom: NODE_FADE[0] - 0.2,
        filter: graphFilter("nodes"),
        layout: { visibility: visible },
        paint: { "circle-color": nodeColourExpr(), "circle-radius": nodeRadiusExpr(),
                 "circle-opacity": graphOpacityExpr("nodes"),
                 "circle-stroke-width": 0 } });
    } else {
      map.addLayer({ id: lid, type: "line", source: sid, minzoom: LINK_FADE[0] - 0.2,
        layout: { "line-cap": "butt", "line-join": "round", visibility: visible },
        paint: { "line-color": linkColourExpr(), "line-width": graphWidthExpr(),
                 "line-opacity": graphOpacityExpr("links"),
                 "line-dasharray": dashFor(0, state.graph.breathe ? state.graph.breatheDepth : 0) } });
    }
    added = true;
  }
  if (part === "links" && !hasLayer("graph-arrows")) {
    // One-way segments only, close in only, and with collision checks off:
    // placing labels along every line in view is what stalls the map.
    map.addLayer({ id: "graph-arrows", type: "symbol", source: sid, minzoom: ARROW_MIN_ZOOM,
      filter: ["==", ["to-number", ["coalesce", ["get", "o"], 0], 0], 1],
      layout: {
        visibility: state.graph.show && state.graph.links && state.graph.arrows ? "visible" : "none",
        "symbol-placement": "line", "symbol-spacing": 70,
        // CARTO's glyph set has no U+25B8 triangle: asking for it placed zero
        // arrows with no error at all. A plain ">" in a font the basemap really
        // serves does render, so name the glyph and the font explicitly.
        "text-field": ">", "text-font": ["Open Sans Regular", "Arial Unicode MS Regular"],
        "text-size": 15, "text-rotation-alignment": "map",
        "text-keep-upright": false, "text-allow-overlap": true, "text-ignore-placement": true,
      },
      paint: { "text-color": linkColourExpr(), "text-opacity": graphOpacityExpr("links") } });
    added = true;
  }
  return added;
}

function applyGraph() {
  for (const part of GRAPH_PARTS) {
    const lid = graphLayerId(part);
    if (!hasLayer(lid)) continue;
    setLayout(lid, "visibility", state.graph.show && state.graph[part] ? "visible" : "none");
    if (part === "nodes") {
      setPaint(lid, "circle-color", nodeColourExpr());
      setPaint(lid, "circle-radius", nodeRadiusExpr());
      setPaint(lid, "circle-opacity", graphOpacityExpr("nodes"));
      attempt("graph filter", () => map.setFilter(lid, graphFilter("nodes")));
    } else {
      setPaint(lid, "line-color", linkColourExpr());
      setPaint(lid, "line-width", graphWidthExpr());
      setPaint(lid, "line-opacity", graphOpacityExpr("links"));
    }
  }
  if (hasLayer("graph-arrows")) {
    setLayout("graph-arrows", "visibility",
      state.graph.show && state.graph.links && state.graph.arrows ? "visible" : "none");
    setPaint("graph-arrows", "text-color", linkColourExpr());
    setPaint("graph-arrows", "text-opacity", graphOpacityExpr("links"));
  }
  applyBreath();
  syncBreathing();
}
function applyBreath() {
  const id = graphLayerId("links");
  if (!hasLayer(id)) return;
  const g = state.graph;
  const depth = clamp(finite(g.breatheDepth, 0.6), 0, 1);
  if (!g.breathe || depth <= 0) {
    setPaint(id, "line-dasharray", [1]);           // solid
    return;
  }
  setPaint(id, "line-dasharray", dashFor(Math.round(g.phase), depth));
}
let breathFrame = 0;
let breathLast = 0;
function breathTick(now) {
  breathFrame = 0;
  if (!breathingWanted()) return;
  const rate = 20 * clamp(finite(state.graph.breatheSpeed, 1), 0.05, 4);
  if (now - breathLast >= 1000 / rate) {
    breathLast = now;
    state.graph.phase = (state.graph.phase + 1) % PULSE_STEPS;
    applyBreath();
  }
  breathFrame = requestAnimationFrame(breathTick);
}
function breathingWanted() {
  return state.graph.show && state.graph.links && state.graph.breathe
    && state.graph.breatheDepth > 0 && !document.hidden && hasLayer(graphLayerId("links"));
}
function syncBreathing() {
  if (breathingWanted()) {
    if (!breathFrame) breathFrame = requestAnimationFrame(breathTick);
  } else if (breathFrame) {
    cancelAnimationFrame(breathFrame);
    breathFrame = 0;
    applyBreath();                      // settle on the plain colour
  }
}
document.addEventListener("visibilitychange", syncBreathing);

async function fetchGraphInfo() {
  try {
    const r = await fetch("/api/graph", { cache: "no-store" });
    if (!r.ok) return { ready: false, error: `server answered ${r.status}` };
    return await r.json();
  } catch {
    return { ready: false, error: "server unreachable" };
  }
}
function applyGraphInfo(g) {
  const controls = ["showGraph", "showNodes", "showLinks", "hideCuts", "showArrows", "breathe",
                    "breatheSpeed", "breatheDepth", "nodeSize", "linkWidth", "graphOpacity"];
  if (g && g.ready) {
    state.graph.ready = true;
    if (g.version) state.graph.version = g.version;
    state.graph.summary =
      `<span>${Number(g.nodes || 0).toLocaleString()} nodes · ${Number(g.junctions || 0).toLocaleString()} junctions · ` +
      `${Number(g.cut_points || 0).toLocaleString()} cut points</span><br>` +
      `${Number(g.links || 0).toLocaleString()} segments from ${Number(g.edges || 0).toLocaleString()} directed edges · ` +
      `${Number(g.oneway || 0).toLocaleString()} one-way · ` +
      `${((g.bytes || 0) / 1048576).toFixed(1)} MB`;
    $("graphInfo").innerHTML = state.graph.summary;
    controls.forEach((id) => { if ($(id)) $(id).disabled = false; });
    document.querySelectorAll("#linkColour button, #nodeColour button, #graphPalette button")
      .forEach((b) => { b.disabled = false; });
    return;
  }
  const reason = (g && g.error) || "not built";
  if (state.graph.ready) {
    $("graphInfo").innerHTML = state.graph.summary +
      `<br><span class="warn">Keeping the graph on screen: ${escapeHtml(reason)}</span>`;
    return;
  }
  controls.forEach((id) => { if ($(id)) $(id).disabled = true; });
  document.querySelectorAll("#linkColour button, #nodeColour button, #graphPalette button")
    .forEach((b) => { b.disabled = true; });
  $("graphInfo").innerHTML = `no graph: ${escapeHtml(reason)}.<br>Run <b>python pipeline.py</b> in the algorithms folder.`;
}

/* ═════════════════════════ reveal animation ═════════════════════════
   Plays once per part per page load. Guarantees, in order: a hidden tab skips
   it; a stale token stops it; any exception finishes it; and a timer finishes
   it even if animation frames never arrive. There is no path that ends with a
   layer held below full opacity. */

let revealSeq = 0;
function primeReveal(part) {
  if (part === "minor") state.model.reveal.minor = 0;
  else state.model.intro.major = 0;
}
function playReveal(part) {
  if (document.hidden) { finishReveal(part); return; }
  const token = ++revealSeq;
  state.model.anim[part] = token;
  const dur = part === "minor" ? T.cascade : T.majorFade;
  const live = () => state.model.anim[part] === token;
  const queued = performance.now();
  let begun = 0;
  note("reveal-queued", { part });
  const guard = setTimeout(() => { if (live()) { note("reveal-forced", { part }); finishReveal(part); } },
    T.waitForData + dur + 400);
  const step = (now) => {
    if (!live()) return;
    try {
      if (!hasLayer(layerId(part))) { clearTimeout(guard); finishReveal(part); return; }
      if (!begun) {
        const ready = hasSource(sourceId(part))
          && !!attempt("isSourceLoaded", () => map.isSourceLoaded(sourceId(part)));
        if (!ready && now - queued < T.waitForData) { requestAnimationFrame(step); return; }
        begun = now;
        note("reveal-start", { part });
      }
      const k = Math.min(1, (now - begun) / dur);
      const eased = 1 - (1 - k) * (1 - k);                  // ease-out: quick start, soft landing
      if (part === "minor") state.model.reveal.minor = eased;
      else state.model.intro.major = eased;
      applyModelOpacity();
      if (k < 1) requestAnimationFrame(step);
      else { clearTimeout(guard); finishReveal(part); }
    } catch (err) {
      clearTimeout(guard);
      warn(`reveal ${part}`, err);
      finishReveal(part);
    }
  };
  requestAnimationFrame(step);
}
function finishReveal(part) {
  const wasRunning = !!state.model.anim[part];
  state.model.anim[part] = 0;
  state.model.revealed[part] = true;
  state.model.intro[part] = 1;
  state.model.reveal[part] = 1;
  applyModelOpacity();
  if (wasRunning) note("reveal-done", { part });
}
function finishAllReveals() {
  for (const part of PARTS) {
    if (state.model.anim[part] || state.model.intro[part] < 1 || state.model.reveal[part] < 1) finishReveal(part);
  }
}
document.addEventListener("visibilitychange", () => { if (document.hidden) finishAllReveals(); });

/* The side streets are the bulk of the data. Hold them back until the arterial
   network has drawn, so the map is useful in about a second. */
function releaseMinor() {
  if (state.model.minorReady || !state.model.ready) return;
  state.model.minorReady = true;
  ensureLayers();
}
map.on("sourcedata", (e) => {
  if (!e || !e.sourceId || !e.sourceId.startsWith("model-")) return;
  const loaded = e.isSourceLoaded !== undefined ? e.isSourceLoaded
    : !!attempt("isSourceLoaded", () => map.isSourceLoaded(e.sourceId));
  if (!loaded) return;
  const part = e.sourceId === sourceId("major") ? "major" : "minor";
  state.model.retry[part] = 0;
  if (part === "major") releaseMinor();
});

/* A failed data load (server restarting, network blip) is retried with backoff.
   The roads already on screen stay there meanwhile. */
const retryTimers = {};
function scheduleSourceRetry(part) {
  if (retryTimers[part]) return;
  const n = state.model.retry[part] = Math.min(state.model.retry[part] + 1, 6);
  retryTimers[part] = setTimeout(async () => {
    retryTimers[part] = 0;
    const info = await fetchModelInfo();
    if (!info.ready) { scheduleSourceRetry(part); return; }
    applyModelInfo(info);
    if (hasSource(sourceId(part))) {
      attempt(`retry ${part}`, () => map.getSource(sourceId(part)).setData(modelUrl(part)));
    } else {
      ensureLayers();
    }
  }, Math.min(15000, 500 * 2 ** (n - 1)));
}
map.on("error", (e) => {
  const sid = e && e.sourceId;
  warn(sid ? `source ${sid}` : "map", (e && e.error) || e);
  if (sid && sid.startsWith("model-")) scheduleSourceRetry(sid === sourceId("major") ? "major" : "minor");
});

/* ═════════════════════════ applying state ═════════════════════════ */

function reconcile() {
  if (!styleReady()) return;
  applyBase(); applyTraffic(); applyModel(); applyModelStyle(); applyGraph();
  applyLayerOpacity(); orderLayers();
}
function applyBase() {
  if (!styleReady()) return;
  for (const l of state.baseLayers) {
    if (l.type === "background") {
      setPaint(l.id, "background-color", state.showMap ? state.origBg : THEME_BG[state.theme]);
      continue;
    }
    const visible = state.showMap && !state.baseHidden.has(l.id) && !(l.type === "symbol" && !state.labels);
    setLayout(l.id, "visibility", visible ? "visible" : "none");
  }
  setPaint("dim", "fill-color", THEME_BG[state.theme]);
  setPaint("dim", "fill-opacity", state.showMap ? state.dim : 0);
  setRowEnabled("labels", state.showMap);
  setRowEnabled("dim", state.showMap);
}
function applyTraffic() {
  setLayout("traffic", "visibility", state.traffic.show ? "visible" : "none");
  setPaint("traffic", "raster-opacity", clamp(finite(state.traffic.opacity, 1), 0, 1));
}
function applyModelOpacity() {
  for (const part of PARTS) setPaint(layerId(part), "line-opacity", opacityExpr(part));
}
function applyModel() {
  for (const part of PARTS) {
    const id = layerId(part);
    if (!hasLayer(id)) continue;
    setLayout(id, "visibility", state.model.show ? "visible" : "none");
    attempt(`zoomRange ${id}`, () => map.setLayerZoomRange(id, minzoomFor(part), 24));
  }
  applyModelOpacity();
}
function applyModelStyle() {          // the live part: colour, width, filter and opacity
  const colour = colourExpr();
  const filter = modelFilter();
  for (const part of PARTS) {
    const id = layerId(part);
    if (!hasLayer(id)) continue;
    setPaint(id, "line-color", colour);
    setPaint(id, "line-width", widthExpr(part === "minor"));
    setPaint(id, "line-opacity", opacityExpr(part));
    attempt(`filter ${id}`, () => map.setFilter(id, filter));
  }
  renderLegend();
  renderCalc();
}
function applyLayerOpacity() {
  for (const id of state.activeLayers) {
    const ids = geoIds(id);
    setPaint(ids.line, "line-opacity", state.layerOpacity);
    setPaint(ids.pt, "circle-opacity", state.layerOpacity);
    setPaint(ids.fill, "fill-opacity", state.layerOpacity * 0.3);
  }
}
function layerOrder() {
  if (typeof map.getLayersOrder === "function") return attempt("getLayersOrder", () => map.getLayersOrder()) || [];
  if (map.style && Array.isArray(map.style._order)) return map.style._order.slice();
  const st = attempt("getStyle", () => map.getStyle());
  return st ? st.layers.map((l) => l.id) : [];
}
/* Final stacking: basemap, dim, capture, side streets, arteries, algorithm layers. */
function orderLayers() {
  if (!styleReady()) return;
  const order = layerOrder();
  const want = ["dim", "traffic", layerId("minor"), layerId("major"),
                graphLayerId("links"), graphLayerId("nodes"), "graph-arrows"]
    .filter((id) => order.includes(id))
    .concat(order.filter((id) => id.startsWith("gj:")));
  if (!want.length) return;
  if (order.slice(order.length - want.length).join("|") === want.join("|")) return;
  for (const id of want) attempt(`moveLayer ${id}`, () => map.moveLayer(id));
}
function setRowEnabled(inputId, on) {
  const row = $(inputId) && $(inputId).closest(".row");
  if (row) row.classList.toggle("off", !on);
}

/* Slider events arrive far faster than the map repaints 60 k lines. Collapse
   them to one restyle per frame; the numbers beside the sliders update at once. */
let styleFrame = 0;
function requestStyleApply() {
  renderLegend();
  renderCalc();
  if (styleFrame) return;
  styleFrame = requestAnimationFrame(() => { styleFrame = 0; applyModelStyle(); });
}
let opacityFrame = 0;
function requestModelOpacity() {
  if (opacityFrame) return;
  opacityFrame = requestAnimationFrame(() => { opacityFrame = 0; applyModelOpacity(); });
}

/* A value that jumps rather than slides (Reset) travels there briefly. */
let easeFrame = 0;
function easeWeightsTo(target, dur = T.ease) {
  cancelAnimationFrame(easeFrame);
  const apply = (e, from) => {
    for (const key of ["g", "o", "r", "d"]) state.weights[key] = from[key] + (target[key] - from[key]) * e;
    state.jam = from.jam + (target.jam - from.jam) * e;
    state.beta = from.beta + (target.beta - from.beta) * e;
    syncWeightInputs();
    applyModelStyle();
  };
  const from = { ...state.weights, jam: state.jam, beta: state.beta };
  if (document.hidden) { apply(1, from); return; }
  const t0 = performance.now();
  const step = (now) => {
    try {
      const k = Math.min(1, (now - t0) / dur);
      apply(1 - (1 - k) * (1 - k), from);
      if (k < 1) easeFrame = requestAnimationFrame(step);
    } catch (err) {
      warn("ease", err);
      apply(1, from);
    }
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
  if (!hasSource(ids.src)) map.addSource(ids.src, { type: "geojson", data: state.layerData[l.id] });
  const color = ["case", ["has", "color"], ["get", "color"],
    ["has", "cls"], ["match", ["get", "cls"], 1, RAMP[0], 2, RAMP[1], 3, RAMP[2], 4, RAMP[3], "#9e9e9e"],
    "#0071e3"];
  if (!hasLayer(ids.fill)) {
    map.addLayer({ id: ids.fill, type: "fill", source: ids.src, filter: ["==", ["geometry-type"], "Polygon"],
      paint: { "fill-color": color, "fill-opacity": state.layerOpacity * 0.3 } });
  }
  if (!hasLayer(ids.line)) {
    map.addLayer({ id: ids.line, type: "line", source: ids.src,
      filter: ["any", ["==", ["geometry-type"], "LineString"], ["==", ["geometry-type"], "Polygon"]],
      paint: { "line-color": color, "line-width": ["case", ["has", "width"], ["get", "width"], 3],
               "line-opacity": state.layerOpacity },
      layout: { "line-cap": "round", "line-join": "round" } });
  }
  if (!hasLayer(ids.pt)) {
    map.addLayer({ id: ids.pt, type: "circle", source: ids.src, filter: ["==", ["geometry-type"], "Point"],
      paint: { "circle-color": color, "circle-radius": 5, "circle-opacity": state.layerOpacity,
               "circle-stroke-color": "#fff", "circle-stroke-width": 1.5 } });
  }
}
function removeGeoJsonLayer(id) {
  const ids = geoIds(id);
  attempt(`remove ${id}`, () => {
    for (const k of [ids.pt, ids.line, ids.fill]) if (map.getLayer(k)) map.removeLayer(k);
    if (map.getSource(ids.src)) map.removeSource(ids.src);
  });
}
async function loadLayerList() {
  try { state.layers = await (await fetch("/api/layers")).json(); } catch { state.layers = []; }
  const box = $("layers");
  if (!Array.isArray(state.layers) || !state.layers.length) {
    state.layers = [];
    box.innerHTML = '<p class="hint">none yet — pipeline outputs appear here</p>';
    return;
  }
  box.innerHTML = "";
  for (const l of state.layers) {
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML = `<span>${escapeHtml(l.name || l.id)}</span>
      <label class="switch sm"><input type="checkbox"><span></span></label>`;
    const cb = row.querySelector("input");
    cb.onchange = async () => {
      if (cb.checked) {
        state.activeLayers.add(l.id);
        if (!state.layerData[l.id]) {
          try {
            state.layerData[l.id] = await (await fetch(l.url)).json();
          } catch (err) {
            warn(`layer ${l.id}`, err);
            state.activeLayers.delete(l.id);
            cb.checked = false;
            return;
          }
        }
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

function fitCapture() {
  const c = state.capture;
  if (!c || !c.bbox) return;
  attempt("fitBounds", () => map.fitBounds([[c.bbox.west, c.bbox.south], [c.bbox.east, c.bbox.north]],
    { padding: 40, duration: T.camera }));
}
function selectCapture(name, fit) {
  const next = state.captures.find((c) => c.name === name) || null;
  const changed = !state.capture || !next || state.capture.name !== next.name;
  state.capture = next;
  if (!next) { $("capInfo").textContent = "no captures found"; return; }
  if (changed) {
    if (hasSource("traffic")) syncTrafficTiles();
    else ensureLayers();
  }
  const when = (next.captured_utc || "").replace("T", " ").replace("Z", "");
  $("capInfo").innerHTML = `${escapeHtml(next.status)} · z${next.zoom} · ${next.coverage_pct}% coverage · ` +
    `${next.tiles_nonempty ?? "?"} of ${next.expected_tiles ?? "?"} tiles carry traffic<br>${escapeHtml(when)} UTC`;
  if ($("capture").value !== name) $("capture").value = name;
  if (fit) fitCapture();
}
async function loadCaptures() {
  try { state.captures = await (await fetch("/api/captures")).json(); } catch { state.captures = []; }
  if (!Array.isArray(state.captures)) state.captures = [];
  const sel = $("capture");
  sel.innerHTML = "";
  for (const c of state.captures) {
    const o = document.createElement("option");
    o.value = c.name;
    o.textContent = c.name;
    sel.appendChild(o);
  }
  if (state.captures.length) {
    const newest = state.captures.reduce((a, b) =>
      (Date.parse(b.captured_utc) || 0) > (Date.parse(a.captured_utc) || 0) ? b : a);
    sel.value = newest.name;
    selectCapture(newest.name, true);
  }
}

/* ═════════════════════════ model data ═════════════════════════ */

async function fetchModelInfo() {
  try {
    const r = await fetch("/api/model", { cache: "no-store" });
    if (!r.ok) return { ready: false, error: `server answered ${r.status}` };
    return await r.json();
  } catch {
    return { ready: false, error: "server unreachable" };
  }
}
/* Apply what the server says. Once data has been shown, a failed or empty
   answer only adds a note: it never switches the model off or drops its roads.
   Returns true when the server now has a different version of the data. */
function applyModelInfo(m, { boot = false } = {}) {
  const controls = ["showModel", "modelOpacity", "abFlip", "lineWidth", "minCls", "lodMinor", "dimPredicted"];
  if (m && m.ready) {
    const changed = !!state.model.version && !!m.version && m.version !== state.model.version;
    state.model.ready = true;
    if (m.version) state.model.version = m.version;
    const pct = m.lines ? Math.round((100 * m.observed) / m.lines) : 0;
    state.model.summary =
      `${Number(m.lines || 0).toLocaleString()} roads · ${Number(m.observed || 0).toLocaleString()} observed (${pct}%) · ` +
      `${Number(m.predicted || 0).toLocaleString()} predicted<br>` +
      `${Number(m.edges || 0).toLocaleString()} directed edges · ${((m.bytes || 0) / 1048576).toFixed(1)} MB from ${escapeHtml(m.weights || "")}`;
    $("modelInfo").innerHTML = state.model.summary + (m.stale && m.error
      ? `<br><span class="warn">Serving the last complete data: ${escapeHtml(m.error)}</span>` : "");
    controls.forEach((id) => { if ($(id)) $(id).disabled = false; });
    document.querySelectorAll("#modelWhich button, #colourBy button").forEach((b) => { b.disabled = false; });
    return changed;
  }
  const reason = (m && m.error) || "not built";
  if (state.model.ready) {
    $("modelInfo").innerHTML = (state.model.summary || "") +
      `<br><span class="warn">Keeping the data on screen: ${escapeHtml(reason)}</span>`;
    return false;
  }
  controls.forEach((id) => { if ($(id)) $(id).disabled = true; });
  document.querySelectorAll("#modelWhich button, #colourBy button").forEach((b) => { b.disabled = true; });
  $("modelInfo").innerHTML = `no model data: ${escapeHtml(reason)}.<br>Run <b>python pipeline.py</b> in the algorithms folder.`;
  if (boot) {
    state.model.show = false;
    state.traffic.show = true;
  }
  return false;
}
/* Swap in new data on the existing sources. The old roads stay drawn until the
   new tiles are ready, so a refresh never blanks the map. */
function refreshModelData() {
  for (const part of PARTS) {
    if (!hasSource(sourceId(part))) continue;
    attempt(`setData ${part}`, () => map.getSource(sourceId(part)).setData(modelUrl(part)));
  }
  note("data-refresh", { version: state.model.version });
}

/* The fallback button: settle any animation, pick up new data if the server has
   it, re-apply every control, repaint. Never hides anything on the way. */
async function reloadModel() {
  if (state.reloading) return;
  state.reloading = true;
  const btn = $("reloadModel");
  if (!btn.dataset.label) btn.dataset.label = btn.innerHTML;
  clearTimeout(btn._restore);
  btn.disabled = true;
  btn.textContent = "Reloading…";
  cancelAnimationFrame(easeFrame);
  let outcome = "Up to date";
  try {
    let info = await fetchModelInfo();
    // the server may still be settling or rebuilding after a pipeline run
    const deadline = performance.now() + 20000;
    while (info.ready && (info.pending || info.building) && performance.now() < deadline) {
      btn.textContent = "Waiting for new data…";
      await new Promise((r) => setTimeout(r, 500));
      info = await fetchModelInfo();
    }
    const changed = applyModelInfo(info);
    if (!info.ready) {
      outcome = state.model.ready ? "Server unavailable · kept data" : "No data";
    } else if (changed || state.model.retry.major || state.model.retry.minor) {
      refreshModelData();
      outcome = changed ? "Loaded new data" : "Reloaded";
    }
    finishAllReveals();
    ensureLayers();
    reconcile();
    attempt("repaint", () => map.triggerRepaint());
  } catch (err) {
    warn("reload", err);
    outcome = "Reload failed · kept data";
  } finally {
    state.reloading = false;
    btn.disabled = false;
    btn.textContent = outcome;
    btn._restore = setTimeout(() => { btn.innerHTML = btn.dataset.label; }, 1400);
  }
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
  if (!state.inspect || !styleReady()) return;
  const nodeLayer = [graphLayerId("nodes")].filter(hasLayer);
  const box = [[e.point.x - 6, e.point.y - 6], [e.point.x + 6, e.point.y + 6]];
  // a junction under the pointer wins: it is the smaller target
  const nodeHit = nodeLayer.length
    ? (attempt("inspect node", () => map.queryRenderedFeatures(box, { layers: nodeLayer })) || [])[0]
    : null;
  if (nodeHit) { showJunction(nodeHit.properties || {}); return; }
  const layers = PARTS.map(layerId).filter(hasLayer);
  if (!layers.length) return;
  const hits = attempt("inspect query", () => map.queryRenderedFeatures(box, { layers })) || [];
  if (!hits.length) { $("inspect").hidden = true; return; }
  const p = hits[0].properties || {};
  const base = finite(Number(p.w), LADDER[0]);
  const { weight, delay } = delayAt(base);
  const free = finite(Number(p.f), 30);
  const changed = weight.toFixed(0) !== base.toFixed(0);
  $("inspectBody").innerHTML =
    `<h4><i style="background:${rampColour(base)}"></i>${escapeHtml(CLASS_NAMES[p.h] || "road")}</h4>
     <dl>
       <dt>Source</dt><dd><span class="tag">${Number(p.s) === 0 ? "observed" : "predicted"}</span></dd>
       <dt>Weight</dt><dd>${base.toFixed(0)}${changed ? ` → ${weight.toFixed(0)}` : ""}</dd>
       ${p.wb !== undefined ? `<dt>Other way</dt><dd>${Number(p.wb).toFixed(0)}` +
          `${p.sb ? ' <span class="tag">predicted</span>' : ""}</dd>` : ""}
       <dt>v/c ratio</dt><dd>${(weight / 100).toFixed(2)}</dd>
       <dt>Coverage</dt><dd>${(finite(Number(p.c), 0) * 100).toFixed(0)}%</dd>
       <dt>Free flow</dt><dd>${free.toFixed(0)} km/h</dd>
       <dt>Slowdown</dt><dd>${delay.toFixed(2)}x</dd>
       <dt>Effective</dt><dd>${(free / delay).toFixed(0)} km/h</dd>
       <dt>Edge id</dt><dd>${escapeHtml(p.i)}</dd>
     </dl>`;
  $("inspect").hidden = false;
});

const DEGREE_NAME = { 1: "dead end", 2: "pass-through", 3: "T junction", 4: "crossroads" };
function showJunction(p) {
  const degree = Number(p.d) || 0;
  const cut = Number(p.x) === 1;
  $("inspectBody").innerHTML =
    `<h4><i style="background:${pal()[clamp(degree - 1, 0, 4)]}"></i>${cut ? "Cut point" : (DEGREE_NAME[degree] || "junction")}</h4>
     <dl>
       <dt>Kind</dt><dd><span class="tag">${cut ? "150 m split" : "intersection"}</span></dd>
       <dt>Roads here</dt><dd>${degree}</dd>
       <dt>Biggest road</dt><dd>${escapeHtml(CLASS_NAMES[p.r] || "unknown")}</dd>
       <dt>Node id</dt><dd>${escapeHtml(p.n)}</dd>
     </dl>`;
  $("inspect").hidden = false;
}

/* ═════════════════════════ timeline ═════════════════════════
   A week of 15-minute slots. Only slots with a real capture carry data; the
   rest are the shape the weekly collection will fill in. */

const SLOT_MS = 15 * 60 * 1000;
const DHAKA_OFFSET = 6 * 3600 * 1000;          // captures are stamped UTC, Dhaka is UTC+6
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function buildTimeline() {
  const tl = state.timeline;
  const stamps = state.captures.map((c) => Date.parse(c.captured_utc)).filter((t) => !isNaN(t));
  const anchor = stamps.length ? Math.max(...stamps) : Date.now();
  const d = new Date(anchor + DHAKA_OFFSET);
  const dow = (d.getUTCDay() + 6) % 7;
  const monday = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) - dow * 86400000;
  tl.start = monday - DHAKA_OFFSET;

  tl.available.clear();
  for (const c of state.captures) {
    const t = Date.parse(c.captured_utc);
    if (isNaN(t)) continue;
    const idx = Math.round((t - tl.start) / SLOT_MS);
    if (idx >= 0 && idx < tl.slots) tl.available.set(idx, c.name);
  }
  const here = [...tl.available.entries()].find(([, n]) => n === (state.capture && state.capture.name));
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
  if (!Number.isFinite(i)) return;
  tl.index = clamp(Math.round(i), 0, tl.slots - 1);
  const name = tl.available.get(tl.index);
  if (name && name !== (state.capture && state.capture.name)) selectCapture(name, false);
  renderTimeline();
  if (fromUser && tl.playing) stopPlay();
}
function startPlay() {
  const tl = state.timeline;
  if (tl.playing) return;
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
    if (r.width > 0) setSlot(((ev.clientX - r.left) / r.width) * (state.timeline.slots - 1), true);
  };
  let dragging = false;
  track.addEventListener("pointerdown", (e) => {
    dragging = true;
    try { track.setPointerCapture(e.pointerId); } catch (err) { /* synthetic events */ }
    toSlot(e);
  });
  track.addEventListener("pointermove", (e) => { if (dragging) toSlot(e); });
  const end = (e) => {
    dragging = false;
    try { track.releasePointerCapture(e.pointerId); } catch (err) { /* already released */ }
  };
  track.addEventListener("pointerup", end);
  track.addEventListener("pointercancel", end);
  track.addEventListener("wheel", (e) => {
    e.preventDefault();
    setSlot(state.timeline.index + Math.sign(e.deltaY || e.deltaX), true);
  }, { passive: false });
})();

/* ═════════════════════════ theme ═════════════════════════
   Both basemap styles are fetched once and switched as objects, so there is no
   race between two quick switches, and our sources and layers are carried
   across so the roads never leave the screen. */

const styleCache = {};
function styleJSON(theme) {
  if (!styleCache[theme]) {
    styleCache[theme] = fetch(STYLE_URL[theme])
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .catch((err) => { delete styleCache[theme]; throw err; });
  }
  return styleCache[theme];
}
function captureBase(style) {
  const layers = (style && style.layers) || [];
  state.baseLayers = layers.filter((l) => !isOurs(l.id)).map((l) => ({ id: l.id, type: l.type }));
  state.baseHidden = new Set(layers.filter((l) => !isOurs(l.id) && l.layout && l.layout.visibility === "none")
    .map((l) => l.id));
  const bg = layers.find((l) => l.type === "background");
  state.origBg = (bg && bg.paint && bg.paint["background-color"]) || THEME_BG[state.theme];
}
function baseLayerFor(l) {
  if (l.type === "background") {
    return state.showMap ? l : { ...l, paint: { ...(l.paint || {}), "background-color": THEME_BG[state.theme] } };
  }
  const visible = state.showMap && !state.baseHidden.has(l.id) && !(l.type === "symbol" && !state.labels);
  return { ...l, layout: { ...(l.layout || {}), visibility: visible ? "visible" : "none" } };
}
/* transformStyle: the new basemap, already set to the current map and label
   choices so nothing flashes, with our sources and layers appended unchanged. */
function carryOver(prev, next) {
  if (!next) return next;
  captureBase(next);
  const out = { ...next, sources: { ...(next.sources || {}) },
                layers: (next.layers || []).filter((l) => !isOurs(l.id)).map(baseLayerFor) };
  if (prev) {
    for (const [id, src] of Object.entries(prev.sources || {})) if (isOurs(id)) out.sources[id] = src;
    const ours = (prev.layers || []).filter((l) => isOurs(l.id)).map((l) => (l.id === "dim"
      ? { ...l, paint: { ...(l.paint || {}), "fill-color": THEME_BG[state.theme] } } : l));
    out.layers = out.layers.concat(ours);
  }
  return out;
}
function whenStyleReady(maxMs = 10000) {
  if (styleReady()) return Promise.resolve(true);
  return new Promise((resolve) => {
    const t0 = performance.now();
    const iv = setInterval(() => {
      if (styleReady() || performance.now() - t0 > maxMs) { clearInterval(iv); resolve(styleReady()); }
    }, 40);
  });
}
let themeSeq = 0;
async function setTheme(theme) {
  if (theme !== "light" && theme !== "dark") return;
  state.theme = theme;
  document.body.classList.toggle("dark", theme === "dark");
  document.querySelectorAll("#theme button").forEach((b) => b.classList.toggle("on", b.dataset.theme === theme));
  if (state.appliedTheme === theme && !state.themePending) { reconcile(); return; }
  const seq = ++themeSeq;
  let style = STYLE_URL[theme];
  try { style = await styleJSON(theme); } catch (err) { warn("style fetch", err); }
  if (seq !== themeSeq) return;                 // a newer switch has taken over
  await whenStyleReady();
  if (seq !== themeSeq) return;
  finishAllReveals();                           // never carry a half-played animation across
  state.themePending = true;
  attempt("setStyle", () => map.setStyle(style, { diff: true, transformStyle: carryOver }));
  state.appliedTheme = theme;
  note("theme", { theme });
  scheduleStyleSettled();
  setTimeout(() => { if (state.themePending && seq === themeSeq) onStyleSettled(); }, 1500);
}
let settleFrame = 0;
function scheduleStyleSettled() {
  if (settleFrame) return;
  settleFrame = requestAnimationFrame(() => { settleFrame = 0; onStyleSettled(); });
}
function onStyleSettled() {
  if (!styleReady()) return;
  state.themePending = false;
  ensureLayers();
  reconcile();
}
map.on("style.load", () => {
  if (!state.baseLayers.length) captureBase(attempt("getStyle", () => map.getStyle()));
  onStyleSettled();
});
map.on("styledata", () => { if (state.themePending) scheduleStyleSettled(); });
map.on("idle", () => { ensureLayers(); });

/* ═════════════════════════ watchdog ═════════════════════════ */

function heal() {
  for (const part of PARTS) {
    if (!state.model.anim[part] && (state.model.intro[part] < 1 || state.model.reveal[part] < 1)) finishReveal(part);
  }
  // every setter below returns early when the map already matches, so this is cheap
  applyModel();
  applyTraffic();
  applyGraph();
}
setInterval(() => {
  if (document.hidden || !styleReady()) return;
  if (!ensureLayers()) heal();
}, T.watchdog);
setInterval(async () => {
  if (document.hidden || state.reloading || !state.booted) return;
  const wasReady = state.model.ready;
  const info = await fetchModelInfo();
  const changed = applyModelInfo(info);
  if (!wasReady && state.model.ready) ensureLayers();
  else if (changed && !info.pending && !info.building) refreshModelData();
  const gwas = state.graph.version;
  applyGraphInfo(await fetchGraphInfo());
  if (state.graph.ready && gwas && state.graph.version !== gwas) {
    for (const part of GRAPH_PARTS) {
      if (hasSource(graphSourceId(part))) {
        attempt(`graph setData ${part}`, () => map.getSource(graphSourceId(part)).setData(graphUrl(part)));
      }
    }
  }
}, T.infoPoll);

/* ═════════════════════════ controls ═════════════════════════ */

document.querySelectorAll(".tabs button").forEach((b) => b.onclick = () => {
  document.querySelectorAll(".tabs button").forEach((o) => o.classList.toggle("on", o === b));
  document.querySelectorAll(".tabpanel").forEach((p) => p.classList.toggle("on", p.dataset.panel === b.dataset.tab));
});
document.querySelectorAll("#theme button").forEach((b) => b.onclick = () => setTheme(b.dataset.theme));
$("dockBtn").onclick = () => document.body.classList.toggle("dock-hidden");
$("helpBtn").onclick = () => { $("help").hidden = false; };
$("helpClose").onclick = () => { $("help").hidden = true; };
$("help").onclick = (e) => { if (e.target.id === "help") $("help").hidden = true; };

$("zoomIn").onclick = () => map.zoomIn({ duration: T.camera });
$("zoomOut").onclick = () => map.zoomOut({ duration: T.camera });
$("fitBtn").onclick = fitCapture;
$("northBtn").onclick = () => map.easeTo({ bearing: 0, pitch: 0, duration: T.camera });
$("inspectBtn").onclick = () => setInspect(!state.inspect);
$("inspectClose").onclick = () => { $("inspect").hidden = true; };

$("capture").onchange = (e) => selectCapture(e.target.value, true);
$("showTraffic").onchange = (e) => { state.traffic.show = e.target.checked; applyTraffic(); };
$("pure").onchange = (e) => { state.traffic.pure = e.target.checked; syncTrafficTiles(); };
$("opacity").oninput = (e) => {
  state.traffic.opacity = +e.target.value;
  $("ov").textContent = `${Math.round(state.traffic.opacity * 100)}%`;
  applyTraffic();
};
$("showModel").onchange = (e) => { state.model.show = e.target.checked; applyModel(); };
$("modelOpacity").oninput = (e) => {
  state.model.opacity = +e.target.value;
  $("mov").textContent = `${Math.round(state.model.opacity * 100)}%`;
  requestModelOpacity();
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
  setPaint("dim", "fill-opacity", state.showMap ? state.dim : 0);
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
/* graph controls */
function graphToggle(id, key) {
  $(id).onchange = (e) => {
    state.graph[key] = e.target.checked;
    if (key === "show" && state.graph.show) ensureLayers();
    applyGraph();
    orderLayers();
  };
}
graphToggle("showGraph", "show");
graphToggle("showNodes", "nodes");
graphToggle("showLinks", "links");
graphToggle("hideCuts", "hideCuts");
graphToggle("showArrows", "arrows");
graphToggle("breathe", "breathe");
$("breatheSpeed").oninput = (e) => {
  state.graph.breatheSpeed = +e.target.value;
  $("breathev").textContent = `${state.graph.breatheSpeed.toFixed(1)}x`;
};
$("breatheDepth").oninput = (e) => {
  state.graph.breatheDepth = +e.target.value;
  $("breathedv").textContent = `${Math.round(state.graph.breatheDepth * 100)}%`;
  applyBreath();
  syncBreathing();
};
$("nodeSize").oninput = (e) => {
  state.graph.nodeSize = +e.target.value;
  $("nodesizev").textContent = `${state.graph.nodeSize.toFixed(1)}x`;
  setPaint(graphLayerId("nodes"), "circle-radius", nodeRadiusExpr());
};
$("linkWidth").oninput = (e) => {
  state.graph.linkWidth = +e.target.value;
  $("linkwidthv").textContent = `${state.graph.linkWidth.toFixed(1)}x`;
  setPaint(graphLayerId("links"), "line-width", graphWidthExpr());
};
$("graphOpacity").oninput = (e) => {
  state.graph.opacity = +e.target.value;
  $("graphopacityv").textContent = `${Math.round(state.graph.opacity * 100)}%`;
  setPaint(graphLayerId("nodes"), "circle-opacity", graphOpacityExpr("nodes"));
  setPaint(graphLayerId("links"), "line-opacity", graphOpacityExpr("links"));
  setPaint("graph-arrows", "text-opacity", graphOpacityExpr("links"));
};
for (const [group, key] of [["linkColour", "linkColour"], ["nodeColour", "nodeColour"],
                            ["graphPalette", "palette"]]) {
  document.querySelectorAll(`#${group} button`).forEach((b) => b.onclick = () => {
    state.graph[key] = b.dataset.mode || b.dataset.palette;
    document.querySelectorAll(`#${group} button`).forEach((o) => o.classList.toggle("on", o === b));
    applyGraph();
  });
}

$("resetWeights").onclick = () => easeWeightsTo({ ...DEFAULTS });
$("reloadModel").onclick = reloadModel;

function abFlip() {
  if (!state.model.ready) return;
  const toModel = !state.model.show;
  state.model.show = toModel;
  state.traffic.show = !toModel;
  $("showModel").checked = toModel;
  $("showTraffic").checked = !toModel;
  applyModel();
  applyTraffic();
}
$("abFlip").onclick = abFlip;
$("playBtn").onclick = () => (state.timeline.playing ? stopPlay() : startPlay());

const toggle = (id) => { const el = $(id); if (el.disabled) return; el.checked = !el.checked; el.onchange({ target: el }); };
document.addEventListener("keydown", (e) => {
  if (/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const k = e.key.toLowerCase();
  if (e.repeat && k !== "arrowleft" && k !== "arrowright") return;
  if (k === "t") toggle("showTraffic");
  else if (k === "m") toggle("showModel");
  else if (k === "p") toggle("pure");
  else if (k === "l") toggle("labels");
  else if (k === "b") toggle("showMap");
  else if (k === "c") abFlip();
  else if (k === "f") fitCapture();
  else if (k === "i") setInspect(!state.inspect);
  else if (k === "n") map.easeTo({ bearing: 0, pitch: 0, duration: T.camera });
  else if (k === "r") reloadModel();
  else if (k === "g") { const el = $("showGraph"); if (!el.disabled) { el.checked = !el.checked; el.onchange({ target: el }); } }
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
function updateStatus() {
  const c = map.getCenter();
  // MapLibre defines zoom against 512 px tiles, so ground metres per CSS pixel
  // is half the familiar 256 px figure.
  const mpp = 78271.516964 * Math.cos((c.lat * Math.PI) / 180) / Math.pow(2, map.getZoom());
  $("coords").textContent = `${c.lat.toFixed(5)}, ${c.lng.toFixed(5)}  ·  z${map.getZoom().toFixed(1)}  ·  ` +
    `${distance(mpp * map.getCanvas().clientWidth)} across`;
}
map.on("move", updateStatus);

/* ═════════════════════════ boot ═════════════════════════ */

map.on("load", async () => {
  try {
    const cfg = await (await fetch("/api/config")).json();
    state.minZoom = cfg.min_zoom ?? state.minZoom;
  } catch { /* defaults are fine */ }
  await loadCaptures();
  buildTimeline();
  await loadLayerList();
  applyModelInfo(await fetchModelInfo(), { boot: true });
  applyGraphInfo(await fetchGraphInfo());
  $("showModel").checked = state.model.show;
  $("showTraffic").checked = state.traffic.show;
  ensureLayers();
  reconcile();
  renderLegend();
  renderCalc();
  updateStatus();
  state.booted = true;
  setTimeout(releaseMinor, 2000);                  // in case the arterial source never reports ready
  styleJSON("light").catch(() => {});             // warm both basemaps so a theme switch is instant
  styleJSON("dark").catch(() => {});
});

/* Read-only handle for diagnostics and the reliability tests. */
window.__track = {
  state, T, MINOR_FADE, warnings, events, styleReady, ensureLayers, reconcile,
  health() {
    const ready = styleReady();
    const layer = (id) => {
      if (!ready) return { present: false };
      const l = attempt(`getLayer ${id}`, () => map.getLayer(id));
      if (!l) return { present: false };
      return { present: true, minzoom: l.minzoom,
               visibility: attempt(`getLayout ${id}`, () => map.getLayoutProperty(id, "visibility")) || "visible" };
    };
    return {
      styleReady: ready, theme: state.theme, appliedTheme: state.appliedTheme, zoom: map.getZoom(),
      traffic: layer("traffic"), major: layer(layerId("major")), minor: layer(layerId("minor")),
      graphNodes: layer(graphLayerId("nodes")), graphLinks: layer(graphLayerId("links")),
      // Not `!!breathFrame` alone: breathTick zeroes the handle at its top and
      // only re-arms at its bottom, so for the whole of applyBreath() the
      // handle is 0 while the loop is perfectly alive. Measured: six such
      // windows in three seconds, each one sample wide, with the dash pattern
      // still advancing through them. Report what is true of the pulse rather
      // than what a frame handle happens to hold at the instant of sampling.
      graph: { ...state.graph, breathing: !!breathFrame || breathingWanted() },
      sources: { traffic: hasSource("traffic"), major: hasSource(sourceId("major")), minor: hasSource(sourceId("minor")) },
      model: {
        ready: state.model.ready, show: state.model.show, version: state.model.version,
        minorReady: state.model.minorReady, opacity: state.model.opacity, which: state.model.which,
        intro: { ...state.model.intro }, reveal: { ...state.model.reveal },
        revealed: { ...state.model.revealed }, anim: { ...state.model.anim }, retry: { ...state.model.retry },
      },
      warnings: warnings.slice(),
    };
  },
};
