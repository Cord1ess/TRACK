# TRACK Project Plan (v2)

*Restructure of 2026-09-12. Supersedes `implementation-plan-v1.md`, which is kept for history.*

TRACK learns where and when Dhaka gets congested from Google's traffic layer, then
shows the whole city being rebalanced by A\* inside a traffic-assignment loop
(see `idea.md`). The project is now three independent subsystems that talk to each
other only through files with fixed contracts.

```
TRACK/
  docs/               concept, pitch, plans
  data-collection/    keyless capture of Google traffic tiles -> captures/<name>/
  algorithms/         images -> data, road graph, A*, BPR, assignment, K-means, KNN, logreg
  visualizer/         dev server + map UI showing captures and algorithm outputs
```

## 1. Decisions that shape everything

| # | Decision | Why |
|---|---|---|
| 1 | **Keyless direct tile capture is the data source.** `www.google.com/maps/vt` serves the traffic layer as transparent 256 px PNG tiles with no key and no browser. | No card-backed API key is obtainable; Mapbox and every other provider lack Dhaka density. Verified: 4,928-tile metro-area capture, 100 % coverage, byte-identical re-fetches. |
| 2 | **Zoom 17 is the native maximum.** Zoom 18 renders no traffic; zoom 17 and 16 carry the same roads. 256 px is the only tile size served. | Measured. Residential lanes have no traffic at any zoom: a Google data limit shared by the paid API and the published Dhaka papers. |
| 3 | **Images are an intermediate.** The decoder turns tiles into a per-slot, per-directed-edge table on our own OpenStreetMap graph. Every model, the engine and every visual uses only the graph and that table. | Google pixels never appear in the demo or in model inputs. |
| 4 | **The visualizer serves tiles, not screenshots.** Captured tiles are served as a real slippy-map raster layer (native at z17, pooled at lower zooms), over CARTO vector basemaps in MapLibre GL. | Full resolution, no texture limits, no downscale artefacts, no keys. Replaces the standalone `overlay.html` previewer. |
| 5 | **Algorithms are written from scratch, readable first.** numpy only; each file is one algorithm with a docstring that explains it and a test that proves it. | They are the syllabus content of the project and will be read by faculty. |
| 6 | **Subsystems are decoupled by file contracts** (section 3). | Each folder can be worked on, tested and demoed alone. |
| 7 | **Terms risk accepted by the project owner** for academic, non-commercial use. Mitigations: polite rate, private data, no redistribution of tiles. | Owner's decision, recorded. |

## 2. Repository layout

```
data-collection/
  README.md
  requirements.txt
  config.json                 bbox, zoom, rate, palette, thresholds
  collector/
    grid.py                   Web Mercator / tile math (single source of truth)
    fetch.py                  tile URL, polite limiter, strict PNG validation, retrying fetch
    tiles.py                  coverage, per-tile audit, block mosaics
    analyze.py                palette classification, traffic fraction, quantise
    checks.py                 preflight + post-capture checks
    capture.py                CLI: one capture run -> captures/<name>/
    reaudit.py                recompute palette-dependent stats of an existing capture
  storage/                    optional remote copy (Hugging Face) for the daily system
    upload.py, download.py
  scheduler/                  daily capture system (next step, see section 5)
    README.md, daily_check.py
  tests/selftest.py           proves every component before a run
  captures/
    test-capture-2026-09-12/  first full-city capture (1 AM, sparse)

algorithms/
  README.md
  requirements.txt
  track_algos/
    geo.py                    lon/lat <-> tile/pixel (mirrors collector/grid.py)
    graph/
      road_graph.py           RoadGraph: nodes, directed edges, capacity, free-flow time
      build_graph.py          OpenStreetMap (Overpass) -> RoadGraph JSON
    decode/
      palette.py              measured Google colours -> class, casing-blend recovery
      weights.py              the 25/55/85/105 weight scale and demotion ladder
      impute.py               a weight for every road, including the 90 % Google skips
      decoder.py              capture tiles + RoadGraph -> per-edge congestion table
    search/
      astar.py, dijkstra.py
    traffic/
      bpr.py                  BPR volume-delay function
      demand.py               gravity-model synthetic OD demand
      assignment.py           incremental assignment / MSA loop, emits frames
    ml/
      kmeans.py, knn.py, logistic_regression.py
  tests/                      one test file per algorithm
  output/                     GeoJSON/CSV the visualizer picks up

visualizer/
  README.md
  requirements.txt
  server.py                   dev server: static UI, capture API, tile endpoint, layer API
  static/index.html, app.js, style.css
```

