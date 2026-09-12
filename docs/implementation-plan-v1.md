> **Superseded on 2026-09-12 by [`project-plan.md`](project-plan.md).** Kept for history: it describes the browser/API-key collector design that was replaced by keyless direct tile capture.

# TRACK Implementation Plan

*Data collection and data preparation for the Dhaka traffic optimization project.*
*Written 2026-09-08. Supersedes the collector design in `track-data-collector/README.md` and `SETUP.md`.*

---

## 0. Status

| Phase | State (2026-09-08) |
|---|---|
| Phase 0, build without key | **Done, hardened.** Both capture modes, uploader, daily check, workflows, day-0 harness and download tool built and passed in mock mode. Robustness pass added: 17-test self-test gate, memory guard, preflight, per-tile audit, georef and file verification, deadline, salvage on exception, watchdog, quota guard, atomic verified upload, duplicate-slot guard, daily gap/liveness/size checks. See README "Failsafes". |
| Phase 1, day 0 | Waiting for the Google key. Command: `python tools/day0_test.py --page live` |
| Phase 2, deploy | Waiting for owner setup (SETUP.md). |
| Phase 3, graph + decoder | Not started. |

## 1. Where we are

- Concept, pitch and syllabus mapping are done (`idea.md`, `presentation.md`). Proposal accepted.
- Two collectors were built and neither has produced real data:
  - `track-data-collector/collector/` + GitHub workflows (Aug 4): TomTom points, Google Routes corridors, one zoom-12 screenshot. Never deployed.
  - `track-data-collector/server/` (Sep 5): local always-on control center that tiles Dhaka into zoom-17 chunks with Playwright. Tested only with mock images.
- Nothing downstream exists: no road graph, no colour decoder, no models, no engine, no visualizer.

## 2. Decisions taken (and why)

| # | Decision | Reason |
|---|---|---|
| 1 | **Google Maps is the only traffic source.** | TomTom and HERE list no Bangladesh coverage. Google exposes no traffic API; the visual traffic layer is the only per-road signal. |
| 2 | **Capture the traffic layer only, with all base-map features hidden.** | Clean colours, tiny files, exact georeferencing. |
| 3 | **First live test = direct capture of the traffic tile PNGs the page downloads.** Screenshots are the fallback. | Tiles are transparent PNGs whose URL carries zoom/column/row, so position is exact, lossless, no settle time, no pan drift, no stitching seams. |
| 4 | **Host on GitHub Actions (public repo), not Render, not a local machine.** | Free unlimited minutes, 4 vCPU / 16 GB runners. Render free tier is 512 MB / 0.1 CPU, sleeps after 15 min, ephemeral disk. |
| 5 | **Store captures in a private Hugging Face dataset repo.** | 100 GB private storage free, no card, one-command download at any time. Nothing sits on the user's drive during the run. |
| 6 | **Cadence 15 min, window 7 days.** | Matches the 15-min interval of the published Dhaka datasets. 672 cycles. |
| 7 | **One map load per cycle.** The page loads once and pans. | Google bills per map load; pans, zooms and layer switches are free. 672 loads per week against 10,000 free per month. |
| 8 | **Remove TomTom, Google Routes, the local scheduler, dashboard and SQLite.** | Dead sources or a process model that does not fit one-shot cloud runs. Grid math, browser engine, blank detection and retries are kept. |
| 9 | **Images are an intermediate.** They are decoded once into a per-edge table on our own OpenStreetMap graph. Models, simulation and visuals use only the graph and the table. | Google pixels never appear in the demo or the models' inputs. |
| 10 | **Google Maps Platform terms risk accepted by the project owner** (academic, non-commercial use). Mitigations still applied: dedicated Google project, private storage, minimal volume, quota caps. | Owner's decision, recorded here. |

## 3. Architecture

