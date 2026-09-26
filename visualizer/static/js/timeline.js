/* The time track along the bottom.

   The track indexes CAPTURES, not clock slots. It used to quantise the week
   into 672 fifteen-minute slots, which worked while captures were 20 minutes
   apart and broke the moment they were not: at one capture every 6 minutes,
   twelve of them collapsed into five slots, seven were unreachable, and play
   froze because stepping from a slot landed back on the same slot. Now every
   capture is its own stop and its position comes from its timestamp, so two
   captures four minutes apart are still two places to be.

   The track spans whatever the captures cover, not a calendar week. Anchoring
   to the Monday of the newest capture's week hid everything before it: a run
   from Sep 16 to Sep 23 drew only the last three days.

   It also zooms. A week of captures is over a thousand marks in a few hundred
   pixels, so the view is a window [view0, view1] of the whole span and the
   wheel narrows it around the pointer. */

import { $, DHAKA_OFFSET, clamp, clock12, dayLabel, dayShort, dhaka, state } from "./core.js";
import { fillCaptureSelect, selectCapture, setCapturesChangedHook } from "./captures.js";

const STEP_MS = 900;                           // one capture per this long at 1x
const GAP_MIN = 25;                            // a longer wait than this is a gap
const MIN_VIEW = 1 / 2000;                     // deepest zoom: about one capture wide

/** Midnight Dhaka time on the day containing t, as a UTC instant. */
function dayStart(t) {
  const d = dhaka(t);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) - DHAKA_OFFSET;
}

/* ── the stops ─────────────────────────────────────────────────────────── */

/** Every capture with a readable timestamp, oldest first. */
export function allCaptures() {
  return state.captures
    .filter((c) => !isNaN(Date.parse(c.captured_utc)))
    .sort((a, b) => Date.parse(a.captured_utc) - Date.parse(b.captured_utc));
}

export function buildTimeline() {
  const tl = state.timeline;
  // A rebuild replaces every stop, so a running timer is stepping through a
  // list that no longer exists. Clear it, remember it was playing, and start a
  // fresh one at the end: leaving `playing` true with an orphaned interval
  // behind it was why pause could not stop playback.
  const wasPlaying = tl.playing;
  clearInterval(tl.timer);
  tl.timer = null;
  tl.playing = false;

  const mine = allCaptures();
  const first = mine.length ? Date.parse(mine[0].captured_utc) : Date.now();
  const last = mine.length ? Date.parse(mine[mine.length - 1].captured_utc) : first;
  tl.start = dayStart(first);
  tl.end = dayStart(last) + 86400000;           // to the end of the last day
  tl.span = Math.max(tl.end - tl.start, 3600000);
  tl.days = Math.max(1, Math.round(tl.span / 86400000));

  tl.stops = mine.map((c) => {
    const t = Date.parse(c.captured_utc);
    return {
      name: c.name,
      t,
      at: (t - tl.start) / tl.span,             // 0..1 across the whole span
      coverage: c.coverage_pct,
      tiles: c.tiles_nonempty,
      status: c.status,
    };
  });

  // gaps: where the sequence skipped a beat
  tl.gaps = new Set();
  for (let k = 1; k < tl.stops.length; k++) {
    if ((tl.stops[k].t - tl.stops[k - 1].t) / 60000 > GAP_MIN) tl.gaps.add(k);
  }

  if (!Number.isFinite(tl.view0)) { tl.view0 = 0; tl.view1 = 1; }

  // stay on the capture already showing, else the newest
  const here = tl.stops.findIndex((s) => s.name === (state.capture && state.capture.name));
  tl.index = here >= 0 ? here : Math.max(0, tl.stops.length - 1);

  renderTrack();
  renderTimeline();
  if (tl.stops.length && here < 0) {
    const name = tl.stops[tl.index].name;
    if (name !== (state.capture && state.capture.name)) selectCapture(name, false);
  }
  if (wasPlaying) startPlay();                  // a new capture must not stop play
}

/* ── zoom ──────────────────────────────────────────────────────────────── */

