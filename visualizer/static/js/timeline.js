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

   It zooms: a week of captures is over a thousand marks in a few hundred
   pixels, so the view is a window [view0, view1] of the whole span and the
   wheel narrows it around the pointer.

   Marks are blue when TRACK has processed the capture and purple when it has
   not. Clicking one opens a popup above the track rather than growing the
   panel, which pushed the whole timeline up the screen.

   Play never lets steps pile up. The next capture's model colours are in hand
   before the head moves. With Google's layer on, each step waits for its
   images, so play runs as fast as they arrive. With the model alone, play
   keeps the clock: a step recolours only the roads that changed, a few
   milliseconds, and the map draws the newest capture each time it draws. At
   20x that can mean a capture or two between draws is never drawn, like a
   video dropping frames; whatever is on screen is always the capture the
   timeline names, and a paused map is exactly it. */

import { $, DHAKA_OFFSET, clamp, clock12, dayLabel, dayShort, dhaka, escapeHtml, map, RAMP, state } from "./core.js";
import { fillCaptureSelect, selectCapture, setCapturesChangedHook } from "./captures.js";
import { getFrame, isProcessed, prefetch } from "./frames.js";

const STEP_MS = 900;                           // one capture per this long at 1x
const MAX_SPEED = 20;
const GAP_MIN = 25;                            // a longer wait than this is a gap
const MIN_VIEW = 1 / 2000;                     // deepest zoom: about one capture wide
const AHEAD = 60;                              // model frames fetched ahead of the head
const IMAGE_WAIT_MS = 2500;                    // longest a step waits for Google's images

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
  // A rebuild replaces every stop. Stop play and start it again at the end,
  // over the new list, rather than leave a loop stepping through the old one.
  const wasPlaying = tl.playing;
  if (wasPlaying) stopPlay();

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
      expected: c.expected_tiles,
      status: c.status,
      processed: !!c.processed,
      stale: !!c.stale,
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

  closePopup();
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
  closePopup();
  renderTrack();
  renderTimeline();
}

