/* The week track along the bottom, and the two timelines it can show.

   A capture belongs to one of two series. `scheduled` is the unbroken run the
   collection workflow takes every 10 minutes, which is what the research
   rests on. `test` is everything taken by hand. They are shown separately so
   a test never reads as a spike in the real sequence, and so a gap in the
   scheduled run is visible as exactly that.

   Play walks the captures, not the clock. Stepping through 672 slots to find
   the handful that hold data made the button look broken on a sparse
   timeline; now it hops capture to capture, and the readout says how far
   apart they were. */

import { $, clamp, state } from "./core.js";
import { fillCaptureSelect, selectCapture, setCapturesChangedHook } from "./captures.js";

const SLOT_MS = 15 * 60 * 1000;
const DHAKA_OFFSET = 6 * 3600 * 1000;          // captures are stamped UTC, Dhaka is UTC+6
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const STEP_MS = 900;                           // one capture per this long at 1x
const SCHEDULED_GAP_MIN = 25;                  // longer than this between scheduled captures is a gap

/* Captures in the series now showing, oldest first. */
export function seriesCaptures(series = state.timeline.series) {
  return state.captures
    .filter((c) => (c.series || "test") === series && !isNaN(Date.parse(c.captured_utc)))
    .sort((a, b) => Date.parse(a.captured_utc) - Date.parse(b.captured_utc));
}

export function buildTimeline() {
  const tl = state.timeline;
  const mine = seriesCaptures();

  // the week containing this series' newest capture
  const anchor = mine.length ? Date.parse(mine[mine.length - 1].captured_utc) : Date.now();
  const d = new Date(anchor + DHAKA_OFFSET);
  const dow = (d.getUTCDay() + 6) % 7;
  const monday = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) - dow * 86400000;
  tl.start = monday - DHAKA_OFFSET;

  tl.available.clear();
  tl.order = [];
  tl.outside = 0;
  for (const c of mine) {
    const idx = Math.round((Date.parse(c.captured_utc) - tl.start) / SLOT_MS);
    if (idx >= 0 && idx < tl.slots) {
      tl.available.set(idx, c.name);
      tl.order.push(idx);
    } else {
      // the track covers the week around this series' newest capture, so
      // anything older than that week has nowhere to sit. Counted, not
      // dropped silently: the readout says how many are out of view.
      tl.outside += 1;
    }
  }
  tl.order.sort((a, b) => a - b);

  // stay on the capture already showing if it is in this series, else go to
  // the newest one, else leave the head where the data would start
  const here = tl.order.find((i) => tl.available.get(i) === (state.capture && state.capture.name));
  tl.index = here ?? (tl.order.length ? tl.order[tl.order.length - 1] : 0);

  renderTrack();
  renderTimeline();
  if (tl.order.length && here === undefined) {
    const name = tl.available.get(tl.index);
    if (name && name !== (state.capture && state.capture.name)) selectCapture(name, false);
  }
}

/* The marks, and the band showing the stretch the series actually covers. A
   run of one morning should not look like a week that failed to load. */
function renderTrack() {
  const tl = state.timeline;
  $("trackDays").innerHTML = DAYS.map((x) => `<div>${x}</div>`).join("");

  const pct = (i) => (i / (tl.slots - 1)) * 100;
  const gaps = gapSlots();
  $("trackMarks").innerHTML = tl.order
    .map((i) => `<i class="${gaps.has(i) ? "gap" : ""}" style="left:${pct(i)}%"></i>`)
    .join("");

  const span = $("trackSpan");
  if (tl.order.length > 1) {
    const a = pct(tl.order[0]);
    span.style.left = `${a}%`;
    span.style.width = `${pct(tl.order[tl.order.length - 1]) - a}%`;
    span.hidden = false;
  } else {
    span.hidden = true;
  }
}

/* Slots where the scheduled sequence skipped a beat. Only meaningful for the
   scheduled series: captures taken by hand are not supposed to be regular. */
function gapSlots() {
  const tl = state.timeline;
  const out = new Set();
  if (tl.series !== "scheduled") return out;
  for (let k = 1; k < tl.order.length; k++) {
    const minutes = (tl.order[k] - tl.order[k - 1]) * (SLOT_MS / 60000);
    if (minutes > SCHEDULED_GAP_MIN) out.add(tl.order[k]);
  }
  return out;
}

export function slotDate(i) {
  return new Date(state.timeline.start + i * SLOT_MS + DHAKA_OFFSET);
}