/** Where a stop sits in the visible window, 0..1, or outside it. */
const inView = (at) => {
  const tl = state.timeline;
  return (at - tl.view0) / Math.max(tl.view1 - tl.view0, 1e-9);
};

/** Zoom by a factor about a point in the window (0..1 across the track). */
export function zoomAt(factor, pointer = 0.5) {
  const tl = state.timeline;
  if (!tl.stops.length) return;
  const w = tl.view1 - tl.view0;
  const anchor = tl.view0 + clamp(pointer, 0, 1) * w;
  const next = clamp(w * factor, MIN_VIEW, 1);
  // keep the anchor under the pointer, then push the window inside 0..1
  let a = anchor - (anchor - tl.view0) * (next / w);
  a = clamp(a, 0, 1 - next);
  tl.view0 = a;
  tl.view1 = a + next;
  renderTrack();
  renderTimeline();
}

/** Show everything again. */
export function zoomReset() {
  const tl = state.timeline;
  tl.view0 = 0;
  tl.view1 = 1;
  renderTrack();
  renderTimeline();
}

/** Slide the window without changing its width. */
export function panView(fraction) {
  const tl = state.timeline;
  const w = tl.view1 - tl.view0;
  const a = clamp(tl.view0 + fraction * w, 0, 1 - w);
  tl.view0 = a;
  tl.view1 = a + w;
  renderTrack();
  renderTimeline();
}

/** Keep the playhead in view while playing at a deep zoom. */
function scrollIntoView(at) {
  const tl = state.timeline;
  const w = tl.view1 - tl.view0;
  if (at >= tl.view0 && at <= tl.view1) return;
  const a = clamp(at - w / 2, 0, 1 - w);
  tl.view0 = a;
  tl.view1 = a + w;
  renderTrack();
}

/* ── drawing ───────────────────────────────────────────────────────────── */

/* The ticks above the track. Zoomed out they are days; zoomed in they become
   hours, because a day label on a two-hour window says nothing. */
function renderTrack() {
  const tl = state.timeline;
  const w = tl.view1 - tl.view0;
  const visMs = tl.span * w;
  const days = $("trackDays");
  if (!days) return;

  const marks = [];
  if (visMs <= 8 * 3600000) {                   // under 8 hours: hourly
    const step = visMs <= 2 * 3600000 ? 1800000 : 3600000;
    const t0 = tl.start + tl.view0 * tl.span;
    let t = Math.ceil(t0 / step) * step;
    for (; t < t0 + visMs; t += step) {
      marks.push([inView((t - tl.start) / tl.span), clock12(t)]);
    }
  } else {                                      // otherwise one per day shown
    for (let i = 0; i <= tl.days; i++) {
      const t = tl.start + i * 86400000;
      const at = (t - tl.start) / tl.span;
      if (at < tl.view0 - 1e-9 || at > tl.view1 + 1e-9) continue;
      marks.push([inView(at), visMs <= 3 * 86400000 ? dayLabel(t) : dayShort(t)]);
    }
  }
  days.innerHTML = marks
    .map(([at, s]) => `<span style="left:${at * 100}%">${s}</span>`)
    .join("");

  // the marks themselves, only those in the window
  const out = [];
  for (let i = 0; i < tl.stops.length; i++) {
    const s = tl.stops[i];
    if (s.at < tl.view0 - 1e-9 || s.at > tl.view1 + 1e-9) continue;
    const cls = [];
    if (tl.gaps.has(i)) cls.push("gap");
    if (i === tl.index) cls.push("here");
    if (s.status && s.status !== "ok") cls.push("warn");
    out.push(`<i class="${cls.join(" ")}" data-i="${i}" style="left:${inView(s.at) * 100}%"></i>`);
  }
  $("trackMarks").innerHTML = out.join("");

  const span = $("trackSpan");
  if (tl.stops.length > 1) {
    const a = inView(tl.stops[0].at) * 100;
    const b = inView(tl.stops[tl.stops.length - 1].at) * 100;
    span.style.left = `${clamp(a, 0, 100)}%`;
    span.style.width = `${clamp(b, 0, 100) - clamp(a, 0, 100)}%`;
    span.hidden = false;
  } else {
    span.hidden = true;
  }

  const zl = $("tlZoomLabel");
  if (zl) {
    zl.textContent = w >= 0.999 ? "whole run"
      : visMs < 3600000 ? `${Math.round(visMs / 60000)} min`
      : visMs < 86400000 ? `${(visMs / 3600000).toFixed(1)} h`
      : `${(visMs / 86400000).toFixed(1)} days`;
  }
}

