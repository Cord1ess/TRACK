/* The road network A* actually walks: junctions, and one line per directed
   edge, with a pulse running along each in its direction of travel.

   The pulse is a moving DASH, not a gradient. `line-gradient` would be the
   obvious way, but it needs `lineMetrics` on the source, which makes MapLibre
   measure distance along all 209,847 coordinates of the graph, and rewriting
   the gradient each frame kept that work running so the map never settled. A
   dash pattern costs nothing per coordinate: shifting the gap through a short
   cycle makes the dashes run along each line, and because a line's coordinates
   are stored in its direction of travel, they run that way. */

import {
  $, ARROW_MIN_ZOOM, GRAPH_PARTS, LINK_FADE, NODE_FADE, PALETTES, PULSE_STEPS,
  attempt, clamp, escapeHtml, finite, graphLayerId, graphSourceId, map, state,
} from "./core.js";
import { SITE, STATIC, apiJson } from "./api.js";
import {
  hasLayer, hasSource, registerLayers, setLayout, setPaint,
} from "./mapkit.js";

export const graphUrl = (part) => STATIC
  ? `${SITE}data/graph-${part}.${encodeURIComponent(state.graph.version || "0")}.json`
  : `${location.origin}/graph.geojson?part=${part}&v=${encodeURIComponent(state.graph.version || "0")}`;

export const pal = () => PALETTES[state.graph.palette] || PALETTES.vivid;

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
export const graphExprs = { linkColourExpr, nodeColourExpr, graphOpacityExpr, nodeRadiusExpr, graphWidthExpr };

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

export function applyGraph() {
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

export function applyBreath() {
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

/* PULSE_STEPS positions is all a dash cycle can show, so the animation steps
   between them about 20 times a second instead of once per frame. */
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
export function breathingWanted() {
  return state.graph.show && state.graph.links && state.graph.breathe
    && state.graph.breatheDepth > 0 && !document.hidden && hasLayer(graphLayerId("links"));
}
export function syncBreathing() {
  if (breathingWanted()) {
    if (!breathFrame) breathFrame = requestAnimationFrame(breathTick);
  } else if (breathFrame) {
    cancelAnimationFrame(breathFrame);
    breathFrame = 0;
    applyBreath();                      // settle on the plain colour
  }
}
export const breathAlive = () => !!breathFrame || breathingWanted();
document.addEventListener("visibilitychange", syncBreathing);

/* ═════════════════════════ the data itself ═════════════════════════ */

export async function fetchGraphInfo() {
  try {
    return await apiJson("/api/graph");
  } catch (e) {
    return { ready: false,
             error: /^(server answered|data unreachable)/.test(e.message) ? e.message : "server unreachable" };
  }
}

export function applyGraphInfo(g) {
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
  $("graphInfo").innerHTML =
    `no graph: ${escapeHtml(reason)}.<br>Run <b>python pipeline.py</b> in the algorithms folder.`;
}

registerLayers({
  build: () => {
    if (!state.graph.ready || !state.graph.show) return false;
    let added = false;
    for (const part of GRAPH_PARTS) if (addGraphPart(part)) added = true;
    return added;
  },
  apply: applyGraph,
});
