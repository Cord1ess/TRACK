/* The TRACK model through time.

   The road shapes never change from one capture to the next; only the colours
   do. So the map loads the shapes once, and each processed capture is a FRAME:
   one character per map line, "0".."7" = level 0..3 (green .. dark red), plus 4
   when the road was predicted rather than read from Google's tiles. A frame is
   about 15 KB compressed against 1.6 MB for the whole city.

   Showing a frame sets MapLibre feature state (`w`, `s`) on the lines whose
   colour changed since the last one shown, never on the whole city. That is
   what keeps playback smooth: between two captures eight minutes apart only a
   small share of roads change level, and only those are touched.

   A capture with no frame is not processed. The model is hidden for it rather
   than left showing another moment's traffic under this one's timestamp. */

import { $, LADDER, RAMP, attempt, clock12, dayLabel, escapeHtml, map, sourceId, state } from "./core.js";
import { SITE, STATIC, getManifest, loadManifest } from "./api.js";
import { hasSource } from "./mapkit.js";

const CACHE_MAX = 400;          // frames kept in memory, ~60 KB each
const PARALLEL = 4;             // frame downloads at once while prefetching

const fr = state.frames;
const cache = new Map();        // name -> {codes: Uint8Array, stats}
const inflight = new Map();     // name -> Promise
let applied = null;             // codes currently on the map, or null
let appliedFor = "";            // the lines version they were applied under

/* ── the index: which captures are processed ─────────────────────────────── */

export async function loadFrameIndex() {
  let idx = null;
  try {
    if (STATIC) {
      const m = getManifest() || await loadManifest();
      idx = m && m.frames;
    } else {
      const r = await fetch("/api/frames?parts=1", { cache: "no-store" });
      if (r.ok) idx = await r.json();
    }
  } catch { /* keep what we had */ }
  if (!idx || !idx.v) return false;
  if (idx.v !== fr.v) {
    // a different line list: every frame and every applied state is void
    cache.clear();
    inflight.clear();
    clearApplied();
    fr.parts = Uint8Array.from(idx.parts || "", (c) => c.charCodeAt(0) - 48);
  }
  fr.v = idx.v;
  fr.lines = idx.lines;
  fr.base = idx.base || "";
  fr.files = idx.files || null;
  fr.processed = new Set(idx.processed || []);
  fr.stale = new Set(idx.stale || []);
  return true;
}

export const isProcessed = (name) => fr.processed.has(name);

/* ── fetching ─────────────────────────────────────────────────────────────── */

function frameUrl(name) {
  if (STATIC) return fr.files && fr.files[name] ? `${SITE}${fr.files[name]}` : null;
  return `/frame/${encodeURIComponent(name)}?v=${encodeURIComponent(fr.v)}`;
}

export function getFrame(name) {
  if (cache.has(name)) {
    const hit = cache.get(name);
    cache.delete(name);                       // most recently used goes last
    cache.set(name, hit);
    return Promise.resolve(hit);
  }
  if (inflight.has(name)) return inflight.get(name);
  const url = frameUrl(name);
  if (!url || !isProcessed(name)) return Promise.resolve(null);
  const v = fr.v;
  const p = fetch(url, { cache: STATIC ? "default" : "force-cache" })
    .then((r) => (r.ok ? r.json() : null))
    .then((j) => {
      inflight.delete(name);
      if (!j || j.v !== v || fr.v !== v || j.codes.length !== fr.lines) return null;
      // a plain loop: Uint8Array.from with a callback per character cost 5 ms a
      // frame, a fifth of what playback spent in this file
      const codes = new Uint8Array(j.codes.length);
      for (let n = 0; n < codes.length; n++) codes[n] = j.codes.charCodeAt(n) - 48;
      const out = { codes, stats: j.stats };
      cache.set(name, out);
      while (cache.size > CACHE_MAX) cache.delete(cache.keys().next().value);
      return out;
    })
    .catch(() => { inflight.delete(name); return null; });
  inflight.set(name, p);
  return p;
}

/** Fetch these frames ahead of need, a few at a time. */
export async function prefetch(names) {
  const want = names.filter((n) => isProcessed(n) && !cache.has(n) && !inflight.has(n));
  let i = 0;
  const worker = async () => { while (i < want.length) await getFrame(want[i++]); };
  await Promise.all(Array.from({ length: Math.min(PARALLEL, want.length) }, worker));
}

export const cachedStats = (name) => (cache.get(name) || {}).stats || null;