export function renderTimeline() {
  const tl = state.timeline;
  const stop = tl.stops[tl.index];

  const head = $("trackHead");
  if (stop) {
    const at = inView(stop.at);
    head.style.left = `${clamp(at, 0, 1) * 100}%`;
    head.hidden = at < -0.01 || at > 1.01;
  } else {
    head.style.left = "0%";
    head.hidden = false;
  }

  $("slotLabel").textContent = stop
    ? `${dayLabel(stop.t)} · ${clock12(stop.t)}`
    : "—";

  document.querySelectorAll("#trackMarks i").forEach((el) => {
    el.classList.toggle("here", Number(el.dataset.i) === tl.index);
  });

  const st = $("slotState");
  const n = tl.stops.length;
  if (!n) {
    st.textContent = "no captures yet";
    st.className = "";
  } else {
    st.textContent = `capture ${tl.index + 1} of ${n}`;
    st.className = "live";
  }

  const count = $("seriesCount");
  if (count) {
    count.textContent = n
      ? `${n} captures · ${tl.days} day${tl.days === 1 ? "" : "s"}`
      : "";
  }
  renderStopCard();
}

/* What one capture is: shown under the track when a mark is clicked. */
function renderStopCard() {
  const card = $("stopCard");
  if (!card) return;
  const tl = state.timeline;
  const s = tl.stops[tl.index];
  if (!s || !tl.cardOpen) { card.hidden = true; return; }

  const prev = tl.index > 0 ? tl.stops[tl.index - 1] : null;
  const gapMin = prev ? Math.round((s.t - prev.t) / 60000) : null;
  const rows = [
    ["When", `${dayLabel(s.t)}, ${clock12(s.t)}`],
    ["Since previous", gapMin === null ? "first capture"
      : `${gapMin} min${tl.gaps.has(tl.index) ? " — gap" : ""}`],
    ["Coverage", s.coverage != null ? `${s.coverage}%` : "—"],
    ["Painted tiles", s.tiles != null ? s.tiles.toLocaleString() : "—"],
    ["Status", s.status || "—"],
    ["Capture", s.name],
  ];
  card.innerHTML =
    `<button class="icon close" id="stopCardClose" aria-label="Close">×</button>` +
    rows.map(([k, v]) => `<div class="row"><span>${k}</span><b>${v}</b></div>`).join("");
  card.hidden = false;
  const x = $("stopCardClose");
  if (x) x.onclick = () => { tl.cardOpen = false; renderStopCard(); };
}

/* ── moving about ──────────────────────────────────────────────────────── */

/** Go to a stop by its position in the list. */
export function setStop(i, fromUser) {
  const tl = state.timeline;
  if (!tl.stops.length || !Number.isFinite(i)) return;
  tl.index = clamp(Math.round(i), 0, tl.stops.length - 1);
  const name = tl.stops[tl.index].name;
  if (name !== (state.capture && state.capture.name)) selectCapture(name, false);
  if (tl.playing) scrollIntoView(tl.stops[tl.index].at);
  renderTimeline();
  if (fromUser && tl.playing) stopPlay();
}

/** Go to whichever capture sits nearest a fraction across the VISIBLE window.
    Dragging wants this: the space between captures holds nothing to show. */
