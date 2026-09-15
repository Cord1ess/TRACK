/* The week track along the bottom.

   A week of 15-minute slots. Only slots with a real capture carry data; the
   rest are the shape the weekly collection will fill in. Dragging, the wheel
   and shift+arrows all move the same way, through setSlot. */

import { $, clamp, state } from "./core.js";
import { selectCapture, setCapturesChangedHook } from "./captures.js";

const SLOT_MS = 15 * 60 * 1000;
const DHAKA_OFFSET = 6 * 3600 * 1000;          // captures are stamped UTC, Dhaka is UTC+6
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function buildTimeline() {
  const tl = state.timeline;
  const stamps = state.captures.map((c) => Date.parse(c.captured_utc)).filter((t) => !isNaN(t));
  const anchor = stamps.length ? Math.max(...stamps) : Date.now();
  const d = new Date(anchor + DHAKA_OFFSET);
  const dow = (d.getUTCDay() + 6) % 7;
  const monday = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) - dow * 86400000;
  tl.start = monday - DHAKA_OFFSET;

  tl.available.clear();
  for (const c of state.captures) {
    const t = Date.parse(c.captured_utc);
    if (isNaN(t)) continue;
    const idx = Math.round((t - tl.start) / SLOT_MS);
    if (idx >= 0 && idx < tl.slots) tl.available.set(idx, c.name);
  }
  const here = [...tl.available.entries()].find(([, n]) => n === (state.capture && state.capture.name));
  const newest = [...tl.available.keys()].sort((a, b) => b - a)[0];
  tl.index = here ? here[0] : (newest ?? Math.floor(tl.slots / 2));

  $("trackDays").innerHTML = DAYS.map((x) => `<div>${x}</div>`).join("");
  $("trackMarks").innerHTML = [...tl.available.keys()]
    .map((i) => `<i style="left:${(i / (tl.slots - 1)) * 100}%"></i>`).join("");
  renderTimeline();
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
  const st = $("slotState");
  if (tl.available.has(tl.index)) { st.textContent = "capture loaded"; st.className = "live"; }
  else { st.textContent = `${tl.available.size} of ${tl.slots} slots collected`; st.className = ""; }
}

export function setSlot(i, fromUser) {
  const tl = state.timeline;
  if (!Number.isFinite(i)) return;
  tl.index = clamp(Math.round(i), 0, tl.slots - 1);
  const name = tl.available.get(tl.index);
  if (name && name !== (state.capture && state.capture.name)) selectCapture(name, false);
  renderTimeline();
  if (fromUser && tl.playing) stopPlay();
}

export function startPlay() {
  const tl = state.timeline;
  if (tl.playing) return;
  tl.playing = true;
  $("playIcon").innerHTML = '<path d="M4.5 3h2.6v10H4.5zM8.9 3h2.6v10H8.9z" fill="currentColor"/>';
  $("playBtn").title = "Pause (space)";
  tl.timer = setInterval(() => setSlot((tl.index + 1) % tl.slots), 120);
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
  const toSlot = (ev) => {
    const r = track.getBoundingClientRect();
    if (r.width > 0) setSlot(((ev.clientX - r.left) / r.width) * (state.timeline.slots - 1), true);
  };
  let dragging = false;
  track.addEventListener("pointerdown", (e) => {
    dragging = true;
    try { track.setPointerCapture(e.pointerId); } catch (err) { /* synthetic events */ }
    toSlot(e);
  });
  track.addEventListener("pointermove", (e) => { if (dragging) toSlot(e); });
  const end = (e) => {
    dragging = false;
    try { track.releasePointerCapture(e.pointerId); } catch (err) { /* already released */ }
  };
  track.addEventListener("pointerup", end);
  track.addEventListener("pointercancel", end);
  track.addEventListener("wheel", (e) => {
    e.preventDefault();
    setSlot(state.timeline.index + Math.sign(e.deltaY || e.deltaX), true);
  }, { passive: false });
}

setCapturesChangedHook(buildTimeline);
