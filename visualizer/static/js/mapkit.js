/* The rules for touching the map safely, and the layer registry.

   Every map edit in the app goes through these helpers rather than calling
   MapLibre directly. Each one exists because doing it the obvious way made
   roads disappear:

   - styleReady, not map.isStyleLoaded(). isStyleLoaded is false whenever ANY
     tile is still loading, which is most of the time while panning, so layer
     adds silently did nothing until the next idle.
   - hasLayer / hasSource before every edit, so a call against a layer that
     went away with a style switch is a no-op instead of an exception.

   The registry at the bottom is how ensureLayers adds things without this
   module importing the four modules that own them. Each of those registers
   its own "add whatever is missing" function here at load time, which keeps
   the imports pointing one way. */

import { attempt, isOurs, map, state, warn } from "./core.js";

export function styleReady() {
  const style = map.style;
  if (!style) return false;
  // MapLibre 4 sets `_loaded` once the style spec is in, independent of tiles.
  return typeof style._loaded === "boolean" ? style._loaded : map.loaded();
}
export function hasLayer(id) {
  return styleReady() && !!attempt(`getLayer ${id}`, () => map.getLayer(id));
}
export function hasSource(id) {
  return styleReady() && !!attempt(`getSource ${id}`, () => map.getSource(id));
}
export function setPaint(id, prop, value) {
  if (hasLayer(id)) attempt(`paint ${id}.${prop}`, () => map.setPaintProperty(id, prop, value));
}
export function setLayout(id, prop, value) {
  if (hasLayer(id)) attempt(`layout ${id}.${prop}`, () => map.setLayoutProperty(id, prop, value));
}
export function setRowEnabled(inputId, on) {
  const row = document.getElementById(inputId) && document.getElementById(inputId).closest(".row");
  if (row) row.classList.toggle("off", !on);
}

/* ═════════════════════════ layer registry ═════════════════════════ */

const builders = [];      // () => true when something was added
const appliers = [];      // () => void, re-apply state to existing layers

/** A module that owns map layers registers here instead of being imported. */
export function registerLayers({ build, apply }) {
  if (build) builders.push(build);
  if (apply) appliers.push(apply);
}

let ensuring = false;
/* Add whatever should exist and does not. Idempotent, safe to call any time. */
export function ensureLayers() {
  if (ensuring || !styleReady()) return false;
  ensuring = true;
  let added = false;
  try {
    for (const build of builders) {
      if (attempt("ensureLayers", build)) added = true;
    }
  } catch (err) {
    warn("ensureLayers", err);
  } finally {
    ensuring = false;
  }
  if (added) reconcile();
  return added;
}

/** Push the whole of state back onto the map. Cheap: every setter returns
    early when the map already matches. */
export function reconcile() {
  if (!styleReady()) return;
  for (const apply of appliers) attempt("reconcile", apply);
  orderLayers();
}

/* ═════════════════════════ stacking order ═════════════════════════ */

export function layerOrder() {
  if (typeof map.getLayersOrder === "function") return attempt("getLayersOrder", () => map.getLayersOrder()) || [];
  if (map.style && Array.isArray(map.style._order)) return map.style._order.slice();
  const st = attempt("getStyle", () => map.getStyle());
  return st ? st.layers.map((l) => l.id) : [];
}

/* Final stacking: basemap, dim, capture, side streets, arteries, graph,
   algorithm layers on top. */
export function orderLayers() {
  if (!styleReady()) return;
  const order = layerOrder();
  const want = ["dim", "traffic", "model-minor", "model-major",
                "graph-links", "graph-nodes", "graph-arrows"]
    .filter((id) => order.includes(id))
    .concat(order.filter((id) => id.startsWith("gj:")));
  if (!want.length) return;
  if (order.slice(order.length - want.length).join("|") === want.join("|")) return;
  for (const id of want) attempt(`moveLayer ${id}`, () => map.moveLayer(id));
}

/* The basemap's own layers, remembered so the map, label and dim controls can
   put them back exactly as the style had them. */
export function captureBase(style) {
  const layers = (style && style.layers) || [];
  state.baseLayers = layers.filter((l) => !isOurs(l.id)).map((l) => ({ id: l.id, type: l.type }));
  state.baseHidden = new Set(layers.filter((l) => !isOurs(l.id) && l.layout && l.layout.visibility === "none")
    .map((l) => l.id));
  const bg = layers.find((l) => l.type === "background");
  state.origBg = (bg && bg.paint && bg.paint["background-color"]) || null;
}
