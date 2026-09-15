# TRACK

Traffic Route optimization using A* search, Clustering and KNN.
A data-driven approach to congestion-aware traffic optimization in Dhaka.

TRACK reads Google's traffic layer for the whole Dhaka metro area, turns it into
a traffic weight for every road in the city, and runs its algorithms on that
data: A*, Dijkstra, K-means, KNN, logistic regression, a gravity demand model
and a traffic assignment loop. A map shows the captured traffic, the model, the
road graph, and one layer per algorithm.

A GitHub Actions workflow repeats the whole thing every 10 minutes and publishes
the result to GitHub Pages: https://cord1ess.github.io/TRACK/

## Parts

| Folder | What it does | Run |
|---|---|---|
| [data-collection/](data-collection/) | Captures Google's traffic tiles for the metro area. No API key. Every tile is validated. | `python collector/capture.py --name <name>` |
| [algorithms/](algorithms/) | Road graph from OpenStreetMap, tile decoder, imputation, the algorithms, one map layer per algorithm. | `python pipeline.py` |
| [visualizer/](visualizer/) | The map. A dev server for local use; `build_site.py` makes the static site for GitHub Pages. | `python server.py` |

The parts share files only: a capture folder, the road graph, the edge weight
table, and the layer index.

## How the data flows

1. Capture. 4,928 tiles at zoom 17 cover the metro area. About 13 minutes at 8 requests a second.
2. Graph. 48,413 nodes and 110,382 directed edges from OpenStreetMap, 8,302 km, no edge longer than 150 m. Built once and committed.
3. Decode. Every edge is sampled along its length and read from the tiles into a weight: green 25, yellow 55, red 85, dark red 105. 13 seconds.
4. Impute. Google paints about a tenth of the network. The rest is predicted from the nearest observed roads with KNN, faded to a K-means zone average far from any data. 16 seconds.
5. Layers. Every algorithm runs on the result and writes a map layer. 65 seconds.
6. Site. The map and all its data as static files. 19 seconds.

## Collection and deployment

Two workflows. `deploy.yml` publishes the site on every push that changes the
app or the algorithms, from the data already collected, so the site is live
before the first capture. `collect.yml` does one cycle: capture, pipeline,
build, deploy, and then starts the next run itself, so one manual start keeps
collection going. Runs never overlap. A capture takes about 13 minutes and
the rest of a run about 5, so new data lands about every 20 minutes. A failed
capture stops the run and the site keeps its last data; the 10-minute
schedule restarts the chain, when GitHub honours it. The page shows the
latest capture and its age, the current run and how long it has been going,
when the site was built, and links to the run history.

Setup, once:

1. Settings, Pages, Build and deployment, Source: GitHub Actions.
2. Optional: repository secrets `HF_TOKEN` and `HF_REPO` archive every capture to a private Hugging Face dataset, and let a fresh deploy start from the newest captures there. Without them that step is skipped.
3. Actions, deploy, Run workflow, to publish the site. Then Actions, collect, Run workflow, once: each run starts the next.

The site keeps the last 3 captures (`KEEP` in the workflow). The page checks
`data/manifest.json` every 5 seconds and swaps in new data without a reload.

Google's terms do not allow storing or republishing their map tiles. The site
publishes the captured tiles anyway, by the project owner's decision. GitHub
could take the site down for it.

## Run locally

```
cd algorithms       && pip install -r requirements.txt && python -m pytest tests -q
cd ../data-collection && pip install -r requirements.txt && python collector/capture.py --name my-capture
cd ../algorithms    && python pipeline.py --capture ../data-collection/captures/my-capture
cd ../visualizer    && pip install -r requirements.txt && python server.py    # http://127.0.0.1:8765
```

The road graph is included as `algorithms/output/graph/dhaka.json.gz`; the
pipeline unpacks it on first run, so nothing queries OpenStreetMap unless you
ask (`python pipeline.py --only graph --force`). Captures and pipeline outputs
are not committed.

To build the static site yourself: `python visualizer/build_site.py --out site`,
then serve the `site` folder with any file server.

## Docs

- [docs/project-plan.md](docs/project-plan.md): decisions, layout, file contracts, status, risks.
- [docs/presentation.md](docs/presentation.md): talking points.