export function setAt(fraction, fromUser) {
  const tl = state.timeline;
  if (!tl.stops.length || !Number.isFinite(fraction)) return;
  const target = tl.view0 + clamp(fraction, 0, 1) * (tl.view1 - tl.view0);
  let best = 0;
  for (let i = 1; i < tl.stops.length; i++) {
    if (Math.abs(tl.stops[i].at - target) < Math.abs(tl.stops[best].at - target)) best = i;
  }
  setStop(best, fromUser);
}

/** Step to the next or previous capture, wrapping at the ends. */
export function stepCapture(dir) {
  const tl = state.timeline;
  if (!tl.stops.length) return;
  setStop((tl.index + dir + tl.stops.length) % tl.stops.length, false);
}

export function setSpeed(mult) {
  const tl = state.timeline;
  tl.speed = clamp(Number(mult) || 1, 0.25, 8);
  document.querySelectorAll("#playSpeed button")
    .forEach((b) => b.classList.toggle("on", Number(b.dataset.speed) === tl.speed));
  if (tl.playing) { stopPlay(); startPlay(); }      // pick up the new rate at once
}

export function startPlay() {
  const tl = state.timeline;
  if (tl.playing || tl.stops.length < 2) return;
  clearInterval(tl.timer);        // never leave a second interval running
  tl.playing = true;
  $("playIcon").innerHTML = '<path d="M4.5 3h2.6v10H4.5zM8.9 3h2.6v10H8.9z" fill="currentColor"/>';
  $("playBtn").title = "Pause (space)";
  tl.timer = setInterval(() => stepCapture(1), STEP_MS / tl.speed);
}

export function stopPlay() {
  const tl = state.timeline;
  tl.playing = false;
  clearInterval(tl.timer);
  tl.timer = null;
  $("playIcon").innerHTML = '<path d="M5 3.2l7.2 4.8-7.2 4.8z" fill="currentColor"/>';
  $("playBtn").title = "Play (space)";
}

export const togglePlay = () => (state.timeline.playing ? stopPlay() : startPlay());

export function bindTrack() {
  const track = $("track");
  const toFraction = (ev) => {
    const r = track.getBoundingClientRect();
    return r.width > 0 ? (ev.clientX - r.left) / r.width : null;
  };
  let dragging = false;
  let downAt = 0;

  track.addEventListener("pointerdown", (e) => {
    dragging = true;
    downAt = Date.now();
    try { track.setPointerCapture(e.pointerId); } catch (err) { /* synthetic events */ }
    // clicking a mark opens its details; anywhere else just moves the head
    const mark = e.target && e.target.closest && e.target.closest("#trackMarks i");
    if (mark) {
      state.timeline.cardOpen = true;
      setStop(Number(mark.dataset.i), true);
    } else {
      setAt(toFraction(e), true);
    }
  });
  track.addEventListener("pointermove", (e) => { if (dragging) setAt(toFraction(e), true); });
  const end = (e) => {
    dragging = false;
    try { track.releasePointerCapture(e.pointerId); } catch (err) { /* already released */ }
  };
  track.addEventListener("pointerup", end);
  track.addEventListener("pointercancel", end);

  // the wheel zooms about the pointer; with shift it steps between captures
  track.addEventListener("wheel", (e) => {
    e.preventDefault();
    const dir = Math.sign(e.deltaY || e.deltaX) || 1;
    if (e.shiftKey) {
      if (state.timeline.playing) stopPlay();
      stepCapture(dir);
    } else {
      zoomAt(dir > 0 ? 1.25 : 0.8, toFraction(e) ?? 0.5);
    }
  }, { passive: false });

  // double click zooms in hard about that point, and out again if already deep
  track.addEventListener("dblclick", (e) => {
    const tl = state.timeline;
    if (tl.view1 - tl.view0 < 0.05) zoomReset();
    else zoomAt(0.15, toFraction(e) ?? 0.5);
  });

  const on = (id, fn) => { const el = $(id); if (el) el.onclick = fn; };
  on("tlZoomIn", () => zoomAt(0.6, 0.5));
  on("tlZoomOut", () => zoomAt(1 / 0.6, 0.5));
  on("tlZoomAll", zoomReset);
}

setCapturesChangedHook(buildTimeline);
