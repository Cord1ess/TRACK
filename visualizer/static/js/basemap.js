/* The CARTO basemap underneath everything, the dimming sheet over it, and the
   captured Google tiles.

   The dim layer is a world-covering polygon in the theme's background colour.
   Fading the basemap by turning its layers off would lose the labels and the
   coastline; a sheet over it keeps the shape of the city while pushing it
   back, which is what the Fade map slider wants. */

import {
  $, T, THEME_BG, WORLD, attempt, clamp, finite, map, state,
} from "./core.js";
import { SITE, STATIC } from "./api.js";
import {
  ensureLayers, hasLayer, hasSource, registerLayers, setLayout, setPaint,
  setRowEnabled, styleReady,
} from "./mapkit.js";

export const trafficUrl = (c) => STATIC
  ? `${SITE}tiles${state.traffic.pure ? "-clean" : ""}/${encodeURIComponent(c.name)}/{z}/{x}/{y}.png`
  : `${location.origin}/tiles/${encodeURIComponent(c.name)}/{z}/{x}/{y}.png${state.traffic.pure ? "?clean=1" : ""}`;

function addDim() {
  let added = false;
  if (!hasSource("dim")) map.addSource("dim", { type: "geojson", data: WORLD });
  if (!hasLayer("dim")) {
    map.addLayer({ id: "dim", type: "fill", source: "dim",
      paint: { "fill-color": THEME_BG[state.theme], "fill-opacity": state.showMap ? state.dim : 0 } });
    added = true;
  }
  return added;
}

function addTraffic() {
  const c = state.capture;
  if (!c) return false;
  let added = false;
  if (!hasSource("traffic")) {
    // bounds keep requests inside the capture: the dev server answers a
    // transparent tile outside it, a static site has nothing to answer with
    const b = c.bbox ? { bounds: [c.bbox.west, c.bbox.south, c.bbox.east, c.bbox.north] } : {};
    map.addSource("traffic", { type: "raster", tileSize: 256, minzoom: state.minZoom,
      maxzoom: c.zoom || 17, tiles: [trafficUrl(c)], ...b });
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
export function syncTrafficTiles() {
  if (!styleReady() || !state.capture) return;
  const src = attempt("getSource traffic", () => map.getSource("traffic"));
  if (!src) { ensureLayers(); return; }
  const url = trafficUrl(state.capture);
  const b = state.capture.bbox;
  const sameBox = !b || !src.bounds
    || (Math.abs(src.bounds[0] - b.west) < 1e-9 && Math.abs(src.bounds[1] - b.south) < 1e-9
     && Math.abs(src.bounds[2] - b.east) < 1e-9 && Math.abs(src.bounds[3] - b.north) < 1e-9);
  if (src.maxzoom === (state.capture.zoom || 17) && sameBox && typeof src.setTiles === "function") {
    const current = Array.isArray(src.tiles) ? src.tiles[0] : null;
    if (current !== url) attempt("setTiles", () => src.setTiles([url]));
    return;
  }
  // a capture at a different native zoom or a different area needs a new source
  attempt("rebuild traffic", () => {
    if (map.getLayer("traffic")) map.removeLayer("traffic");
    if (map.getSource("traffic")) map.removeSource("traffic");
  });
  ensureLayers();
}

export function applyBase() {
  if (!styleReady()) return;
  for (const l of state.baseLayers) {
    if (l.type === "background") {
      setPaint(l.id, "background-color",
        state.showMap && state.origBg ? state.origBg : THEME_BG[state.theme]);
      continue;
    }
    const visible = state.showMap && !state.baseHidden.has(l.id)
      && !(l.type === "symbol" && !state.labels);
    setLayout(l.id, "visibility", visible ? "visible" : "none");
  }
  setPaint("dim", "fill-color", THEME_BG[state.theme]);
  setPaint("dim", "fill-opacity", state.showMap ? state.dim : 0);
  setRowEnabled("labels", state.showMap);
  setRowEnabled("dim", state.showMap);
}

export function applyTraffic() {
  setLayout("traffic", "visibility", state.traffic.show ? "visible" : "none");
  setPaint("traffic", "raster-opacity", clamp(finite(state.traffic.opacity, 1), 0, 1));
}

export function fitCapture() {
  const c = state.capture;
  if (!c || !c.bbox) return;
  attempt("fitBounds", () => map.fitBounds(
    [[c.bbox.west, c.bbox.south], [c.bbox.east, c.bbox.north]],
    { padding: 40, duration: T.camera }));
}

registerLayers({
  build: () => {
    let added = addDim();
    if (addTraffic()) added = true;
    return added;
  },
  apply: () => { applyBase(); applyTraffic(); },
});
