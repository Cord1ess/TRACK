/* Light and dark.

   Both basemap styles are fetched once and switched as objects, so there is no
   race between two quick switches. Our sources and layers are carried across
   into the new style rather than being wiped and rebuilt: wiping them left the
   map with no roads for about 2.5 seconds on every switch, and re-downloaded
   everything. */

import {
  STYLE_URL, THEME_BG, attempt, isOurs, map, note, state, warn,
} from "./core.js";
import { captureBase, ensureLayers, reconcile, styleReady } from "./mapkit.js";
import { finishAllReveals } from "./model.js";

const styleCache = {};

export function styleJSON(theme) {
  if (!styleCache[theme]) {
    styleCache[theme] = fetch(STYLE_URL[theme])
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .catch((err) => { delete styleCache[theme]; throw err; });
  }
  return styleCache[theme];
}

function baseLayerFor(l) {
  if (l.type === "background") {
    return state.showMap
      ? l
      : { ...l, paint: { ...(l.paint || {}), "background-color": THEME_BG[state.theme] } };
  }
  const visible = state.showMap && !state.baseHidden.has(l.id)
    && !(l.type === "symbol" && !state.labels);
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
export async function setTheme(theme) {
  if (theme !== "light" && theme !== "dark") return;
  state.theme = theme;
  document.body.classList.toggle("dark", theme === "dark");
  document.querySelectorAll("#theme button")
    .forEach((b) => b.classList.toggle("on", b.dataset.theme === theme));
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