```
GitHub Actions (cron every 15 min)
  └─ run_cycle.py
       ├─ Playwright loads collector/map.html once (traffic layer only)
       ├─ pans across the Dhaka bbox on a fixed grid
       ├─ MODE tiles      : saves every traffic tile PNG the page fetches (z/x/y from URL)
       │  MODE screenshot : screenshots each grid position (fallback)
       ├─ completeness check against the expected tile set, re-pan for gaps
       ├─ mosaics tiles into 16x16-tile blocks, palette PNG
       ├─ writes manifest.json (bounds, zoom, hashes, timings)
       └─ uploads the cycle folder to the private Hugging Face dataset

Later, on any machine:
  download.py  ──►  local copy of all cycles
  build_graph.py ─►  OSM drive network for the same bbox (nodes, directed edges, capacity, free-flow)
  decode.py ───────►  per (slot, directed edge) congestion class table  ==  our raw dataset
                          │
                          ▼
             K-means / KNN / logistic regression / BPR / A* / assignment loop / visualizer
```

## 4. Phase 0: build everything that needs no API key

### 4.1 Repository layout after the rewrite

```
track-data-collector/
  .github/workflows/
    collect.yml          cron 7,22,37,52 * * * *  ->  run_cycle.py + upload
    daily-check.yml      07:00 Dhaka: count yesterday's cycles on HF, fail (email) if low
  collector/
    config.json          bbox, zoom, viewport, grid stride, cadence, thresholds, mode
    map.html             Maps JS API page: base map fully hidden, TrafficLayer on,
                         __panTo / __getCenter / __mapIdle contract, idle-fallback timer
    grid.py              grid math (from server/capture.py compute_grid) + slippy tile math
    browser.py           Playwright engine (from server/capture.py BrowserEngine)
                         + response listener that saves traffic tiles
    tiles.py             tile set expected for bbox, completeness check, mosaic builder
    analyze.py           blank detection, colour-pixel fraction (from analyze_image)
    run_cycle.py         one-shot orchestrator: capture -> verify -> package -> manifest
    upload.py            push cycle folder to HF (huggingface_hub.upload_folder)
    mock_map.html        fake page implementing the same contract for local tests
  tools/
    day0_test.py         live test harness (section 5)
    download.py          snapshot_download of the dataset to a chosen folder
  requirements.txt       playwright, Pillow, numpy, huggingface_hub
  README.md              rewritten for the new design
```

Deleted: `collector/tomtom.py`, `collector/google_routes.py`, `collector/report.py`, `collector/screenshot.py`, `server/`, `dryrun/`, the old `data/` placeholders. The `data-local/` folder stays ignored and holds only `secrets.json` for local runs.

### 4.2 Capture modes

**tiles (primary).** Playwright's response hook records every image response whose URL is a Google traffic tile. The zoom, column and row are parsed from the URL. Each tile is a transparent PNG. Tiles are written to a temp folder keyed by `z/x/y`, duplicates are ignored. After the pan sweep, the set of received tiles is compared with the expected set for the bbox at that zoom. Missing tiles trigger a targeted re-pan (up to `retries` times). The cycle is `ok` when coverage is 100 percent, `partial` above `min_coverage_pct`, else `failed`.

**screenshot (fallback).** The existing chunk approach: pan to each grid centre, wait for idle plus settle, screenshot, blank-detect, retry. Kept fully working so the switch is one config value.

Both modes share: one page load per cycle, the same pan grid, the same manifest schema.

### 4.3 Map page

- Styles hide every feature type (`all` / `visibility: off`). TrafficLayer added on top.
- `__panTo(lat, lon)` sets `__mapIdle=false`, registers a one-time `idle` listener, and **also starts a fallback timer** (`settle_s + 3 s`) that sets `__mapIdle=true` if `idle` never fires. This fixes the retry hang when re-panning to an unchanged centre.
- `__getCenter()` for drift measurement (screenshot mode only).
- Query parameters: `key, lat, lon, zoom, settle`. If Google rejects embedded JSON styles at any point, the fallback is a Map ID with a cloud style that hides everything; the page then passes `mapId` instead of `styles`.

