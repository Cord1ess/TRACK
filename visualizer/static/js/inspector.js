/* Click a road or a junction and read what the data says about it.

   A junction under the pointer wins over a road, because it is the smaller
   target and harder to hit deliberately. The query box is a few pixels wide
   rather than a point, so a thin line is still catchable. */

import {
  $, CLASS_NAMES, LADDER, RAMP, attempt, clamp, escapeHtml, finite,
  graphLayerId, layerId, map, PARTS, state,
} from "./core.js";
import { hasLayer, styleReady } from "./mapkit.js";
import { delayAt } from "./model.js";
import { pal } from "./graph.js";

const DEGREE_NAME = { 1: "dead end", 2: "pass-through", 3: "T junction", 4: "crossroads" };

export function setInspect(on) {
  state.inspect = on;
  $("inspectBtn").setAttribute("aria-pressed", String(on));
  map.getCanvas().style.cursor = on ? "crosshair" : "";
  if (!on) $("inspect").hidden = true;
}

function rampColour(w) {
  const i = LADDER.findIndex((v) => w <= v);
  return RAMP[i < 0 ? RAMP.length - 1 : i];
}

function showRoad(p) {
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
}

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

map.on("click", (e) => {
  if (!state.inspect || !styleReady()) return;
  const box = [[e.point.x - 6, e.point.y - 6], [e.point.x + 6, e.point.y + 6]];
  const nodeLayer = [graphLayerId("nodes")].filter(hasLayer);
  const nodeHit = nodeLayer.length
    ? (attempt("inspect node", () => map.queryRenderedFeatures(box, { layers: nodeLayer })) || [])[0]
    : null;
  if (nodeHit) { showJunction(nodeHit.properties || {}); return; }
  const layers = PARTS.map(layerId).filter(hasLayer);
  if (!layers.length) return;
  const hits = attempt("inspect query", () => map.queryRenderedFeatures(box, { layers })) || [];
  if (!hits.length) { $("inspect").hidden = true; return; }
  showRoad(hits[0].properties || {});
});
