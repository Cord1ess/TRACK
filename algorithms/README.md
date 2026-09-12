# TRACK Algorithms

Clean, readable, from-scratch implementations of everything TRACK computes, each
with a docstring that explains the idea and a test that proves it. numpy only.

```
pip install -r requirements.txt
python -m pytest tests -q
```

## Map of the package

| Module | What it is | Role in TRACK |
|---|---|---|
| `geo.py` | Web Mercator tile/pixel math, haversine | shared by decoder and A\* heuristic |
| `graph/road_graph.py` | `RoadGraph`: nodes, directed edges, capacity, free-flow time; JSON + GeoJSON | the network everything runs on |
| `graph/build_graph.py` | OpenStreetMap (Overpass) → `RoadGraph`, split at junctions and at a length cap, one-way handling | the Dhaka graph |
| `decode/palette.py` | measured Google colours → class 1..4, casing-blend recovery | colour → class |
| `decode/decoder.py` | capture tiles + graph → per-edge weight table (CSV) + GeoJSON layer | **images → data** |
| `traffic/weights.py` | the 25/55/85/105 weight scale, demotion ladder, colour ramp | class → number the model uses |
| `traffic/impute.py` | fills in every road Google never painted; measures its own assumption | **partial data → the whole city** |
| `search/astar.py` | A\* with admissible haversine heuristic, injectable costs | the router, called thousands of times |
| `search/dijkstra.py` | one-to-all least cost | baselines, hub distances |
| `traffic/bpr.py` | BPR volume-delay function | congestion → travel time |
| `traffic/demand.py` | gravity-model synthetic OD demand over zones | trips without vehicle data |
| `traffic/assignment.py` | incremental assignment and MSA loop; frames for the visualizer | the "thinning out" engine |
| `ml/kmeans.py` | K-means++ clustering | traffic scenarios |
| `ml/knn.py` | KNN classifier and regressor + standardisation | congestion prediction, road imputation |
| `ml/logistic_regression.py` | logistic regression + one-vs-rest | congestion prediction, compared with KNN |

## Pipeline on real data

```
python pipeline.py                  # graph -> decode -> impute, skipping what is already current
python pipeline.py --force          # redo everything
python pipeline.py --only impute --decay-m 180 --demote-strength 0.6    # retune just the model
cd ../visualizer && python server.py    # then tick "show our data"
```

Or the three stages by hand:

```
python -m track_algos.graph.build_graph --out output/graph/dhaka.json --max-edge-m 150 \
       --osm-cache output/graph/dhaka-osm.json
python -m track_algos.decode.decoder --capture ../data-collection/captures/test-capture-2026-09-12 \
       --graph output/graph/dhaka.json --out output/traffic/observed.csv --geojson
python -m track_algos.traffic.impute --graph output/graph/dhaka.json \
       --observed output/traffic/observed.csv --out output/traffic/complete.csv
```

## The traffic weight

One number per directed edge, higher means worse: **green 25, yellow 55, red 85,
dark red 105**. Divided by 100 it is the volume/capacity ratio BPR takes, so the
scale is both readable and directly usable as a cost. It is continuous, not a
class label: an edge that is half green and half red lands near 55 rather than
pretending to be one or the other.

`source` says where a weight came from. `observed` means Google painted that
road and we read it; `predicted` means we inferred it. On the Dhaka test capture
that split is about 10 % observed to 90 % predicted, because Google paints
almost only the arterial network.

## What the model decided, and what the data said

Measured on the 2026-09-12 capture, all of it reproduced in
`output/traffic/impute-report.json` on every run:

| Question | Answer from the data |
|---|---|
| Which side of the road is a direction's line on? | Left. Sampling left of travel observes 72 % of major-road edges, right observes 47 %. |
| How much of Dhaka does Google paint? | 81 % of primary, 69 % of trunk and motorway, 56 % of secondary, 20 % of tertiary, 2.8 % of residential. About 10 % of road length. |
| Is a smaller road quieter than the big road beside it? | Only where the big road is busy. Across all pairs the difference is +4 (no effect, mostly green beside green). Beside a road at yellow or worse it is **-21 weight points**, lower in 62 % of pairs: almost exactly one rung of the ladder. |
| Does predicting from neighbours work? | Hold-out on observed edges: MAE about 8 weight points and 81 % of predictions land on the right rung, against 14 for predicting the global mean. |

Two caveats the report repeats and the write-up should keep: the held-out edges
are mostly major roads, because those are the ones with data, so the hold-out
cannot validate the demotion step for residential streets; and Google paints a
residential street only where it has probe traffic, so the small roads in the
sample are the busy ones. The absence of data is itself weak evidence of a quiet
road, which is the real argument for demoting.

## Contracts

- Edge weight CSV: `slot_utc, edge_id, cls, weight, f_green, f_orange, f_red, f_darkred, coverage, vc_ratio, n_samples, source`; `complete.csv` adds `method`.
- GeoJSON for the visualizer: features carry `cls` (0..4) and/or `color` (hex), optional `width`.
- Assignment frames: `Assignment.frames[i]` = `{label, volume, vc, stats}`; `frame_geojson(frame)` renders one.
