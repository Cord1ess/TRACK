/* The algorithm layers: one GeoJSON overlay per algorithm, listed in the
   Layers tab with its description, legend and headline numbers.

   Each layer becomes three MapLibre layers (fill, line, circle) filtered by
   geometry type, so one file can carry zones, routes and points together
   without the caller having to say which it is. */

import {
  $, RAMP, attempt, escapeHtml, geoIds, map, state, warn,
} from "./core.js";
import { apiJson } from "./api.js";
import { ensureLayers, hasLayer, hasSource, registerLayers, setPaint } from "./mapkit.js";

function addGeoJsonLayer(l) {
  const ids = geoIds(l.id);
  if (!hasSource(ids.src)) map.addSource(ids.src, { type: "geojson", data: state.layerData[l.id] });
  // a feature may name its own colour, or carry a traffic class to look up
  const color = ["case", ["has", "color"], ["get", "color"],
    ["has", "cls"], ["match", ["get", "cls"], 1, RAMP[0], 2, RAMP[1], 3, RAMP[2], 4, RAMP[3], "#9e9e9e"],
    "#0071e3"];
  if (!hasLayer(ids.fill)) {
    map.addLayer({ id: ids.fill, type: "fill", source: ids.src,
      filter: ["==", ["geometry-type"], "Polygon"],
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
    map.addLayer({ id: ids.pt, type: "circle", source: ids.src,
      filter: ["==", ["geometry-type"], "Point"],
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

export function applyLayerOpacity() {
  for (const id of state.activeLayers) {
    const ids = geoIds(id);
    setPaint(ids.line, "line-opacity", state.layerOpacity);
    setPaint(ids.pt, "circle-opacity", state.layerOpacity);
    setPaint(ids.fill, "fill-opacity", state.layerOpacity * 0.3);
  }
}

/* A big layer is tiled in a worker after it is added, so the row stays marked
   as loading until its first tiles are actually on screen. */
function markUntilDrawn(row, id) {
  const ids = geoIds(id);
  const t0 = performance.now();
  const drawn = () => [ids.line, ids.pt, ids.fill].some((k) =>
    hasLayer(k) && attempt(`query ${k}`, () => map.queryRenderedFeatures({ layers: [k] }).length > 0));
  const tick = () => {
    if (!row.classList.contains("loading")) return;
    const done = !state.activeLayers.has(id) || drawn()
      || (hasSource(ids.src) && map.isSourceLoaded(ids.src)) || performance.now() - t0 > 20000;
    if (done) row.classList.remove("loading"); else setTimeout(tick, 150);
  };
  tick();
}

async function toggleLayer(l, row, cb) {
  if (!cb.checked) {
    state.activeLayers.delete(l.id);
    row.classList.remove("loading");
    removeGeoJsonLayer(l.id);
    return;
  }
  state.activeLayers.add(l.id);
  row.classList.add("loading");
  if (!state.layerData[l.id]) {
    try {
      state.layerData[l.id] = await (await fetch(l.url)).json();
    } catch (err) {
      warn(`layer ${l.id}`, err);
      state.activeLayers.delete(l.id);
      cb.checked = false;
      row.classList.remove("loading");
      return;
    }
  }
  ensureLayers();
  markUntilDrawn(row, l.id);
}

export async function loadLayerList() {
  try { state.layers = await apiJson("/api/layers"); } catch { state.layers = []; }
  const box = $("layers");
  if (!Array.isArray(state.layers) || !state.layers.length) {
    state.layers = [];
    box.innerHTML = '<p class="hint">none yet — pipeline outputs appear here</p>';
    return;
  }
  box.innerHTML = "";
  for (const l of state.layers) {
    const row = document.createElement("div");
    row.className = "algo";
    const legend = (l.legend || []).map((s) =>
      `<i style="background:${escapeHtml(s.color)}"></i>${escapeHtml(s.label)}`).join("");
    row.innerHTML = `<div class="row"><span>${escapeHtml(l.name || l.id)}</span>
        <label class="switch sm"><input type="checkbox" id="layer-${escapeHtml(l.id)}"><span></span></label></div>
      ${l.description ? `<p class="hint">${escapeHtml(l.description)}</p>` : ""}
      ${legend ? `<div class="lg">${legend}</div>` : ""}
      ${l.summary ? `<p class="hint stat">${escapeHtml(l.summary)}</p>` : ""}`;
    const cb = row.querySelector("input");
    cb.onchange = () => toggleLayer(l, row, cb);
    box.appendChild(row);
  }
}

registerLayers({
  build: () => {
    let added = false;
    for (const l of state.layers) {
      if (state.activeLayers.has(l.id) && state.layerData[l.id] && !hasLayer(geoIds(l.id).line)) {
        addGeoJsonLayer(l);
        added = true;
      }
    }
    return added;
  },
  apply: applyLayerOpacity,
});
