# TRACK algorithms

Everything TRACK computes, written from scratch on numpy. Each module opens
with a short note on what it does. `tests/` has one file per algorithm.

```
pip install -r requirements.txt
python -m pytest tests -q
```

## Modules

| Module | What it does |
|---|---|
| `geo.py` | Web Mercator tile maths and haversine distance |
| `graph/road_graph.py` | `RoadGraph`: junctions, directed edges, capacity, free-flow time; JSON and GeoJSON |
| `graph/build_graph.py` | OpenStreetMap (Overpass) to `RoadGraph`, cut at every junction and every 150 m |
| `decode/palette.py` | Google's traffic colours to a class per pixel |
| `decode/decoder.py` | Capture tiles and graph to a weight per edge |
| `traffic/weights.py` | The weight scale (green 25, yellow 55, red 85, dark red 105) and the demotion ladder |
| `traffic/impute.py` | A weight for every road Google never painted, with its own checks |
| `traffic/bpr.py` | Travel time from volume over capacity (BPR function) |
| `traffic/demand.py` | Trips between zones from a gravity model |
| `traffic/assignment.py` | Route trips on congested costs and feed the load back: incremental and MSA |
| `search/astar.py` | A* with a haversine heuristic and an injected cost function |
| `search/dijkstra.py` | Least cost from one node to every node |
| `ml/kmeans.py` | K-means with k-means++ start |
| `ml/knn.py` | KNN classifier and regressor |
| `ml/logistic_regression.py` | Logistic regression, binary and one-vs-rest |
| `layers.py` | Runs every algorithm on the current data and writes one map layer each |

## Pipeline

```
python pipeline.py --capture ../data-collection/captures/<name>
```

Stages: `graph`, `decode`, `impute`, `layers`. A stage is skipped when its
output is newer than its inputs. `--force` redoes everything, `--only
decode,impute` runs a subset, `--quick` skips the hold-out evaluation in impute
(the collection workflow uses it).

The road graph is committed as `output/graph/dhaka.json.gz` and unpacked on
first run. `python pipeline.py --only graph --force` rebuilds it from
OpenStreetMap; gzip the result to update the committed copy.

Times on the metro capture: decode 13 s, impute 16 s (225 s with the
evaluation), layers 65 s.

## The traffic weight

One number per directed edge, higher means worse: green 25, yellow 55, red 85,
dark red 105. Divided by 100 it is the volume over capacity ratio the BPR
function takes. It is continuous, so an edge that is half green and half red
lands near 55 instead of being forced into one class.

`source` says where a weight came from: `observed` means Google painted that
road and the decoder read it, `predicted` means it was inferred. On the metro
capture that is 4,878 observed edges out of 110,382.

The BPR function is calibrated so a dark red road runs at a quarter of its
free-flow speed. With the textbook value the four levels would differ by under
20 % and the router would ignore traffic.

## What the data says

Measured on the 2026-09-12 metro capture, written to
`output/traffic/impute-report.json` on every full run:

| Question | Answer |
|---|---|
| Which side of the road is a direction's line on? | Left. Sampling left of travel observes 72 % of major-road edges, right observes 47 %. |
| How much of Dhaka does Google paint? | 81 % of primary, 69 % of trunk and motorway, 56 % of secondary, 20 % of tertiary, 2.8 % of residential. About a tenth of road length. |
| Is a smaller road quieter than the big road beside it? | Only where the big road is busy. Beside a road at yellow or worse the smaller road averages 15 points lower and is lower in 62 % of pairs. |
| Does predicting from neighbours work? | Hold-out on observed edges: mean error 6 weight points and 85 % of predictions on the right level, against 15 points for predicting the global mean. |

Two caveats. The held-out edges are mostly major roads, because those are the
ones with data, so the hold-out cannot validate the demotion step for
residential streets. And Google paints a residential street only where it has
probe traffic, so the small roads in the sample are the busy ones.

## Layers

`layers.py` writes `output/layers/index.json` and one GeoJSON per layer.

| Layer | Algorithm | What it shows |
|---|---|---|
| `decoder-coverage` | decoder | Observed roads, shaded by how much of each road Google painted |
| `impute-method` | impute | Every road by how its weight was decided |
| `kmeans-zones` | K-means | Observed roads grouped into 24 zones, with zone centres |
| `knn-holdout` | spread | A fifth of the observed roads hidden and predicted by the spread; colour is how many levels off |
| `lr-classes` | logistic regression | Predicted traffic level for every road; hold-out accuracy against KNN |
| `dijkstra-reach` | Dijkstra | Minutes from Shahbagh to every road on today's traffic |
| `astar-routes` | A* | Six routes across the city, on an empty network and on today's traffic |
| `demand-flows` | gravity model | The strongest zone-to-zone flows |
| `assignment-before` | assignment | The load before new trips |
| `assignment-all-at-once` | assignment | Every trip on its shortest path, no feedback |
| `assignment-incremental` | assignment | Trips routed in ten batches, each avoiding what the last filled |

On the metro capture: 617 roads over capacity when every trip takes its
shortest path at once, 496 with incremental assignment, from 263 before the
trips. 600 A* searches in 40 seconds.

## File contracts

Edge weight table, `observed.csv` and `complete.csv`:
`slot_utc, edge_id, cls, weight, f_green, f_orange, f_red, f_darkred, coverage,
vc_ratio, n_samples, source`. `complete.csv` adds `method` (observed,
adjacent, spread or assumed_clear) and `hops`, the junctions between a road
and the painted road its colour came from (0 observed, -1 none). Every weight
is exactly 25, 55, 85 or 105. A `.meta.json` beside each
table records the `graph_id` its edge ids belong to; readers refuse a mismatch.

Layer index, `output/layers/index.json`:
`{graph_id, capture, slot_utc, built_utc, seconds, layers: [{id, file, name,
algorithm, description, legend: [{color, label}], summary, stats, features}]}`.

Layer GeoJSON features carry `color` (hex) or `cls` (1 to 4) and optionally
`width`; the visualizer styles them from those.
