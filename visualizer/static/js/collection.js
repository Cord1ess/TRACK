/* What collection is doing right now, across the top of the page.

   Two sources, both public and unauthenticated:

     runs API   is a run going, which number, when it started
     jobs API   which step that run is on, and which steps have finished

   The jobs request is only made while a run is actually in progress. GitHub
   allows 60 unauthenticated requests an hour from a browser, and the run poll
   already spends one every two minutes; asking for steps unconditionally
   would sit exactly on the limit and start being refused.

   Progress is measured, not guessed. Steps that have finished are counted at
   their real cost, and the step running is credited with however long it has
   been going, both against the medians build_site.py publishes from the
   manifests of captures actually taken. When the API says nothing, the bar
   falls back to elapsed time against the median run, and says so. */

import { $, clamp, state } from "./core.js";
import { STATIC, getManifest, loadManifest } from "./api.js";

export const RUN_POLL = 120000;

/* Workflow step names are written for the workflow file. These are for a
   person watching the map. A step not listed here is shown as-is. */
const STEP_WORDS = {
  "Set up job": "starting up",
  "Run actions/checkout@v4": "fetching the code",
  "Run actions/setup-python@v5": "setting up Python",
  "Install": "installing dependencies",
  "Restore the previous captures and site": "restoring the last run's files",
  "Restore the previous captures": "restoring the last run's files",
  "Capture": "downloading tiles from Google",
  "Decode, impute, layers": "reading the tiles and running the algorithms",
  "Build the site": "building the page",
  "Archive the capture to Hugging Face": "archiving the capture",
  "Keep the captures and site for the next run": "saving files for the next run",
  "Keep the captures for the next run": "saving files for the next run",
  "Run actions/configure-pages@v5": "preparing to publish",
  "Run actions/upload-pages-artifact@v3": "uploading the site",
  "Run actions/deploy-pages@v4": "publishing",
  "Start the next run": "starting the next run",
  "Complete job": "finishing",
};

/* Steps the workflow spends real time in. Setup and teardown are quick and
   would make the bar jump; they are counted, but the estimate leans on these. */
const HEAVY = ["Capture", "Decode, impute, layers", "Build the site"];

const plain = (name) => STEP_WORDS[name] || name;
const secsSince = (iso) => Math.max(0, (Date.now() - Date.parse(iso)) / 1000);

export function shortAge(ms) {
  const m = Math.round(ms / 60000);
  return m < 1 ? "just now" : m < 60 ? `${m} min ago` : `${Math.floor(m / 60)} h ${m % 60} min ago`;
}

