# TRACK project plan

TRACK reads Google's traffic layer for Dhaka, gives every road in the city a
traffic weight, and runs its algorithms on that data. Three parts that share
files only.

```
TRACK/
  data-collection/    capture of Google traffic tiles -> captures/<name>/
  algorithms/         road graph, decoder, imputation, the algorithms, map layers
  visualizer/         the map: dev server locally, static site on GitHub Pages
  .github/workflows/  collect.yml: capture, pipeline, build, deploy every 10 minutes
  docs/
```

## 1. Decisions

| # | Decision | Why |
|---|---|---|
| 1 | Google's consumer tile server is the data source. It returns the traffic layer as transparent 256 px tiles with no key. | No card-backed API key is obtainable. Every other provider lacks Dhaka density. Verified: 4,928-tile metro capture, 100 % coverage, identical bytes on re-fetch. |
| 2 | Zoom 17 is the native maximum. | Zoom 18 renders no traffic; 17 and 16 carry the same roads. Residential lanes have no traffic at any zoom: a Google data limit. |
| 3 | Images are an intermediate. The decoder turns tiles into a weight per directed edge on our own OpenStreetMap graph; everything else uses the graph and that table. | Models and the map work on one dataset. |
| 4 | The road graph is built once and committed, gzipped. | 5 MB. The workflow never queries OpenStreetMap. |
| 5 | The algorithms are written from scratch on numpy, short and readable, one test file each. | They are what the project shows. |
| 6 | Collection runs on GitHub Actions every 10 minutes and the site is GitHub Pages. | Free, no server to keep alive. A capture takes about 2 minutes and a run about 8, so the site updates about every 10 minutes. |
| 7 | The public site includes the captured tiles. Every capture is also archived to a private Hugging Face dataset. | Owner's decision. Google's terms forbid storing and republishing tiles; GitHub could take the site down. |

## 2. Layout

```
data-collection/
  config.json                 bbox, zoom, rate, palette, thresholds
  collector/                  grid, fetch, tiles, analyze, checks, capture, reaudit
  storage/upload.py           one capture -> private Hugging Face dataset
  storage/download.py
  scheduler/daily_check.py    health report over the archive
  tests/selftest.py

algorithms/
  pipeline.py                 graph -> decode -> impute -> layers
  track_algos/
    geo.py
    graph/road_graph.py, build_graph.py
    decode/palette.py, decoder.py
    traffic/weights.py, impute.py, bpr.py, demand.py, assignment.py
    search/astar.py, dijkstra.py
    ml/kmeans.py, knn.py, logistic_regression.py
    layers.py                 one map layer per algorithm
  tests/                      one file per algorithm
  output/graph/dhaka.json.gz  committed; everything else under output/ is generated

visualizer/
  server.py                   dev server: UI, tiles, model, graph, layers
  model.py, graph_data.py     the model and graph as versioned vector payloads
  build_site.py               the static site for GitHub Pages
  static/index.html, app.js, style.css
```

## 3. File contracts

Capture folder `data-collection/captures/<name>/`: `manifest.json` (zoom,
bbox, tile_range, coverage_pct, captured_utc, status, checks),
`tiles/z{z}_{x}_{y}.png`, `blocks/`, `tiles.csv.gz`.

Road graph `algorithms/output/graph/dhaka.json`: `graph_id`, `nodes: {id:
[lon, lat]}`, `edges: [{id, u, v, length_m, highway, lanes, oneway,
capacity_vph, free_flow_kmph, free_flow_s, geometry}]`. Cut points from the
150 m cap have negative ids.

Edge weight table `observed.csv` and `complete.csv`: `slot_utc, edge_id, cls,
weight, f_green, f_orange, f_red, f_darkred, coverage, vc_ratio, n_samples,
source`; `complete.csv` adds `method`. Weight: green 25, yellow 55, red 85, dark
red 105, continuous; `vc_ratio = weight / 100`. Source: observed, predicted, or
none (decoder only, below the coverage threshold). A `.meta.json` beside each
table carries the `graph_id`.

Layer index `algorithms/output/layers/index.json`: `{graph_id, capture,
slot_utc, built_utc, seconds, layers: [{id, file, name, algorithm,
description, legend, summary, stats, features}]}`. Features carry `color` or
`cls`, optionally `width`.

Site manifest `site/data/manifest.json`: `{built_utc, min_zoom, keep, captures,
model, graph, layers}`, the same answers the dev server gives at `/api/...`.
Model and graph files are named by content version, `data/model-<part>.<v>.json`
and `data/graph-<part>.<v>.json`; the page reloads them when the version in the
manifest changes.

## 4. Status

| Phase | Scope | Status |
|---|---|---|
| Collection | tile fetch with validation, retry pass, gap fill, manifest checks | done |
| Graph | OpenStreetMap to a 48,413-node, 110,382-edge graph, cut every 150 m | done, committed |
| Decoder and imputation | tiles to a weight per edge; every unobserved road predicted; self-checks | done |
| Algorithms | A*, Dijkstra, BPR, gravity demand, assignment, K-means, KNN, logistic regression, tests | done |
| Map layers | one layer per algorithm, listed with a description, legend and numbers | done |
| Visualizer | captures as tiles, the model, the road graph, layers, live weight tuning, reliability suite | done |
| Scheduled collection | GitHub Actions every 10 minutes, Hugging Face archive, GitHub Pages deploy, live update in the page | done |
| Models across time | K-means scenarios over many captures, KNN against logistic regression per time of day | not started; the workflow now produces the captures |
| Engine and demo | before and after assignment layers exist; the animated run and personal A-to-B routing are not built | partly |

## 5. Risks

| Risk | Signal | Fallback |
|---|---|---|
| Google throttles the shared GitHub Actions addresses | capture status failed, HTTP 429 in the manifest | the run stops without deploying; the next run tries again; lower `rate` in config.json |
| Tile URL format changes | validation rejects rise, coverage drops | the URL lives only in `fetch.py` |
| Google restyles the palette | `palette_match` below 80 % | update `config.json`, `palette.py` and `server.py`, then `reaudit.py` |
| GitHub removes the site for republishing tiles | site gone | keep only derived data on the site: drop the tiles from `build_site.py` |
| Scheduled workflows are delayed at busy times and disabled after 60 days without a commit | gaps in captures | commit occasionally; run manually from Actions |

## 6. Run

```
cd data-collection && python collector/capture.py --name my-capture
cd ../algorithms   && python pipeline.py --capture ../data-collection/captures/my-capture
cd ../visualizer   && python server.py                      # http://127.0.0.1:8765
python build_site.py --out ../site --keep 3                 # the static site
```
