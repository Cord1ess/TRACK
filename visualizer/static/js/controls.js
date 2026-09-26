/* Every control on the page, and the keyboard.

   This module is the only one that reads the DOM for input. The feature
   modules expose what they do; this file decides which widget calls what, so
   a control moving in the markup never means hunting through map code. */

import {
  $, CLASS_NAMES, DEFAULTS, T, attempt, graphLayerId, map, state,
} from "./core.js";
import { ensureLayers, orderLayers, setPaint } from "./mapkit.js";
import { applyBase, applyTraffic, fitCapture, syncTrafficTiles } from "./basemap.js";
import {
  applyModel, applyModelOpacity, applyModelStyle, finishAllReveals,
  fetchModelInfo, applyModelInfo, refreshModelData,
} from "./model.js";
import { applyGraph, applyBreath, graphExprs, syncBreathing } from "./graph.js";
import { applyLayerOpacity } from "./layers.js";
import { selectCapture } from "./captures.js";
import { setSpeed, stepCapture, togglePlay, zoomAt, zoomReset } from "./timeline.js";
import { setInspect } from "./inspector.js";
import { setTheme } from "./theme.js";
import { renderReadouts } from "./legend.js";

/* Slider events arrive far faster than the map repaints 60k lines. Collapse
   them to one restyle per frame; the numbers beside the sliders update at once. */
let styleFrame = 0;
export function requestStyleApply() {
  renderReadouts();
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
export function cancelEase() { cancelAnimationFrame(easeFrame); }
function easeWeightsTo(target, dur = T.ease) {
  cancelAnimationFrame(easeFrame);
  const apply = (e, from) => {
    for (const key of ["g", "o", "r", "d"]) {
      state.weights[key] = from[key] + (target[key] - from[key]) * e;
    }
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
      apply(1, from);
    }
  };
  easeFrame = requestAnimationFrame(step);
}

export function syncWeightInputs() {
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

/* The fallback button: settle any animation, pick up new data if the server
   has it, re-apply every control, repaint. Never hides anything on the way. */
export async function reloadModel() {
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
    attempt("repaint", () => map.triggerRepaint());
  } catch (err) {
    outcome = "Reload failed · kept data";
  } finally {
    state.reloading = false;
    btn.disabled = false;
    btn.textContent = outcome;
    btn._restore = setTimeout(() => { btn.innerHTML = btn.dataset.label; }, 1400);
  }
}

export function abFlip() {
  if (!state.model.ready) return;
  const toModel = !state.model.show;
  state.model.show = toModel;
  state.traffic.show = !toModel;
  $("showModel").checked = toModel;
  $("showTraffic").checked = !toModel;
  applyModel();
  applyTraffic();
}

/* ═════════════════════════ wiring ═════════════════════════ */

export function bindControls() {
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
  const graphToggle = (id, key) => {
    $(id).onchange = (e) => {
      state.graph[key] = e.target.checked;
      if (key === "show" && state.graph.show) ensureLayers();
      applyGraph();
      orderLayers();
    };
  };
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
    setPaint(graphLayerId("nodes"), "circle-radius", graphExprs.nodeRadiusExpr());
  };
  $("linkWidth").oninput = (e) => {
    state.graph.linkWidth = +e.target.value;
    $("linkwidthv").textContent = `${state.graph.linkWidth.toFixed(1)}x`;
    setPaint(graphLayerId("links"), "line-width", graphExprs.graphWidthExpr());
  };
  $("graphOpacity").oninput = (e) => {
    state.graph.opacity = +e.target.value;
    $("graphopacityv").textContent = `${Math.round(state.graph.opacity * 100)}%`;
    setPaint(graphLayerId("nodes"), "circle-opacity", graphExprs.graphOpacityExpr("nodes"));
    setPaint(graphLayerId("links"), "line-opacity", graphExprs.graphOpacityExpr("links"));
    setPaint("graph-arrows", "text-opacity", graphExprs.graphOpacityExpr("links"));
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
  $("abFlip").onclick = abFlip;
  $("playBtn").onclick = togglePlay;
  document.querySelectorAll("#playSpeed button")
    .forEach((b) => b.onclick = () => setSpeed(b.dataset.speed));

  bindKeys();
}

function bindKeys() {
  const toggle = (id) => {
    const el = $(id);
    if (el.disabled) return;
    el.checked = !el.checked;
    el.onchange({ target: el });
  };
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
    else if (k === "g") toggle("showGraph");
    else if (k === "1") setTheme("light");
    else if (k === "2") setTheme("dark");
    else if (k === "\\") document.body.classList.toggle("dock-hidden");
    else if (k === "?") $("help").hidden = !$("help").hidden;
    else if (k === "escape") { $("help").hidden = true; $("inspect").hidden = true; }
    else if (k === " ") togglePlay();
    else if (k === "arrowleft" || k === "arrowright") {
      // plain arrows stay with the map for panning; the timeline takes them
      // only when the track has focus or shift is held
      if (document.activeElement !== $("track") && !e.shiftKey) return;
      stepCapture(k === "arrowleft" ? -1 : 1);
    }
    else return;
    e.preventDefault();
  });
}