export function renderTimeline() {
  const tl = state.timeline;
  $("trackHead").style.left = `${(tl.index / (tl.slots - 1)) * 100}%`;
  const d = slotDate(tl.index);
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  $("slotLabel").textContent = `${DAYS[(d.getUTCDay() + 6) % 7]} ${d.getUTCDate()} · ${hh}:${mm}`;

  document.querySelectorAll("#trackMarks i").forEach((el, n) => {
    el.classList.toggle("here", tl.order[n] === tl.index);
  });

  const st = $("slotState");
  const n = tl.order.length;
  if (tl.available.has(tl.index)) {
    const at = tl.order.indexOf(tl.index) + 1;
    st.textContent = `capture ${at} of ${n}`;
    st.className = "live";
  } else if (!n) {
    st.textContent = tl.series === "scheduled" ? "no scheduled captures yet" : "no test captures";
    st.className = "";
  } else {
    st.textContent = `${n} capture${n === 1 ? "" : "s"} in this timeline`;
    st.className = "";
  }

  const count = $("seriesCount");
  if (count) {
    const other = tl.series === "scheduled" ? "test" : "scheduled";
    const mine = seriesCaptures().length;
    const theirs = seriesCaptures(other).length;
    // `mine` counts the series; tl.order counts what fits on this week's
    // track. Saying only one of them would be a number that does not match
    // the marks under it.
    const shown = tl.outside
      ? `${tl.order.length} of ${mine} ${tl.series} this week`
      : `${mine} ${tl.series}`;
    count.textContent = `${shown}${theirs ? ` · ${theirs} ${other}` : ""}`;
  }
}

/* Move the head. `snap` lands on the nearest capture instead of the raw slot,
   which is what dragging wants: the slots between captures hold nothing. */
export function setSlot(i, fromUser, snap = false) {
  const tl = state.timeline;
  if (!Number.isFinite(i)) return;
  let target = clamp(Math.round(i), 0, tl.slots - 1);
  if (snap && tl.order.length) {
    target = tl.order.reduce((best, s) =>
      Math.abs(s - target) < Math.abs(best - target) ? s : best, tl.order[0]);
  }
  tl.index = target;
  const name = tl.available.get(tl.index);
  if (name && name !== (state.capture && state.capture.name)) selectCapture(name, false);
  renderTimeline();
  if (fromUser && tl.playing) stopPlay();
}

/** Step to the next or previous capture, wrapping at the ends. */
export function stepCapture(dir) {
  const tl = state.timeline;
  if (!tl.order.length) return;
  const at = tl.order.indexOf(tl.index);
  const next = at < 0
    ? (dir > 0 ? tl.order[0] : tl.order[tl.order.length - 1])
    : tl.order[(at + dir + tl.order.length) % tl.order.length];
  setSlot(next, false);
}

export function setSeries(series) {
  const tl = state.timeline;
  if (series !== "scheduled" && series !== "test") return;
  if (tl.series === series) return;
  stopPlay();
  tl.series = series;
  document.querySelectorAll("#series button")
    .forEach((b) => b.classList.toggle("on", b.dataset.series === series));
  fillCaptureSelect();          // the picker follows the timeline
  buildTimeline();
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
  if (tl.playing || tl.order.length < 2) return;
  tl.playing = true;
  $("playIcon").innerHTML = '<path d="M4.5 3h2.6v10H4.5zM8.9 3h2.6v10H8.9z" fill="currentColor"/>';
  $("playBtn").title = "Pause (space)";
  tl.timer = setInterval(() => stepCapture(1), STEP_MS / tl.speed);
}

export function stopPlay() {
  const tl = state.timeline;
  tl.playing = false;
  clearInterval(tl.timer);
  $("playIcon").innerHTML = '<path d="M5 3.2l7.2 4.8-7.2 4.8z" fill="currentColor"/>';
  $("playBtn").title = "Play (space)";
}

export const togglePlay = () => (state.timeline.playing ? stopPlay() : startPlay());

export function bindTrack() {
  const track = $("track");
  const toSlot = (ev, snap) => {
    const r = track.getBoundingClientRect();
    if (r.width > 0) {
      setSlot(((ev.clientX - r.left) / r.width) * (state.timeline.slots - 1), true, snap);
    }
  };
  let dragging = false;
  track.addEventListener("pointerdown", (e) => {
    dragging = true;
    try { track.setPointerCapture(e.pointerId); } catch (err) { /* synthetic events */ }
    toSlot(e, true);
  });
  // free while dragging so the head follows the finger, snapping when released
  track.addEventListener("pointermove", (e) => { if (dragging) toSlot(e, false); });
  const end = (e) => {
    if (dragging) toSlot(e, true);
    dragging = false;
    try { track.releasePointerCapture(e.pointerId); } catch (err) { /* already released */ }
  };
  track.addEventListener("pointerup", end);
  track.addEventListener("pointercancel", end);
  // the wheel moves between captures, which is what there is to look at
  track.addEventListener("wheel", (e) => {
    e.preventDefault();
    if (state.timeline.playing) stopPlay();
    stepCapture(Math.sign(e.deltaY || e.deltaX) || 1);
  }, { passive: false });
}

setCapturesChangedHook(buildTimeline);
