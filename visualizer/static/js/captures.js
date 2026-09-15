/* The list of captures, which one is showing, and how collection is going.

   A capture that arrives while the page is open is picked up by the poll in
   boot.js: it is listed, marked on the timeline, and shown, unless an older
   one was chosen on purpose, in which case the choice is left alone. */

import { $, escapeHtml, state } from "./core.js";
import { STATIC, apiJson, getManifest } from "./api.js";
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

/* The line under the capture controls. The run itself is reported by the
   status bar in the top bar (collection.js), so this says what the data is,
   not what the workflow is doing. */
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
  if (m && m.built_utc) parts.push(`site built ${escapeHtml(m.built_utc.slice(11, 16))} UTC`);
  parts.push(STATIC
    ? "collection runs every 10 minutes"
    : "the dev server checks for new data every 5 s");
  let html = parts.join(" · ");
  if (m && m.runs_url) {
    html += ` · <a href="${escapeHtml(m.runs_url)}" target="_blank" rel="noopener">run history</a>`;
  }
  if (el.innerHTML !== html) el.innerHTML = html;
}