function fmt(secs) {
  if (!Number.isFinite(secs) || secs < 0) return "";
  const s = Math.round(secs);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

/* How long a whole run takes, and how much of it each step is worth. The
   capture stage comes from real manifests; the rest is what the run itself
   last reported, so both improve as runs happen. */
function budget() {
  const m = STATIC ? getManifest() : null;
  const t = (m && m.timing) || {};
  const capture = t.capture_seconds || 75;
  const past = state.runs.stepCosts || {};
  const of = (name, fallback) => past[name] || fallback;
  const steps = [
    ["Capture", capture],
    ["Decode, impute, layers", of("Decode, impute, layers", 100)],
    ["Build the site", of("Build the site", 60)],
  ];
  const overhead = 40;                       // checkout, install, cache, deploy
  return { steps, total: steps.reduce((a, [, s]) => a + s, 0) + overhead, overhead };
}

/* Fraction of the run done, from the steps the API reported. */
function progressFromSteps(steps) {
  const b = budget();
  const cost = (name) => (b.steps.find(([n]) => n === name) || [, 6])[1];
  let done = 0, total = b.overhead;
  for (const [name, secs] of b.steps) total += secs;

  let running = null;
  for (const s of steps) {
    const c = HEAVY.includes(s.name) ? cost(s.name) : 6;
    if (s.status === "completed") {
      done += c;
    } else if (s.status === "in_progress") {
      running = s;
      const spent = s.started_at ? secsSince(s.started_at) : 0;
      done += Math.min(spent, c * 0.95);     // never let one step fill the bar
    }
  }
  return { frac: clamp(done / total, 0, 0.99), running, total, done };
}

/* The whole picture for one run, whether or not the steps are known. */
function describe(run, steps) {
  if (!run) return null;
  if (run.status === "completed") {
    const ok = run.conclusion === "success";
    return {
      busy: false, ok,
      title: `Collection #${run.run_number} ${run.conclusion || "ended"}`,
      detail: `finished ${shortAge(Date.now() - Date.parse(run.updated_at || run.created_at))}`,
      frac: 1,
    };
  }
  const started = run.run_started_at || run.created_at;
  const elapsed = secsSince(started);
  const b = budget();
  if (steps && steps.length) {
    const { frac, running } = progressFromSteps(steps);
    const left = Math.max(0, b.total - elapsed);
    return {
      busy: true, ok: true,
      title: `Collection #${run.run_number}`,
      detail: running
        ? `${plain(running.name)} · ${fmt(elapsed)} in${left > 5 ? ` · about ${fmt(left)} left` : ""}`
        : `${fmt(elapsed)} in`,
      frac,
    };
  }
  // no step detail: elapsed against the measured run, and say it is an estimate
  return {
    busy: true, ok: true,
    title: `Collection #${run.run_number}`,
    detail: `${fmt(elapsed)} in · about ${fmt(Math.max(0, b.total - elapsed))} left`,
    frac: clamp(elapsed / b.total, 0, 0.99),
  };
}

async function getJSON(url) {
  try {
    const r = await fetch(url, { headers: { Accept: "application/vnd.github+json" } });
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;                              // offline or rate limited
  }
}

export async function fetchRuns() {
  const m = STATIC ? (getManifest() || await loadManifest()) : null;
  if (!m || !m.runs_api) return;

  const data = await getJSON(`${m.runs_api}?per_page=6`);
  if (data) {
    const runs = data.workflow_runs || [];
    const pick = (name) => runs.find((x) => x.name === name) || null;
    state.runs = { ...state.runs, collect: pick("collect"), deploy: pick("deploy"), at: Date.now() };
  }

  // only while something is actually running, to stay inside the hourly limit
  const c = state.runs.collect;
  if (c && c.status !== "completed" && c.jobs_url) {
    const jobs = await getJSON(c.jobs_url);
    const job = jobs && (jobs.jobs || [])[0];
    state.runs.steps = (job && job.steps) || null;
    if (job && job.steps) {
      // remember what each step cost last time it completed, so the estimate
      // sharpens with every run instead of staying on the built-in guess
      const costs = { ...(state.runs.stepCosts || {}) };
      for (const s of job.steps) {
        if (s.status === "completed" && s.started_at && s.completed_at) {
          costs[s.name] = (Date.parse(s.completed_at) - Date.parse(s.started_at)) / 1000;
        }
      }
      state.runs.stepCosts = costs;
    }
  } else {
    state.runs.steps = null;
  }
  render();
}

export function render() {
  const el = $("collectBar");
  if (!el) return;
  const m = STATIC ? getManifest() : null;
  if (!m || !m.runs_api) { el.hidden = true; return; }

  const info = describe(state.runs.collect, state.runs.steps)
            || { busy: false, ok: true, title: "Collection", detail: "no run yet", frac: 0 };
  el.hidden = false;
  el.classList.toggle("busy", info.busy);
  el.classList.toggle("bad", info.ok === false);

  const pct = Math.round(info.frac * 100);
  $("collectFill").style.width = `${pct}%`;
  $("collectTitle").textContent = info.title;
  $("collectDetail").textContent = info.detail;
  el.title = `${info.title} — ${info.detail}`;
  const link = $("collectLink");
  if (link) {
    link.href = m.runs_url || "#";
    link.hidden = !m.runs_url;
  }
}

/* The bar counts up between polls: a two-minute gap with a frozen number
   looks stalled, and the elapsed part is knowable without asking anyone. */
export function startTicking() {
  setInterval(() => {
    if (document.hidden) return;
    const c = state.runs.collect;
    if (c && c.status !== "completed") render();
  }, 1000);
}