## 3. File contracts between subsystems

**Capture folder** `data-collection/captures/<name>/`

```
manifest.json        zoom, bbox, tile_range, coverage_pct, expected/received tiles,
                     validation stats, captured_utc, blocks[] with bounds + sha256
tiles/z{z}_{x}_{y}.png   every validated transparent traffic tile (native data)
blocks/z{z}_x{X0}_y{Y0}.png   16x16-tile mosaics (compact archive form)
tiles.csv.gz         one row per expected tile: bytes, sha256, opaque/traffic fraction, status
```

Any pixel converts to lon/lat with the standard Web Mercator tile formula
(`geo.pixel_to_lonlat`). Tile `(x, y)` at zoom `z` covers the standard slippy bounds.

**Road graph** `algorithms/output/graph/dhaka.json` (from `build_graph.py`)

```
nodes: {id: [lon, lat]}
edges: [{id, u, v, length_m, highway, lanes, oneway, capacity_vph,
         free_flow_kmph, free_flow_s, geometry: [[lon, lat], ...]}]
```

**Edge weight table** `algorithms/output/traffic/observed.csv` (from `decoder.py`)
and `complete.csv` (from `impute.py`, which adds a `method` column)

```
slot_utc, edge_id, cls, weight, f_green, f_orange, f_red, f_darkred,
coverage, vc_ratio, n_samples, source
weight: TRACK's own scale, higher = worse. green 25, yellow 55, red 85, dark red 105
        continuous, so a half-jammed edge sits between two rungs; vc_ratio = weight / 100
cls:    0 none, 1 green, 2 orange, 3 red, 4 darkred (the nearest rung, for reporting)
source: observed (Google painted it and we read it) | predicted (we inferred it)
        | none (decoder output only: below the coverage threshold)
(Google draws them #16e098, #ffcf43, #d1352b, #a92727: measured on the test capture, see data-collection/README.md)
```

**Visual layers** any `*.geojson` under `algorithms/output/` or `<capture>/layers/`.
The visualizer lists and draws them; property `color` (hex) or `cls` (0..4) drives styling.

## 4. Phases

| Phase | Scope | Status |
|---|---|---|
| A. Restructure | Three folders, docs moved, obsolete browser/API-key code removed, test capture renamed, clean configs and READMEs | done in this pass |
| B. Data collection core | `fetch.py` with strict validation, `capture.py` with preflight, retry pass, gap-fill, manifest checks; selftest | done in this pass |
| C. Visualizer dev server | tile endpoint with pooled lower zooms and cache, captures API, layer API, MapLibre UI with CARTO styles, label toggle, opacity | done in this pass |
| D. Algorithms foundation | RoadGraph, A\*, Dijkstra, BPR, gravity demand, incremental/MSA assignment, K-means, KNN, logistic regression, decoder, Overpass graph builder, tests | done in this pass (graph build and decoder need a network run to validate on real data) |
| E. Daily capture system | scheduler that captures every 15 min for N days, retries, per-day index, storage push, health report | next |
| F. Real-data validation | Dhaka graph built (33 k nodes, 76 k directed edges, 5 339 km, edges capped at 150 m); test capture decoded to continuous weights; left-of-travel offset tuned on data; every road imputed with KNN + K-means and hold-out evaluated; model layer rendered in the visualizer for side-by-side comparison | done |
| G. Models | K-means scenarios across time slots, KNN vs logistic regression on the edge table, evaluation | after E (needs many captures) |
| H. Engine and demo | BPR costs from predictions, assignment loop frames, before/after and animation in the visualizer, personal A-to-B route | after G |

