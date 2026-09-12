# TRACK

**T**raffic **R**oute optimization using **A**\* search, **C**lustering, and **K**NN.
A data-driven approach to congestion-aware traffic optimization in Dhaka City.

TRACK learns where and when Dhaka gets congested from Google's traffic layer, then shows
the whole city being rebalanced: thousands of A\* trips routed inside a traffic-assignment
loop until no route has an obviously better alternative (Wardrop's user equilibrium).
Concept and pitch: [`docs/idea.md`](docs/idea.md), [`docs/presentation.md`](docs/presentation.md).
Current plan: [`docs/project-plan.md`](docs/project-plan.md).

## Three subsystems

| Folder | Purpose | Run |
|---|---|---|
| [`data-collection/`](data-collection/) | Keyless capture of Google traffic tiles into `captures/<name>/` with strict validation and failsafes | `python collector/capture.py --name <name>` |
| [`algorithms/`](algorithms/) | Images to data (decoder), OpenStreetMap road graph, traffic weights for **every** road, A\*, Dijkstra, BPR, gravity demand, assignment loop, K-means, KNN, logistic regression. Clean, tested, readable | `python pipeline.py` |
| [`visualizer/`](visualizer/) | Dev server + map UI: captures as real tile layers, TRACK's own data rendered in the same tile grid for side-by-side comparison, algorithm outputs as GeoJSON layers, clean keyless vector basemap | `python server.py` |

They communicate only through files with fixed contracts (see the plan, section 3):
a capture folder, a road-graph JSON, an edge-weight CSV, and GeoJSON layers.

## From pictures to a model of the city

Google paints traffic on roughly a tenth of Dhaka's road length, essentially the
arterial network. TRACK turns that into its own dataset covering all of it:

1. **Capture** the traffic layer as transparent tiles, no API key.
2. **Build** the road graph from OpenStreetMap: 33 k nodes, 76 k directed edges,
   5 339 km, every edge cut to at most 150 m so one edge means one traffic condition.
3. **Decode** the tiles onto the graph into a continuous weight per edge
   (green 25, yellow 55, red 85, dark red 105; `weight / 100` is the v/c ratio BPR takes).
4. **Impute** the roads Google never painted, from the roads around them, and record
   for every edge whether its weight was observed or predicted.
5. **Route** with A\* on BPR costs, so a jam on an arterial pushes trips onto the side
   streets that now have costs of their own.

## Quick start

```
cd data-collection && pip install -r requirements.txt && python tests/selftest.py --network
cd ../visualizer   && pip install -r requirements.txt && python server.py     # open http://127.0.0.1:8765
cd ../algorithms   && pip install -r requirements.txt && python -m pytest tests -q
python pipeline.py    # graph -> decode -> impute, then tick "show our data" in the visualizer
```

The first full-city capture is included: `data-collection/captures/test-capture-2026-09-12/`
(taken around 1 AM Dhaka time, so traffic is sparse; a daytime capture is much denser).