### 4.4 Output format and manifest

Per cycle, folder `cycles/YYYY-MM-DD/HHMM/` containing:

```
manifest.json
blocks/z17_x<X0>_y<Y0>.png      16x16 tiles = 4096x4096 px, palette PNG (tiles mode)
chunks/r<R>c<C>.png              palette PNG per grid position    (screenshot mode)
```

`manifest.json`:

```json
{
  "slot_utc": "2026-09-10T04:07:00Z", "captured_utc": "2026-09-10T04:08:41Z",
  "mode": "tiles", "zoom": 17, "tile_px": 256,
  "bbox": {"north": 23.90, "south": 23.69, "east": 90.47, "west": 90.32},
  "tile_range": {"x0": 98411, "x1": 98466, "y0": 56720, "y1": 56803},
  "expected_tiles": 4704, "received_tiles": 4704, "coverage_pct": 100.0,
  "blocks": [{"file": "blocks/z17_x98400_y56720.png", "x0": 98400, "y0": 56720, "sha256": "..."}],
  "map_loads": 1, "duration_s": 212.4, "runner": "github", "collector_version": "2.0.0"
}
```

Tile-to-coordinate mapping is the standard Web Mercator slippy-map formula, so any pixel in any block converts to latitude and longitude exactly. No other georeferencing is needed.

Palette: the PNG is quantised to a fixed palette (transparent, green, orange, red, dark red, plus a few outline shades measured on day 0). Colour classification later is a lookup, not a threshold search.

### 4.5 Uploader

`upload.py` calls `huggingface_hub.upload_folder` on the cycle folder with commit message `cycle <slot>`. One commit per cycle, about 672 for the week, well under the few-thousand mark where Hugging Face repos degrade. Repo is private. Files per week stay under 20,000.

### 4.6 Workflows

`collect.yml`

- `schedule: "7,22,37,52 * * * *"` (offset from the top of the hour, which GitHub documents as its congested time) plus `workflow_dispatch`.
- Concurrency group so runs never overlap; `timeout-minutes: 13`.
- Steps: checkout, setup-python with pip cache, restore Playwright browser cache, `run_cycle.py`, `upload.py`. Failure of either step fails the job, which triggers GitHub's failure email.
- Secrets: `GOOGLE_MAPS_KEY`, `HF_TOKEN`, `HF_REPO`.

`daily-check.yml`

- 01:00 UTC (07:00 Dhaka). Lists yesterday's cycle folders in the HF repo, fails if fewer than `daily_min_cycles` (default 80 of 96) or if more than `max_partial` were partial. Failure = email.

### 4.7 Local mock testing (done before any key exists)

- `mock_map.html` draws a fake city and, in tiles mode, serves fake tile PNGs from a data URL pattern the response hook recognises, so the full path (pan sweep, hook, completeness check, mosaic, manifest, upload to a throwaway HF repo) is exercised end to end.
- `run_cycle.py --mode tiles --page mock` and `--mode screenshot --page mock` must both produce a valid cycle folder.

## 5. Phase 1: Day 0 live test (needs the Google key)

Run locally with `tools/day0_test.py`. Test A decides the capture mode. Tests B and C decide zoom and sizes.

### Test A: direct traffic-tile capture

1. Load `map.html` with the key at zoom 17 over Kakrail / Ramna / Paltan (the area already verified to need zoom 17 on the consumer site).
2. Log every image response URL and content type. Identify the traffic tiles: transparent PNGs containing the traffic palette colours, URL containing zoom/x/y parameters.
3. Save 20 tiles. Check each is a transparent PNG, record its pixel size (256 or 512) and whether the traffic palette matches the expected colours.
4. Convert three tile corners to lat/lon with the slippy formula and place them on OpenStreetMap. The lines must sit on the right roads within a few metres.
5. Pan across a 3x3 grid, count unique tiles received, compare with the expected set. Re-pan for gaps. Coverage must reach 100 percent.
6. Confirm the console reports exactly one map load for the whole test.

