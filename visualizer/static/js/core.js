/* Shared ground for every other module: the constants, the one state object,
   the map itself, and the small helpers that keep a failure in one place from
   taking the page down.

   Everything here is imported by nearly everything else, so this module
   imports nothing of ours. That is what keeps the module graph a tree rather
   than a knot. */

export const STYLE_URL = {
  light: "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
  dark: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
};
export const THEME_BG = { light: "#ffffff", dark: "#000000" };

export const LADDER = [25, 55, 85, 105];                            // the default weight scale
export const RAMP = ["#16e098", "#ffcf43", "#d1352b", "#a92727"];   // Google's measured fills
export const LEVELS = ["Green", "Yellow", "Red", "Dark red"];
export const CLASS_NAMES = ["motorway", "trunk", "primary", "secondary", "tertiary",
                            "unclassified", "residential", "living street", "service"];
export const DEFAULTS = { g: 25, o: 55, r: 85, d: 105, jam: 4, beta: 4 };

/* Motion budget. Short on purpose: the map should feel immediate, and motion
   only exists to show where things came from. */
export const T = {
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

export const MINOR_FADE = [9.6, 10.6];      // zoom range the side streets fade in over
export const NODE_FADE = [11.6, 13.0];      // junctions fade in over this zoom range
export const LINK_FADE = [10.4, 11.6];      // graph edges fade in over this zoom range
export const ARROW_MIN_ZOOM = 15;           // symbol placement is the slowest thing on the map
export const PARTS = ["major", "minor"];
export const GRAPH_PARTS = ["nodes", "links"];
export const PULSE_STEPS = 8;

/* Four palettes for the graph. Each is ordered low to high, and every colour
   scale indexes into the one the menu selected. */
export const PALETTES = {
  vivid: ["#0071e3", "#00b894", "#f5a524", "#e5484d", "#8b5cf6"],
  cool:  ["#164e63", "#0e7490", "#22d3ee", "#a5f3fc", "#e0f2fe"],
  warm:  ["#7c2d12", "#c2410c", "#f59e0b", "#fcd34d", "#fef3c7"],
  mono:  ["#2b2b2f", "#5a5a63", "#8c8c96", "#bdbdc6", "#e9e9ef"],
};

export const layerId = (part) => `model-${part}`;
export const sourceId = (part) => `model-${part}-src`;
export const graphLayerId = (part) => `graph-${part}`;
export const graphSourceId = (part) => `graph-${part}-src`;
export const geoIds = (id) => ({ src: `gj:${id}`, line: `gj:${id}:line`, pt: `gj:${id}:pt`, fill: `gj:${id}:fill` });
export const isOurs = (id) => id === "dim" || id === "traffic" || id.startsWith("model-")
  || id.startsWith("graph-") || id.startsWith("gj:");

export const $ = (id) => document.getElementById(id);
export const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
export const finite = (v, fallback) => (Number.isFinite(v) ? v : fallback);
export const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export const WORLD = { type: "Feature", geometry: { type: "Polygon",
  coordinates: [[[-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85]]] } };

/* One object holds everything the page knows. Modules read and write it
   directly rather than passing it around, which keeps the call signatures
   short and means the diagnostics handle can show the whole picture at once. */
export const state = {
  minZoom: 8, captures: [], capture: null,
  layers: [], activeLayers: new Set(), layerData: {}, layerOpacity: 0.9,
  // the latest workflow runs, the steps of the one in progress, and what
  // each step cost last time it finished. Static site only.
  runs: { collect: null, deploy: null, at: 0, steps: null, stepCosts: {} },
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
  timeline: {
    start: null, slots: 672, index: 0, playing: false, timer: null,
    series: "scheduled",   // which of the two timelines is showing
    available: new Map(),  // slot index -> capture name, for the series showing
    order: [],             // those slot indexes in time order, for stepping
    outside: 0,            // captures in this series that fall outside the drawn week
    speed: 1,
  },
};

export const map = new maplibregl.Map({
  container: "map", style: STYLE_URL.light, center: [90.4125, 23.8075], zoom: 11,
  attributionControl: { compact: true }, fadeDuration: 0,
});
window.__map = map;

/* ═════════════════════════ safety helpers ═════════════════════════ */

export const warnings = [];
export const events = [];

export function note(type, detail) {
  events.push({ t: Math.round(performance.now()), type, ...detail });
  if (events.length > 200) events.shift();
}
export function warn(where, err) {
  const msg = `${where}: ${err && err.message ? err.message : err}`;
  warnings.push(msg);
  if (warnings.length > 50) warnings.shift();
  console.warn(`[track] ${msg}`);
}
export function attempt(where, fn) {
  try { return fn(); } catch (err) { warn(where, err); return undefined; }
}