/** Show everything again. */
export function zoomReset() {
  const tl = state.timeline;
  tl.view0 = 0;
  tl.view1 = 1;
  closePopup();
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
  closePopup();
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

  const ticks = [];
  if (visMs <= 8 * 3600000) {                   // under 8 hours: hourly
    const step = visMs <= 2 * 3600000 ? 1800000 : 3600000;
    const t0 = tl.start + tl.view0 * tl.span;
    let t = Math.ceil(t0 / step) * step;
    for (; t < t0 + visMs; t += step) {
      ticks.push([inView((t - tl.start) / tl.span), clock12(t)]);
    }
  } else {                                      // otherwise one per day shown
    for (let i = 0; i <= tl.days; i++) {
      const t = tl.start + i * 86400000;
      const at = (t - tl.start) / tl.span;
      if (at < tl.view0 - 1e-9 || at > tl.view1 + 1e-9) continue;
      ticks.push([inView(at), visMs <= 3 * 86400000 ? dayLabel(t) : dayShort(t)]);
    }
  }
  days.innerHTML = ticks
    .map(([at, s]) => `<span style="left:${at * 100}%">${s}</span>`)
    .join("");

  // the marks themselves, only those in the window: blue processed, purple not
  const out = [];
  for (let i = 0; i < tl.stops.length; i++) {
    const s = tl.stops[i];
    if (s.at < tl.view0 - 1e-9 || s.at > tl.view1 + 1e-9) continue;
    const cls = [s.processed ? "done" : "todo"];
    if (s.stale) cls.push("stale");
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
  renderLegend();
}

/* How many captures are processed, as a key to the two mark colours. */
function renderLegend() {
  const el = $("seriesCount");
  if (!el) return;
  const tl = state.timeline;
  const done = tl.stops.filter((s) => s.processed).length;
  const stale = tl.stops.filter((s) => s.stale).length;
  const todo = tl.stops.length - done;
  const html = tl.stops.length
    ? `<span class="key"><i class="done"></i>${done.toLocaleString()} processed</span>` +
      `<span class="key"><i class="todo"></i>${todo.toLocaleString()} not processed` +
      `${stale ? ` (${stale.toLocaleString()} old model)` : ""}</span>`
    : "";
  if (el.innerHTML !== html) el.innerHTML = html;
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

  const prev = document.querySelector("#trackMarks i.here");
  const now = document.querySelector(`#trackMarks i[data-i="${tl.index}"]`);
  if (prev !== now) {
    if (prev) prev.classList.remove("here");
    if (now) now.classList.add("here");
  }

  const st = $("slotState");
  const n = tl.stops.length;
  if (!n) {
    st.textContent = "no captures yet";
    st.className = "";
  } else {
    st.textContent = `capture ${tl.index + 1} of ${n}` + (stop && !stop.processed ? " · not processed" : "");
    st.className = stop && stop.processed ? "live" : "";
  }
  if (popup.open && popup.i !== tl.index) closePopup();
}

/* ── the popup for one capture ─────────────────────────────────────────── */

const popup = { open: false, i: -1 };

function popupEl() {
  let el = $("stopPop");
  if (!el) {
    el = document.createElement("div");
    el.id = "stopPop";
    el.hidden = true;
    el.setAttribute("role", "dialog");
    document.body.appendChild(el);
  }
  return el;
}

export function closePopup() {
  if (!popup.open) return;
  popup.open = false;
  popup.i = -1;
  popupEl().hidden = true;
}

function row(k, v) {
  return `<div class="row"><span>${k}</span><b>${v}</b></div>`;
}

function popupBody(i, stats) {
  const tl = state.timeline;
  const s = tl.stops[i];
  const prev = i > 0 ? tl.stops[i - 1] : null;
  const gapMin = prev ? Math.round((s.t - prev.t) / 60000) : null;
  const tiles = s.tiles != null && s.expected != null
    ? `${Number(s.tiles).toLocaleString()} of ${Number(s.expected).toLocaleString()} tiles`
    : "—";
  let model;
  if (!s.processed) {
    model = s.stale ? "old model: run it again" : "not processed yet";
  } else if (!stats) {
    model = "loading…";
  } else {
    const lv = stats.levels || [0, 0, 0, 0];
    model = `${stats.observed.toLocaleString()} read · ${stats.predicted.toLocaleString()} predicted<br>` +
      [[3, "jam"], [2, "heavy"], [1, "slow"], [0, "free"]].map(([k, word]) =>
        `<span class="lv"><i style="background:${RAMP[k]}"></i>${lv[k].toLocaleString()} ${word}</span>`).join(" ");
  }
  return `<button class="icon close" id="stopPopClose" aria-label="Close">×</button>` +
    `<h4>${dayLabel(s.t)} · ${clock12(s.t)}</h4>` +
    row("Since previous", gapMin === null ? "first capture"
      : `${gapMin} min${tl.gaps.has(i) ? ' <span class="warn">gap</span>' : ""}`) +
    row("Google", `${tiles} · ${s.coverage ?? "?"}%` +
      (s.status && s.status !== "ok" ? ` · <span class="warn">${escapeHtml(s.status)}</span>` : "")) +
    row(`<i class="dot ${s.processed ? "done" : "todo"}"></i>TRACK`, model) +
    `<p class="name">${escapeHtml(s.name)}</p>`;
}

function placePopup(el, mark) {
  const tr = $("timeline").getBoundingClientRect();
  const mr = mark ? mark.getBoundingClientRect() : tr;
  const x = mr.left + mr.width / 2;
  const w = el.offsetWidth || 280;
  const left = clamp(x - w / 2, 8, window.innerWidth - w - 8);
  el.style.left = `${left}px`;
  el.style.bottom = `${window.innerHeight - tr.top + 10}px`;
  el.style.setProperty("--arrow", `${clamp(x - left, 14, w - 14)}px`);
}

function openPopup(i, mark) {
  const tl = state.timeline;
  const s = tl.stops[i];
  if (!s) return;
  const el = popupEl();
  popup.open = true;
  popup.i = i;
  el.innerHTML = popupBody(i, null);
  el.hidden = false;
  placePopup(el, mark);
  $("stopPopClose").onclick = closePopup;
  if (s.processed) {
    getFrame(s.name).then((f) => {
      if (!popup.open || popup.i !== i) return;
      el.innerHTML = popupBody(i, f && f.stats);
      placePopup(el, document.querySelector(`#trackMarks i[data-i="${i}"]`) || mark);
      $("stopPopClose").onclick = closePopup;
    });
  }
}

/* ── moving about ──────────────────────────────────────────────────────── */

/** Go to a stop by its position in the list. Returns a promise that settles
    once that capture's model colours are on the map (or it has none). */
export function setStop(i, fromUser) {
  const tl = state.timeline;
  if (!tl.stops.length || !Number.isFinite(i)) return Promise.resolve(false);
  tl.index = clamp(Math.round(i), 0, tl.stops.length - 1);
  const name = tl.stops[tl.index].name;
  const shown = name !== (state.capture && state.capture.name)
    ? selectCapture(name, false) : Promise.resolve(true);
  if (tl.playing) scrollIntoView(tl.stops[tl.index].at);
  // while stopped, fetch the next stretch in the background, so pressing play
  // starts from colours already in hand rather than waiting on each one
  else if (state.model.show && state.frames.v) prefetch(aheadOf(tl.index).slice(0, 30));
  renderTimeline();
  if (fromUser && tl.playing) stopPlay();
  return shown;
}

/** Move the head to a capture chosen somewhere else, such as the picker.
    Picking used to change the map and leave the timeline where it was, so the
    two named different moments. */
export function followCapture(name) {
  const tl = state.timeline;
  const i = tl.stops.findIndex((s) => s.name === name);
  if (i < 0) return;
  if (tl.playing) stopPlay();
  tl.index = i;
  scrollIntoView(tl.stops[i].at);
  renderTrack();
  renderTimeline();
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
  tl.speed = clamp(Number(mult) || 1, 0.25, MAX_SPEED);
  document.querySelectorAll("#playSpeed button")
    .forEach((b) => b.classList.toggle("on", Number(b.dataset.speed) === tl.speed));
  // the loop reads the speed on every step, so nothing needs restarting
}

/* ── play ──────────────────────────────────────────────────────────────── */

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** The step is on screen: wait for the map's next completed draw, which we
    ask for ourselves so a step that changed nothing cannot wait forever. One
    draw is enough; waiting two animation frames doubled every step (measured:
    102 ms a step at 20x, against 30 ms for the draw itself). A hidden tab
    draws nothing, so a timeout stands in rather than stalling play. */
const drawn = () => {
  map.triggerRepaint();
  return Promise.race([new Promise((r) => map.once("render", r)), sleep(250)]);
};

/** Google's images for this capture, when that layer is on: wait until the
    map has loaded them, but never longer than IMAGE_WAIT_MS. */
function imagesIn() {
  if (!state.traffic.show) return drawn();
  return new Promise((resolve) => {
    let done = false;
    const finish = () => { if (!done) { done = true; clearTimeout(timer); resolve(); } };
    const timer = setTimeout(finish, IMAGE_WAIT_MS);
    map.once("idle", finish);
  });
}

function aheadOf(i) {
  const tl = state.timeline;
  const out = [];
  for (let k = 1; k <= tl.stops.length && out.length < AHEAD; k++) {
    const s = tl.stops[(i + k) % tl.stops.length];
    if (s.processed) out.push(s.name);
  }
  return out;
}

/* Google's layer on: one capture per step, each waiting for its images, so
   play runs as fast as they arrive and steps never pile up. */
async function stepLoop(token, live) {
  const tl = state.timeline;
  while (live() && state.traffic.show) {
    const started = performance.now();
    const next = (tl.index + 1) % tl.stops.length;
    const stop = tl.stops[next];
    if (state.model.show && state.frames.v && stop && isProcessed(stop.name)) await getFrame(stop.name);
    if (!live()) return;
    if (state.model.show && state.frames.v) prefetch(aheadOf(next));
    await setStop(next, false);
    await imagesIn();
    if (!live()) return;
    tl.lastStepMs = performance.now() - started;
    await sleep(Math.max(0, STEP_MS / tl.speed - tl.lastStepMs));
  }
}

/* The model alone: keep the clock. Work out from the time played which
   capture is due now and go straight to it. The map can redraw a recolour of
   the city about fifteen times a second, not twenty-two, so giving every
   capture its own step made 20x run at 8x. Captures passed between two
   redraws are skipped, never half-drawn: what the map shows is always exactly
   the capture the timeline names, and a paused map is that capture. */
async function clockLoop(token, live) {
  const tl = state.timeline;
  let t0 = performance.now(), i0 = tl.index, speed = tl.speed, done = 0;
  while (live() && !state.traffic.show) {
    if (tl.speed !== speed) {                     // a new speed starts from here
      t0 = performance.now(); i0 = tl.index; speed = tl.speed; done = 0;
    }
    const due = Math.floor(((performance.now() - t0) * speed) / STEP_MS);
    if (due > done) {
      done = due;
      const target = (i0 + due) % tl.stops.length;
      const stop = tl.stops[target];
      const started = performance.now();
      if (state.model.show && state.frames.v && stop && isProcessed(stop.name)) await getFrame(stop.name);
      if (!live()) return;
      if (state.model.show && state.frames.v) prefetch(aheadOf(target));
      await setStop(target, false);
      tl.lastStepMs = performance.now() - started;
    }
    // wake for the next capture, or the next frame if one is already due
    const nextAt = t0 + ((done + 1) * STEP_MS) / speed;
    await sleep(Math.max(0, nextAt - performance.now()));
  }
}

async function playLoop(token) {
  const tl = state.timeline;
  const live = () => tl.playing && tl.playToken === token;
  // switching Google's layer on or off mid-play moves between the two
  while (live()) {
    if (state.traffic.show) await stepLoop(token, live);
    else await clockLoop(token, live);
  }
}

export function startPlay() {
  const tl = state.timeline;
  if (tl.playing || tl.stops.length < 2) return;
  tl.playing = true;
  tl.playToken = (tl.playToken || 0) + 1;
  closePopup();
  $("playIcon").innerHTML = '<path d="M4.5 3h2.6v10H4.5zM8.9 3h2.6v10H8.9z" fill="currentColor"/>';
  $("playBtn").title = "Pause (space)";
  if (state.model.show && state.frames.v) prefetch(aheadOf(tl.index));
  playLoop(tl.playToken);
}

export function stopPlay() {
  const tl = state.timeline;
  tl.playing = false;
  tl.playToken = (tl.playToken || 0) + 1;       // any loop still waiting sees it is over
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

  track.addEventListener("pointerdown", (e) => {
    dragging = true;
    try { track.setPointerCapture(e.pointerId); } catch (err) { /* synthetic events */ }
    // clicking a mark opens its popup; anywhere else just moves the head
    const mark = e.target && e.target.closest && e.target.closest("#trackMarks i");
    if (mark) {
      const i = Number(mark.dataset.i);
      setStop(i, true);
      openPopup(i, mark);
    } else {
      closePopup();
      setAt(toFraction(e), true);
    }
  });
  track.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    closePopup();
    setAt(toFraction(e), true);
  });
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

  // the popup closes on Escape, on a click anywhere else, and when the window resizes
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePopup(); });
  document.addEventListener("pointerdown", (e) => {
    if (!popup.open) return;
    const t = e.target;
    if (t && t.closest && (t.closest("#stopPop") || t.closest("#trackMarks i"))) return;
    closePopup();
  }, true);
  window.addEventListener("resize", closePopup);

  const on = (id, fn) => { const el = $(id); if (el) el.onclick = fn; };
  on("tlZoomIn", () => zoomAt(0.6, 0.5));
  on("tlZoomOut", () => zoomAt(1 / 0.6, 0.5));
  on("tlZoomAll", zoomReset);
}

setCapturesChangedHook(buildTimeline);