**Pass** = steps 3 to 6 all hold. Tiles mode becomes the production mode. **Fail** = tiles are not fetched as plain PNGs, or their coordinates cannot be recovered from the URL. Then screenshot mode is production and Test B applies.

### Test B: screenshot fallback

Same area, 2x2 grid at viewport 4096, styles hiding the base map. Check blank detection, retry path with the new fallback timer, drift under 5 m, colour palette measured.

### Test C: zoom and size

Capture the same area at zoom 16 and zoom 17 in the chosen mode. Record:

| Measure | Zoom 16 | Zoom 17 |
|---|---|---|
| Tiles or chunks per full-bbox cycle | | |
| Minor roads carrying traffic colour (visual check at 5 junctions) | | |
| Bytes per cycle after palette PNG | | |
| Seconds per cycle | | |

Rule: pick the lowest zoom at which the inner roads we will model show traffic colour. If zoom 16 loses them, zoom 17 is final. If both show them, zoom 16 halves the data.

### Outputs of day 0

- `config.json` finalised: mode, zoom, viewport, stride, settle.
- Measured palette written into `analyze.py`.
- A one-page note in `track-data-collector/DAY0.md` with the numbers from the tables above.

## 6. Phase 2: deploy and run the 7-day window

### 6.1 One-time setup by the project owner

1. **Google Cloud.** New project, billing attached, enable only the Maps JavaScript API. API key restricted to that API. Quotas: cap map loads at 300 per day. Budget alert at $1. Key goes into `track-data-collector/data-local/secrets.json` locally (ignored by git) and into the GitHub secret.
2. **Hugging Face.** Free account, private dataset repo (for example `<user>/track-dhaka-traffic`), write token.
3. **GitHub.** Public repository, push `track-data-collector/`, add secrets `GOOGLE_MAPS_KEY`, `HF_TOKEN`, `HF_REPO`.
4. **Email the authors** of Hasan & Sarker (VEHITS 2025) and Rahman & Naushin (2024) requesting their datasets for academic use.

### 6.2 First run

1. Actions tab, run `Collect traffic data` manually.
2. Confirm the HF repo shows `cycles/<today>/<HHMM>/manifest.json` with `coverage_pct` 100 and the expected number of blocks.
3. Open one block image, confirm coloured lines are present.
4. Run `Daily check` manually once; it is expected to fail until a full day exists.

### 6.3 During the week

- No action unless a failure email arrives.
- If a run fails: open the run log. A key or quota error means the Google console; a tile-coverage error means Google changed something and screenshot mode is switched on by editing one config value.
- Expect a few percent of slots delayed or skipped by GitHub's scheduler. The manifest records the true capture time; downstream binning uses the slot with a tolerance.

### 6.4 End of the window

1. Disable `collect.yml` in the Actions tab.
2. `python tools/download.py --to <any drive>` pulls every cycle. The HF copy stays as backup.

### 6.5 Guards and budgets

| Guard | Value |
|---|---|
| Map loads per cycle | 1 |
| Map loads per week | 672 (free allowance 10,000 per month) |
| Console daily cap on map loads | 300 |
| Budget alert | $1 |
| Runner time per run (target) | under 6 min |
| Runner time per day | about 9 h, free on a public repo |
| Storage per week (palette PNG, tiles mode) | expected 1 to 3 GB, measured on day 0 |
| HF private storage | 100 GB |

## 7. Phase 3 (in parallel with the window): graph and decoder

### 7.1 `build_graph.py`