## 5. Daily capture system (Phase E, design)

- `scheduler/run_daily.py`: loop with a fixed cadence (15 min), each tick calls
  `capture.py --name <YYYY-MM-DD>/<HHMM>`; sleeps to the next slot; never overlaps.
- Per-tick failsafes already exist in `capture.py` (validation, retry pass, gap-fill,
  coverage threshold, manifest checks). The scheduler adds: skip if the slot already
  exists, a daily index (`captures/index.jsonl`), free-disk guard, and a summary at
  07:00 Dhaka.
- Where it runs is still the owner's decision: a machine they control (polite rate,
  IP not shared) is safest; GitHub Actions is possible but shared-IP throttling is a
  real risk with keyless fetches.
- Storage: local first; optional push of each day to Hugging Face via `storage/`.

## 5b. The traffic weight and the imputed city

Google paints about 10 % of Dhaka's road length, essentially the arterial
network. Routing traffic *away* from a jam needs the other 90 % to exist and to
carry a cost, so the pipeline produces its own dataset covering every road.

- **Weight scale** (`traffic/weights.py`): green 25, yellow 55, red 85, dark red
  105, continuous, and `weight / 100` is the v/c ratio BPR consumes.
- **BPR recalibration** (`traffic/bpr.py`): the textbook alpha of 0.15 separates
  those four levels by under 20 %, which makes a router ignore traffic entirely.
  Alpha is pinned instead so dark red runs at a quarter of free flow
  (alpha ~ 2.47), giving 1.01x / 1.23x / 2.29x / 4.00x. `JAM_FACTOR` is the one
  dial to turn if the simulation reroutes too eagerly.
- **Imputation** (`traffic/impute.py`): k nearest observed roads, inverse-distance
  weighted, one step down the ladder when the road is smaller than the roads it
  copies from, faded toward its K-means zone mean by exp(-distance / 250 m) so a
  jam does not repaint a whole neighbourhood.
- **Self-checks in every run**: the demotion assumption is measured against the
  observed roads, and a hold-out compares four models. Numbers land in
  `output/traffic/impute-report.json`; the summary is in `algorithms/README.md`.

## 6. Risks and fallbacks

| Risk | Signal | Fallback |
|---|---|---|
| Tile URL format changes | validation rejects rise, coverage drops | `fetch.py` is the only place the URL lives; re-capture the URL from the public site |
| IP throttling during daily runs | HTTP 429 / 403 counts in manifest | lower rate, longer cadence, change host |
| Roads in graph without traffic data | decoder coverage per edge class | model only edge classes with coverage; document as data limit |
| Palette drift (Google restyles the layer) | `palette_match` check in every manifest warns below 80% match; `analyze.measure_colours` shows the new colours | palette lives in `data-collection/config.json`, `algorithms/.../decode/palette.py` and `visualizer/server.py`; update all three, then `python collector/reaudit.py --name <capture>` |

## 7. How to run each subsystem

```
# data collection
cd data-collection
python tests/selftest.py
python collector/capture.py --name my-capture            # full Dhaka box from config
python collector/capture.py --name small --bbox 23.765,23.745,90.405,90.385
python collector/reaudit.py --name my-capture            # after a palette change: rewrite stats, keep tiles

# visualizer
cd visualizer
python server.py                                        # http://127.0.0.1:8765

# algorithms
cd algorithms
python -m pytest tests -q
python -m track_algos.graph.build_graph --out output/graph/dhaka.json
python -m track_algos.decode.decoder --capture ../data-collection/captures/test-capture-2026-09-12 --graph output/graph/dhaka.json
```
