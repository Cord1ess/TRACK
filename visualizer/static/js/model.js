/* TRACK's own data: one line per road, carrying numbers rather than colours.

   Shipping the numbers is what lets the Weights tab re-colour the whole city
   on the GPU with no server round trip. Every feature carries w (weight on the
   default scale), s (observed or predicted), c (coverage), h (road class rank),
   f (free-flow km/h) and o (reveal order).

   The data arrives in two parts. `major` is the arterial network, small and
   quick, and draws first. `minor` is the 43,000 side streets, held back until
   the arteries are on screen so the map is useful in about a second. */

import {
  $, DEFAULTS, LADDER, MINOR_FADE, PARTS, RAMP, T, attempt, clamp, escapeHtml,
  finite, layerId, map, note, sourceId, state, warn,
} from "./core.js";
import { loadFrameIndex, renderModelInfo, repaint, setVisibilityHook } from "./frames.js";
import { SITE, STATIC, apiJson } from "./api.js";
import {
  ensureLayers, hasLayer, hasSource, registerLayers, setLayout, setPaint,
} from "./mapkit.js";

export const modelUrl = (part) => STATIC
  ? `${SITE}data/model-${part}.${encodeURIComponent(state.model.version || "0")}.json`
  : `${location.origin}/model.geojson?part=${part}&v=${encodeURIComponent(state.model.version || "0")}`;

/* ═════════════════════════ style expressions ═════════════════════════
   Every read has a fallback, so a feature missing a field still draws. */

const num = (key, fallback) => ["to-number", ["coalesce", ["get", key], fallback], fallback];
/* A value that changes from capture to capture: the frame on show sets it as
   feature state; the shipped geometry's own value is the fallback. */
const live = (key, fallback) =>
  ["to-number", ["coalesce", ["feature-state", key], ["get", key], fallback], fallback];

/** Whether the model roads should be drawn at all. A capture that has not
    been processed has no model, so none is drawn rather than another one. */
export const modelVisible = () => state.model.show && !(state.frames.v && state.frames.hidden);

