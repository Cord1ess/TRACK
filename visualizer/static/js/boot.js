/* Start the page, keep it healthy, and expose what the tests read.

   This is the entry point: importing it pulls in every other module. The
   feature modules register their layers and hooks as they load, so by the time
   the map fires `load` everything is wired and boot only has to fetch.

   Reliability rules that live here:
   - a watchdog re-checks every second and repairs anything missing or out of
     step with the controls, so an unforeseen failure heals itself;
   - a slower poll looks for new data and picks it up without a reload. */

import {
  $, GRAPH_PARTS, MINOR_FADE, PARTS, T, attempt, events, graphLayerId,
  graphSourceId, layerId, map, sourceId, state, warnings,
} from "./core.js";
import { apiJson } from "./api.js";
import {
  ensureLayers, hasSource, reconcile, styleReady,
} from "./mapkit.js";
import { applyTraffic } from "./basemap.js";
import {
  applyModel, applyModelInfo, fetchModelInfo, finishReveal, refreshModelData,
  releaseMinor,
} from "./model.js";
import {
  applyGraph, applyGraphInfo, breathAlive, fetchGraphInfo, graphUrl,
} from "./graph.js";
import { loadLayerList } from "./layers.js";
import { loadCaptures, refreshCaptures } from "./captures.js";
import { RUN_POLL, fetchRuns, render as renderCollectBar, startTicking } from "./collection.js";
import { bindTrack, buildTimeline } from "./timeline.js";
import { renderReadouts } from "./legend.js";
import { bindControls } from "./controls.js";
import { styleJSON } from "./theme.js";
import "./inspector.js";

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
  $("coords").textContent =
    `${c.lat.toFixed(5)}, ${c.lng.toFixed(5)}  ·  z${map.getZoom().toFixed(1)}  ·  ` +
    `${distance(mpp * map.getCanvas().clientWidth)} across`;
}
map.on("move", updateStatus);

/* ═════════════════════════ watchdog ═════════════════════════ */

function heal() {
  for (const part of PARTS) {
    if (!state.model.anim[part] && (state.model.intro[part] < 1 || state.model.reveal[part] < 1)) {
      finishReveal(part);
    }
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
        attempt(`graph setData ${part}`,
          () => map.getSource(graphSourceId(part)).setData(graphUrl(part)));
      }
    }
  }
  await refreshCaptures();
}, T.infoPoll);

/* ═════════════════════════ boot ═════════════════════════ */

map.on("load", async () => {
  bindControls();
  bindTrack();
  try {
    const cfg = await apiJson("/api/config");
    state.minZoom = cfg.min_zoom ?? state.minZoom;
  } catch { /* defaults are fine */ }
  await loadCaptures();
  buildTimeline();
  fetchRuns();
  setInterval(fetchRuns, RUN_POLL);
  startTicking();          // the elapsed part counts up between polls
  await loadLayerList();
  applyModelInfo(await fetchModelInfo(), { boot: true });
  applyGraphInfo(await fetchGraphInfo());
  $("showModel").checked = state.model.show;
  $("showTraffic").checked = state.traffic.show;
  ensureLayers();
  reconcile();
  renderReadouts();
  updateStatus();
  state.booted = true;
  setTimeout(releaseMinor, 2000);        // in case the arterial source never reports ready
  styleJSON("light").catch(() => {});    // warm both basemaps so a theme switch is instant
  styleJSON("dark").catch(() => {});
});

/* ═════════════════════════ diagnostics ═════════════════════════
   Read-only handle for the reliability and UI test suites. The shape of
   health() is part of that contract: the suites read these exact fields. */

window.__track = {
  state, T, MINOR_FADE, warnings, events, styleReady, ensureLayers, reconcile,
  // the status bar draws from state.runs; exposing its renderer lets a test
  // drive states that live data only reaches once every ten minutes
  renderCollectBar,
  health() {
    const ready = styleReady();
    const layer = (id) => {
      if (!ready) return { present: false };
      const l = attempt(`getLayer ${id}`, () => map.getLayer(id));
      if (!l) return { present: false };
      return { present: true, minzoom: l.minzoom,
               visibility: attempt(`getLayout ${id}`,
                 () => map.getLayoutProperty(id, "visibility")) || "visible" };
    };
    return {
      styleReady: ready, theme: state.theme, appliedTheme: state.appliedTheme, zoom: map.getZoom(),
      traffic: layer("traffic"), major: layer(layerId("major")), minor: layer(layerId("minor")),
      graphNodes: layer(graphLayerId("nodes")), graphLinks: layer(graphLayerId("links")),
      // Not a raw frame handle: breathTick zeroes it at the top and only
      // re-arms at the bottom, so for the whole of applyBreath() it reads 0
      // while the loop is perfectly alive. Measured: six such windows in three
      // seconds, each one sample wide, with the dash still advancing. Report
      // what is true of the pulse, not what a handle holds at that instant.
      graph: { ...state.graph, breathing: breathAlive() },
      sources: {
        traffic: hasSource("traffic"),
        major: hasSource(sourceId("major")),
        minor: hasSource(sourceId("minor")),
      },
      model: {
        ready: state.model.ready, show: state.model.show, version: state.model.version,
        minorReady: state.model.minorReady, opacity: state.model.opacity, which: state.model.which,
        intro: { ...state.model.intro }, reveal: { ...state.model.reveal },
        revealed: { ...state.model.revealed }, anim: { ...state.model.anim },
        retry: { ...state.model.retry },
      },
      warnings: warnings.slice(),
    };
  },
};