/* ── putting a frame on the map ───────────────────────────────────────────── */

function clearApplied() {
  applied = null;
  appliedFor = "";
  for (const part of ["major", "minor"]) {
    if (hasSource(sourceId(part))) attempt("removeFeatureState", () => map.removeFeatureState({ source: sourceId(part) }));
  }
}

/** Called when a model source is (re)created: its feature state starts empty. */
export function sourcesReset() {
  applied = null;
}

function paint(codes) {
  const parts = fr.parts;
  const sources = [sourceId("major"), sourceId("minor")];
  if (!hasSource(sources[0])) return false;
  const full = !applied || appliedFor !== fr.v;
  let changed = 0;
  for (let n = 0; n < codes.length; n++) {
    const c = codes[n];
    if (!full && applied[n] === c) continue;
    const src = sources[parts[n] || 0];
    if (parts[n] === 1 && !hasSource(src)) continue;     // side streets not added yet
    map.setFeatureState({ source: src, id: n }, { w: LADDER[c & 3], s: c >> 2 });
    changed++;
  }
  // the side streets arrive after the arteries; until they do, a full paint is
  // not complete, so the next one has to be full again
  applied = hasSource(sources[1]) ? codes : null;
  appliedFor = fr.v;
  fr.lastChanged = changed;
  return true;
}

let showSeq = 0;

/** Show one capture's model. Resolves once its colours are set (or the model
    is hidden because the capture is not processed). */
export async function showFrame(name) {
  const seq = ++showSeq;
  fr.current = name;
  if (!name || !isProcessed(name)) {
    fr.hidden = true;
    onVisibility();
    renderModelInfo();
    return false;
  }
  const f = await getFrame(name);
  if (seq !== showSeq) return false;           // a newer request has taken over
  if (!f) {
    fr.hidden = true;
    onVisibility();
    renderModelInfo(`could not load this capture's model data`);
    return false;
  }
  const t0 = performance.now();
  attempt("paint frame", () => paint(f.codes));
  fr.lastPaintMs = performance.now() - t0;
  fr.hidden = false;
  fr.shown = name;
  onVisibility();
  renderModelInfo();
  return true;
}

/** Re-apply the current frame in full: after the sources were rebuilt. */
export function repaint() {
  applied = null;
  if (fr.current) showFrame(fr.current);
}

/* model.js hands us its visibility applier, so the dependency points one way */
let onVisibility = () => {};
export function setVisibilityHook(fn) { onVisibility = fn; }

/* ── the words under the TRACK model switch ───────────────────────────────── */

const pct = (a, b) => (b ? Math.round((100 * a) / b) : 0);

export function renderModelInfo(problem = "") {
  const el = $("modelInfo");
  if (!el || !fr.v) return;
  const c = state.capture;
  const at = c ? Date.parse(c.captured_utc) : NaN;
  const when = isNaN(at) ? "" : `${dayLabel(at)} · ${clock12(at)}`;
  const total = state.captures.length;
  const done = fr.processed.size;
  const tally = `${done.toLocaleString()} of ${total.toLocaleString()} captures processed` +
    (fr.stale.size ? ` · ${fr.stale.size.toLocaleString()} from the old model need running again` : "");
  let html;
  if (problem) {
    html = `<b>${escapeHtml(when)}</b><br><span class="warn">${escapeHtml(problem)}</span>`;
  } else if (!c || !isProcessed(c.name)) {
    const why = c && fr.stale.has(c.name) ? "processed by the old model: run it again" : "not processed yet";
    html = `<b>${escapeHtml(when)}</b> · ${why}<br>${tally}`;
  } else {
    const st = cachedStats(c.name);
    if (!st) {
      html = `<b>${escapeHtml(when)}</b> · loading…`;
    } else {
      const lv = st.levels || [0, 0, 0, 0];
      const dots = ["jam", "heavy", "slow", "free"].map((label, i) => {
        const k = 3 - i;
        return `<span class="lv"><i style="background:${RAMP[k]}"></i>${lv[k].toLocaleString()} ${label}</span>`;
      }).join(" ");
      html = `<b>${escapeHtml(when)}</b><br>` +
        `${st.observed.toLocaleString()} roads read from Google (${pct(st.observed, st.edges)}%) · ` +
        `${st.predicted.toLocaleString()} predicted<br>${dots}<br>${tally}`;
    }
  }
  if (el.innerHTML !== html) el.innerHTML = html;
}