export function remapWeight() {
  const w = state.weights;
  return ["interpolate", ["linear"], live("w", LADDER[0]),
    LADDER[0], finite(w.g, DEFAULTS.g), LADDER[1], finite(w.o, DEFAULTS.o),
    LADDER[2], finite(w.r, DEFAULTS.r), LADDER[3], finite(w.d, DEFAULTS.d)];
}
export function alphaOf() {
  // BPR alpha, pinned so the dark-red weight runs `jam` times slower than free flow
  const beta = finite(state.beta, DEFAULTS.beta);
  const vc = Math.max(finite(state.weights.d, DEFAULTS.d), 1) / 100;
  return clamp((finite(state.jam, DEFAULTS.jam) - 1) / Math.pow(vc, beta), 0, 1e4);
}
export function delayExpr() {
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
/** The same maths in plain JS, for the result table and the inspector. */
export function delayAt(weight) {
  const w = state.weights;
  const W = interp(finite(weight, LADDER[0]), LADDER, [w.g, w.o, w.r, w.d].map((v, i) =>
    finite(v, [DEFAULTS.g, DEFAULTS.o, DEFAULTS.r, DEFAULTS.d][i])));
  return { weight: W, delay: 1 + alphaOf() * Math.pow(Math.max(W, 0) / 100, finite(state.beta, 4)) };
}
export function colourExpr() {
  if (state.colourBy === "delay") {
    return ["interpolate", ["linear"], delayExpr(),
      1, RAMP[0], 1.4, RAMP[1], 2.2, RAMP[2], 3.5, RAMP[3]];
  }
  if (state.colourBy === "speed") {
    return ["interpolate", ["linear"], ["/", num("f", 30), delayExpr()],
      5, RAMP[3], 12, RAMP[2], 25, RAMP[1], 45, RAMP[0]];
  }
  return ["interpolate", ["linear"], live("w", LADDER[0]),
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
    ? ["case", ["==", live("s", 1), 0], base, base * 0.55]
    : base;
  // Observed / Predicted: which roads count changes with every capture, and
  // filters cannot read feature state, so the other kind is faded out instead.
  if (state.model.which !== "all") {
    const keep = state.model.which === "observed" ? 0 : 1;
    perFeature = ["case", ["==", live("s", 1), keep], perFeature, 0];
  }

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
  return ["all", ["<=", num("h", 6), state.minCls]];
}
const minzoomFor = (part) => (part === "minor" && state.lodMinor ? MINOR_FADE[0] - 0.2 : 0);

/* ═════════════════════════ layers ═════════════════════════ */

function addModelPart(part) {
  let added = false;
  if (!hasSource(sourceId(part))) {
    // maxzoom 14 keeps client-side tiling cheap; lines stay crisp above it
    // because vectors are re-projected, not resampled.
    // promoteId: frames address lines by `n`, their fixed line number
    map.addSource(sourceId(part), { type: "geojson", data: modelUrl(part),
      maxzoom: 14, buffer: 32, tolerance: 0.375, promoteId: "n" });
    added = true;
    // a new source starts with no feature state: paint the current frame in full
    setTimeout(repaint, 0);
  }
  if (!hasLayer(layerId(part))) {
    const firstShow = !state.model.revealed[part];
    if (firstShow) primeReveal(part);
    map.addLayer({ id: layerId(part), type: "line", source: sourceId(part),
      minzoom: minzoomFor(part), filter: modelFilter(),
      layout: { "line-cap": "round", "line-join": "round",
                visibility: modelVisible() ? "visible" : "none" },
      paint: { "line-color": colourExpr(), "line-width": widthExpr(part === "minor"),
               "line-opacity": opacityExpr(part) } });
    added = true;
    if (firstShow) playReveal(part);
  }
  return added;
}

export function applyModelOpacity() {
  for (const part of PARTS) setPaint(layerId(part), "line-opacity", opacityExpr(part));
}
export function applyModel() {
  for (const part of PARTS) {
    const id = layerId(part);
    if (!hasLayer(id)) continue;
    setLayout(id, "visibility", modelVisible() ? "visible" : "none");
    attempt(`zoomRange ${id}`, () => map.setLayerZoomRange(id, minzoomFor(part), 24));
  }
  applyModelOpacity();
}
/** The live part: colour, width, filter and opacity. */
export function applyModelStyle() {
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
  onStyleChanged();
}

/* The legend and result table live in another module; it hands us a callback
   rather than us importing it, so the dependency keeps pointing one way. */
let onStyleChanged = () => {};
export function setStyleChangeHook(fn) { onStyleChanged = fn; }

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
export function finishReveal(part) {
  const wasRunning = !!state.model.anim[part];
  state.model.anim[part] = 0;
  state.model.revealed[part] = true;
  state.model.intro[part] = 1;
  state.model.reveal[part] = 1;
  applyModelOpacity();
  if (wasRunning) note("reveal-done", { part });
}
export function finishAllReveals() {
  for (const part of PARTS) {
    if (state.model.anim[part] || state.model.intro[part] < 1 || state.model.reveal[part] < 1) {
      finishReveal(part);
    }
  }
}
document.addEventListener("visibilitychange", () => { if (document.hidden) finishAllReveals(); });

/* The side streets are the bulk of the data. Hold them back until the arterial
   network has drawn, so the map is useful in about a second. */
export function releaseMinor() {
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
export function scheduleSourceRetry(part) {
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

/* ═════════════════════════ the data itself ═════════════════════════ */

export async function fetchModelInfo() {
  try {
    return await apiJson("/api/model");
  } catch (e) {
    return { ready: false,
             error: /^(server answered|data unreachable)/.test(e.message) ? e.message : "server unreachable" };
  }
}

/* Apply what the server says. Once data has been shown, a failed or empty
   answer only adds a note: it never switches the model off or drops its roads.
   Returns true when the server now has a different version of the data. */
export function applyModelInfo(m, { boot = false } = {}) {
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
    // with frames, the words under the switch describe the capture on show
    // (frames.js); the whole-file summary is only the fallback without them
    if (state.frames.v) renderModelInfo();
    else $("modelInfo").innerHTML = state.model.summary + (m.stale && m.error
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
  $("modelInfo").innerHTML =
    `no model data: ${escapeHtml(reason)}.<br>Run <b>python pipeline.py</b> in the algorithms folder.`;
  if (boot) {
    state.model.show = false;
    state.traffic.show = true;
  }
  return false;
}

/* Swap in new data on the existing sources. The old roads stay drawn until the
   new tiles are ready, so a refresh never blanks the map. */
export function refreshModelData() {
  for (const part of PARTS) {
    if (!hasSource(sourceId(part))) continue;
    attempt(`setData ${part}`, () => map.getSource(sourceId(part)).setData(modelUrl(part)));
  }
  note("data-refresh", { version: state.model.version });
  // the line list may have changed with the data: re-read it, then repaint
  loadFrameIndex().then(repaint);
}

setVisibilityHook(() => applyModel());

registerLayers({
  build: () => {
    if (!state.model.ready) return false;
    let added = addModelPart("major");
    if (state.model.minorReady && addModelPart("minor")) added = true;
    return added;
  },
  apply: () => { applyModel(); applyModelStyle(); },
});
