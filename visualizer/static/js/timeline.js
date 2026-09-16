/* The week track along the bottom, and the two timelines it can show.

   A capture belongs to one of two series. `scheduled` is the unbroken run the
   collection workflow takes; `test` is everything taken by hand. They are
   shown separately so a test never reads as a spike in the real sequence, and
   so a gap in the scheduled run is visible as exactly that.

   The track indexes CAPTURES, not clock slots. It used to quantise the week
   into 672 fifteen-minute slots, which worked while captures were 20 minutes
   apart and broke the moment they were not: at one capture every 6 minutes,
   twelve of them collapsed into five slots, seven were unreachable, and play
   froze because stepping from a slot landed back on the same slot. Now every
   capture is its own stop and its position on the track comes from its
   timestamp, so two captures four minutes apart are still two places to be. */

import { $, clamp, state } from "./core.js";
import { fillCaptureSelect, selectCapture, setCapturesChangedHook } from "./captures.js";

const WEEK_MS = 7 * 24 * 3600 * 1000;
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

  // one stop per capture that falls inside the week, in time order
  tl.stops = [];
  tl.outside = 0;
  for (const c of mine) {
    const t = Date.parse(c.captured_utc);
    const at = (t - tl.start) / WEEK_MS;        // 0..1 across the track
    if (at >= 0 && at < 1) tl.stops.push({ name: c.name, t, at });
    else tl.outside += 1;                       // older than the week the track draws
  }

  // stay on the capture already showing if it is in this series, else the newest
  const here = tl.stops.findIndex((s) => s.name === (state.capture && state.capture.name));
  tl.index = here >= 0 ? here : Math.max(0, tl.stops.length - 1);

  renderTrack();
  renderTimeline();
  if (tl.stops.length && here < 0) {
    const name = tl.stops[tl.index].name;
    if (name !== (state.capture && state.capture.name)) selectCapture(name, false);
  }
}

/* The marks, and the band showing the stretch the series actually covers. A
   run of one morning should not look like a week that failed to load. */
function renderTrack() {
  const tl = state.timeline;
  $("trackDays").innerHTML = DAYS.map((x) => `<div>${x}</div>`).join("");

  const gaps = gapStops();
  $("trackMarks").innerHTML = tl.stops
    .map((s, i) => `<i class="${gaps.has(i) ? "gap" : ""}" style="left:${s.at * 100}%"></i>`)
    .join("");

  const span = $("trackSpan");
  if (tl.stops.length > 1) {
    const a = tl.stops[0].at * 100;
    span.style.left = `${a}%`;
    span.style.width = `${tl.stops[tl.stops.length - 1].at * 100 - a}%`;
    span.hidden = false;
  } else {
    span.hidden = true;
  }
}

/* Stops where the scheduled sequence skipped a beat. Only meaningful for the
   scheduled series: captures taken by hand are not supposed to be regular. */
function gapStops() {
  const tl = state.timeline;
  const out = new Set();
  if (tl.series !== "scheduled") return out;
  for (let k = 1; k < tl.stops.length; k++) {
    if ((tl.stops[k].t - tl.stops[k - 1].t) / 60000 > SCHEDULED_GAP_MIN) out.add(k);
  }
  return out;
}

export function renderTimeline() {
  const tl = state.timeline;
  const stop = tl.stops[tl.index];
  $("trackHead").style.left = `${(stop ? stop.at : 0) * 100}%`;

  const d = new Date((stop ? stop.t : tl.start) + DHAKA_OFFSET);
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  $("slotLabel").textContent = `${DAYS[(d.getUTCDay() + 6) % 7]} ${d.getUTCDate()} · ${hh}:${mm}`;

  document.querySelectorAll("#trackMarks i").forEach((el, n) => {
    el.classList.toggle("here", n === tl.index);
  });

  const st = $("slotState");
  const n = tl.stops.length;
  if (!n) {
    st.textContent = tl.series === "scheduled" ? "no scheduled captures yet" : "no test captures";
    st.className = "";
  } else {
    st.textContent = `capture ${tl.index + 1} of ${n}`;
    st.className = "live";
  }

  const count = $("seriesCount");
  if (count) {
    const other = tl.series === "scheduled" ? "test" : "scheduled";
    const mine = seriesCaptures().length;
    const theirs = seriesCaptures(other).length;
    // `mine` counts the series; stops counts what fits on this week's track.
    // Saying only one of them would be a number that does not match the marks.
    const shown = tl.outside ? `${n} of ${mine} ${tl.series} this week` : `${mine} ${tl.series}`;
    count.textContent = `${shown}${theirs ? ` · ${theirs} ${other}` : ""}`;
  }
}

/** Go to a stop by its position in the list. */
export function setStop(i, fromUser) {
  const tl = state.timeline;
  if (!tl.stops.length || !Number.isFinite(i)) return;
  tl.index = clamp(Math.round(i), 0, tl.stops.length - 1);
  const name = tl.stops[tl.index].name;
  if (name !== (state.capture && state.capture.name)) selectCapture(name, false);
  renderTimeline();
  if (fromUser && tl.playing) stopPlay();
}

/** Go to whichever capture sits nearest a fraction across the week. Dragging
    wants this: the space between captures holds nothing to show. */
export function setAt(fraction, fromUser) {
  const tl = state.timeline;
  if (!tl.stops.length || !Number.isFinite(fraction)) return;
  const f = clamp(fraction, 0, 1);
  let best = 0;
  for (let i = 1; i < tl.stops.length; i++) {
    if (Math.abs(tl.stops[i].at - f) < Math.abs(tl.stops[best].at - f)) best = i;
  }
  setStop(best, fromUser);
}

/** Step to the next or previous capture, wrapping at the ends. */
export function stepCapture(dir) {
  const tl = state.timeline;
  if (!tl.stops.length) return;
  setStop((tl.index + dir + tl.stops.length) % tl.stops.length, false);
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
  if (tl.playing || tl.stops.length < 2) return;
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
  const toFraction = (ev) => {
    const r = track.getBoundingClientRect();
    return r.width > 0 ? (ev.clientX - r.left) / r.width : null;
  };
  let dragging = false;
  track.addEventListener("pointerdown", (e) => {
    dragging = true;
    try { track.setPointerCapture(e.pointerId); } catch (err) { /* synthetic events */ }
    setAt(toFraction(e), true);
  });
  track.addEventListener("pointermove", (e) => { if (dragging) setAt(toFraction(e), true); });
  const end = (e) => {
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