- Pull the drive network for the same bbox from OpenStreetMap (osmnx, or Overpass + networkx).
- Directed edges with: `edge_id, u, v, geometry (LineString), length_m, highway tag, lanes, oneway, maxspeed`.
- Derived: `capacity_vph` from a lookup by highway class and lanes; `free_flow_kmph` from maxspeed or class default; `free_flow_s = length / speed`.
- Saved as GraphML plus a GeoParquet of edges. This graph is the one the A* engine uses later, so node and edge ids are frozen here.

### 7.2 `decode.py`

For every cycle and every directed edge:

1. Densify the edge geometry to points every 3 m.
2. Offset each point to the **left of the travel direction** by `line_offset_px` (measured on day 0, likely 3 to 5 px at zoom 17). Bangladesh drives on the left, so the direction's traffic line is drawn on that side.
3. Convert each point to tile and pixel coordinates, read a 3x3 window from the block image, classify by palette lookup.
4. Aggregate along the edge: fraction of sampled points per class, dominant class, `coverage` (fraction of points with any traffic colour).
5. Write one row per `(slot_utc, edge_id)`:

```
slot_utc, edge_id, cls (0 none, 1 green, 2 orange, 3 red, 4 darkred), 
f_green, f_orange, f_red, f_darkred, coverage, vc_ratio (0.3 / 0.6 / 1.0 / 1.2 by class)
```

Output: Parquet, partitioned by day. This table is the dataset for K-means, KNN, logistic regression and the BPR initial volumes.

### 7.3 Validation on the first real day

- Render decoded classes as coloured edges over OpenStreetMap for five junctions and compare side by side with the block image. Misalignment shows up immediately.
- Coverage report: percentage of edges with data by highway class and by hour. Decides which edge classes enter the model and whether zoom needs revisiting while the window is still running.
- Consistency: a corridor's class sequence across 15-min slots should be smooth; isolated flips flag decoding noise.

## 8. Phase 4: models, engine, visualizer (outline only)

Follows `idea.md` sections 4 and 5, all on the graph and table from Phase 3:

1. K-means on per-edge time profiles to name traffic scenarios.
2. KNN and logistic regression predicting class per edge and time slot; compared.
3. BPR costs from predicted volume/capacity, A* with a haversine heuristic, incremental assignment loop with synthetic OD demand from zone weights.
4. Visualizer draws our edges by simulated volume on a plain base map, before/after and animated.

Each of these gets its own short plan once the first real day of decoded data exists.

## 9. Risks and fallbacks

| Risk | Signal | Fallback |
|---|---|---|
| Traffic tiles not capturable as plain PNGs | Test A fails | Screenshot mode, already built |
| Embedded styles stop working | Base map visible in capture | Map ID with a cloud style hiding all features |
| Google rejects the key or suspends the project | HTTP errors in run log | New project and key; nothing in the pipeline depends on the account |
| GitHub schedule delays or drops runs | Gaps in daily check | Accept up to 20 percent; the model bins by slot |
| Runs exceed the 13-minute timeout | Job cancelled | Lower zoom, larger viewport, fewer pan positions |
| Hugging Face rate limits on commits | Upload step errors | Batch two cycles per commit |
| Palette differs from expectation | Day 0 colour histogram | Palette is measured, not assumed |
| Inner roads absent at chosen zoom | Coverage report by highway class | Raise zoom mid-window; manifests carry zoom per cycle |
| Authors reply with datasets | Email | Our week becomes validation; historical modelling uses theirs |

## 10. Timeline

| Day | Work |
|---|---|
| 0 | Phase 0 build and mock tests (no key needed). Owner does setup in section 6.1. |
| Key arrives | Day 0 live test, config finalised, `DAY0.md` written. Push, first manual run, verify on HF. |
| 1 to 7 | Window runs. Build graph and decoder. Validate on day 1 data. Coverage report by day 3. |
| 7 | Disable workflow, download dataset. |
| 8 onward | Phase 4 plans, one per component. |
