/* The list of captures, which one is showing, and how collection is going.

   A capture that arrives while the page is open is picked up by the poll in
   boot.js: it is listed, marked on the timeline, and shown, unless an older
   one was chosen on purpose, in which case the choice is left alone. */

import { $, escapeHtml, state } from "./core.js";
import { STATIC, apiJson, getManifest, loadManifest } from "./api.js";
import { ensureLayers, hasSource } from "./mapkit.js";
import { fitCapture, syncTrafficTiles } from "./basemap.js";

export const newestCapture = (list) => list.reduce((a, b) =>
  (Date.parse(b.captured_utc) || 0) > (Date.parse(a.captured_utc) || 0) ? b : a);

/* The timeline owns the track; it hands us a rebuild function rather than us
   importing it, so captures.js and timeline.js do not depend on each other. */
let onCapturesChanged = () => {};
export function setCapturesChangedHook(fn) { onCapturesChanged = fn; }

export function fillCaptureSelect() {
  const sel = $("capture");
  const current = sel.value;
  sel.innerHTML = "";
  if (!state.captures.length) {
    const o = document.createElement("option");
    o.value = "";
    o.textContent = "no capture yet";
    o.disabled = o.selected = true;
    sel.appendChild(o);
    return;
  }
  for (const c of state.captures) {
    const o = document.createElement("option");
    o.value = c.name;
    o.textContent = c.name;
    sel.appendChild(o);
  }
  if (state.captures.some((c) => c.name === current)) sel.value = current;
}

export function selectCapture(name, fit) {
  const next = state.captures.find((c) => c.name === name) || null;
  const changed = !state.capture || !next || state.capture.name !== next.name;
  state.capture = next;
  if (!next) { $("capInfo").textContent = "no captures found"; return; }
  if (changed) {
    if (hasSource("traffic")) syncTrafficTiles();
    else ensureLayers();
  }
  const when = (next.captured_utc || "").replace("T", " ").replace("Z", "");
  $("capInfo").innerHTML =
    `${escapeHtml(next.status)} · z${next.zoom} · ${next.coverage_pct}% coverage · ` +
    `${next.tiles_nonempty ?? "?"} of ${next.expected_tiles ?? "?"} tiles carry traffic<br>${escapeHtml(when)} UTC`;
  if ($("capture").value !== name) $("capture").value = name;
  if (fit) fitCapture();
}

export async function loadCaptures() {
  try { state.captures = await apiJson("/api/captures"); } catch { state.captures = []; }
  if (!Array.isArray(state.captures)) state.captures = [];
  fillCaptureSelect();
  if (state.captures.length) {
    const newest = newestCapture(state.captures);
    $("capture").value = newest.name;
    selectCapture(newest.name, true);
  }
  renderCollection();
}

export async function refreshCaptures() {
  let list;
  try { list = await apiJson("/api/captures"); } catch { return; }
  if (!Array.isArray(list)) return;
  const names = (l) => l.map((c) => c.name).join("|");
  if (names(list) === names(state.captures)) { renderCollection(); return; }
  const onNewest = !state.capture || !state.captures.length
    || state.capture.name === newestCapture(state.captures).name;
  state.captures = list;
  fillCaptureSelect();
  onCapturesChanged();
  if (list.length && onNewest) {
    const newest = newestCapture(list);
    $("capture").value = newest.name;
    selectCapture(newest.name, false);
  }
  renderCollection();
}

/* ═════════════════════════ how collection is going ═════════════════════════ */

export const ageText = (ms) => {
  const m = Math.round(ms / 60000);
  return m < 1 ? "under a minute ago" : m < 60 ? `${m} min ago` : `${Math.floor(m / 60)} h ${m % 60} min ago`;
};

function runSentence() {
  const c = state.runs.collect;
  if (!c) return state.runs.at ? "no collection run yet" : null;
  const n = `#${c.run_number}`;
  if (c.status !== "completed") {
    const mins = Math.max(0, Math.round((Date.now() - Date.parse(c.run_started_at || c.created_at)) / 60000));
    return `collection run ${n} in progress, ${mins} min in; a run takes about 8`;
  }
  return `last collection run ${n} ${escapeHtml(c.conclusion || "ended")} at ${(c.updated_at || "").slice(11, 16)} UTC`;
}

export function renderCollection() {
  const el = $("collectInfo");
  if (!el) return;
  const m = STATIC ? getManifest() : null;
  const parts = [];
  if (state.captures.length) {
    const c = newestCapture(state.captures);
    const t = Date.parse(c.captured_utc);
    parts.push(`Latest capture ${escapeHtml((c.captured_utc || "").slice(11, 16))} UTC`
      + (isNaN(t) ? "" : `, ${ageText(Date.now() - t)}`));
  } else {
    parts.push("No capture yet. The map shows the road graph until the first collection run finishes");
  }
  const run = runSentence();
  if (run) parts.push(run);
  if (m && m.built_utc) parts.push(`site built ${escapeHtml(m.built_utc.slice(11, 16))} UTC`);
  parts.push(STATIC
    ? "collection is triggered every 10 min and a run takes about 8, so new data lands about every 10 min"
    : "the dev server checks for new data every 5 s");
  let html = parts.join(" · ");
  if (m && m.runs_url) {
    html += ` · <a href="${escapeHtml(m.runs_url)}" target="_blank" rel="noopener">run history</a>`;
  }
  if (el.innerHTML !== html) el.innerHTML = html;
}

/* The current run, from GitHub's public API: the repository is public, the API
   allows cross-origin reads, and 60 requests an hour need no token, so the page
   asks every two minutes and keeps the last answer when refused. */
export const RUN_POLL = 120000;

export async function fetchRuns() {
  const m = STATIC ? (getManifest() || await loadManifest()) : null;
  if (!m || !m.runs_api) return;
  try {
    const r = await fetch(`${m.runs_api}?per_page=6`, { headers: { Accept: "application/vnd.github+json" } });
    if (r.ok) {
      const runs = (await r.json()).workflow_runs || [];
      const pick = (name) => runs.find((x) => x.name === name) || null;
      state.runs = { collect: pick("collect"), deploy: pick("deploy"), at: Date.now() };
    }
  } catch { /* offline or rate limited: keep the last answer */ }
  renderRunStatus();
  renderCollection();
}

export function renderRunStatus() {
  const el = $("runStatus");
  if (!el) return;
  const m = STATIC ? getManifest() : null;
  const brief = (run, verb) => {
    if (!run) return null;
    if (run.status !== "completed") {
      const mins = Math.max(0, Math.round((Date.now() - Date.parse(run.run_started_at || run.created_at)) / 60000));
      return { busy: true, text: `${verb} #${run.run_number}, ${mins} min in` };
    }
    return { busy: false,
             text: `last ${verb} ${run.conclusion || "ended"} ${(run.updated_at || "").slice(11, 16)} UTC` };
  };
  const c = brief(state.runs.collect, "collection");
  const d = brief(state.runs.deploy, "deploy");
  const show = !m || !m.runs_api ? null : (c && c.busy) ? c : (d && d.busy) ? d : c || d;
  el.hidden = !show;
  if (!show) return;
  el.classList.toggle("busy", show.busy);
  el.innerHTML = `<i></i><a href="${escapeHtml(m.runs_url || "#")}" target="_blank" rel="noopener">${escapeHtml(show.text)}</a>`;
}
