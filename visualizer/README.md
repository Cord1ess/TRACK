# TRACK visualizer

The map. Locally a dev server serves it with live data; for GitHub Pages
`build_site.py` writes the same page and data as static files.

```
pip install -r requirements.txt
python server.py                            # http://127.0.0.1:8765
python build_site.py --out ../site --keep 12 # the static site
```

## What it shows

- Every capture under `../data-collection/captures/` as a raster tile layer, full resolution at zoom 17. Lower zooms are built by max-alpha pooling so thin lines survive, and cached under `<capture>/_pyramid/`. A line under the capture controls says how the collection is going: the latest capture and its age, when the site was built, the cadence, and a link to the run history. A capture that arrives while the page is open is listed, marked on the timeline and shown.
- TRACK's model from `../algorithms/output/traffic/complete.csv` as vector lines on the same green, amber, red ramp measured from Google's tiles, so the two compare directly. All roads, observed only, or predicted only; A/B flips between the capture and the model.
- The road graph: 48,413 junctions and 60,409 segments (two-way pairs merged), with the 9,373 cut points from the 150 m cap hidden by default, a pulse along each segment in its direction, one-way arrows from zoom 15, and colour by class, direction or length.
- The algorithm layers from `../algorithms/output/layers/`: one per algorithm, each listed with what it shows, a legend and its numbers.
- Light and dark CARTO basemaps, no keys, with labels and the base map itself switchable off.

Turn on inspect (`I`) and click a road for its weight, source, coverage and the
slowdown the current settings give it, or a junction for its degree and roads.

Hotkeys: `T` capture, `M` model, `C` A/B flip, `P` pure colours, `L` labels,
`B` base map, `F` fit, `I` inspect, `G` road graph, `N` reset bearing, `1` `2`
light and dark, `\` panel, space play and pause, shift with the arrows steps
from capture to capture, `?` for the list.

## Tuning weights live

The Weights tab changes what each traffic level is worth and the shape of the
slowdown curve. Nothing is recomputed on the server: the model carries each
road's raw numbers, and the sliders rebuild the map style, so the whole city
re-colours as you drag. Colour by Delay or Speed to see it; Level is fixed by
definition. To bake a setting into the data, re-run the pipeline:

```
cd ../algorithms && python pipeline.py --only impute,layers --demote-strength 0.6 --decay-m 180
```

## Timeline

The bar at the bottom spans whatever the captures cover, with one mark per
capture placed at its own timestamp rather than snapped to a grid. Times are
Dhaka local (UTC+6) in 12-hour form, because that is the clock the traffic
happened on.

It zooms: the wheel narrows the window about the pointer, `+` `−` `All` and a
double click do the same, and the ticks become hours once the window is under
eight hours wide. Clicking a mark opens that capture's details — when it was
taken, the wait since the previous one, coverage, painted tiles and status.
Play walks the captures at 0.5x to 4x and keeps the head in view when zoomed
in, shift with the wheel or the arrows steps between them, and dragging lands
on the nearest one. Red marks are gaps of more than 25 minutes.

Two earlier designs broke here, and both are worth remembering. Quantising the
week into 15-minute slots collapsed twelve captures six minutes apart into five
slots, left seven unreachable, and froze play because stepping landed back on
the same slot. Anchoring the track to the Monday of the newest capture's week
then hid every capture before it: a run from 16 to 23 September drew only the
last three days, and the fixed Mon..Sun labels named the wrong dates.

A rebuild of the track clears the play timer and restarts it. Leaving the timer
running against a replaced list of stops meant `playing` stayed true behind an
orphaned interval, and pause could not stop it.

## Live data

The server notices when the graph, the weight table or the layers change on
disk and rebuilds once the files settle. The page checks every 5 seconds and
swaps new data in without a reload. Until the pipeline has run, the panel says
so. A weight table built against a different graph is refused, because edge ids
restart at 0 on every build.

On the static site there is no server: `build_site.py` writes what the `/api`
routes answer into `data/manifest.json`, and the page polls that file instead.
Model and graph files are named by content version, so a new manifest points at
new files and the old ones are never re-read.

## Reliability

Whatever the controls say is on screen stays on screen. A browser suite drives
the app through every control, theme and capture switches, server restarts, the
graph layer and a few hundred random actions, checking every 60 ms that no road
layer has gone missing, hidden or stuck part-way through an animation.

- Theme switches carry the data across. The basemap changes underneath; road layers are never removed or downloaded again.
- Nothing is torn down to change what it shows. A capture switch swaps the tile address; new model data replaces the old only once it has loaded.
- Animations cannot strand a layer. Each reveal has a timer that forces it visible, survives exceptions, and finishes at once in a background tab.
- A watchdog repairs drift every second.
- The server never serves nothing. Until a rebuild succeeds, the last complete data is served and the panel says so. Failed loads in the page retry while the drawn roads stay.

## Static site

`build_site.py --out ../site --keep 12` writes the following. An existing site
folder is reused: a capture's tiles never change once written, so only tiles
for a capture the site does not have yet are copied and tiles for captures no
longer kept are deleted. Everything else is rewritten every time, and `--fresh`
rebuilds from empty.

```
index.html, style.css, js/    the page and its ES modules
data/manifest.json           the /api answers, in one file
data/model-<part>.<v>.json   the model, versioned by content
data/graph-<part>.<v>.json   the road graph
data/layers/*.json           the algorithm layers and their index
tiles/<capture>/z/x/y.png    the newest --keep captures, native zoom and pyramid
tiles-clean/<capture>/...    the pure-colours variant
```

`--prune` deletes captures that were not kept. The collection workflow does not
use it: the runner is discarded after every run, so nothing accumulates there.
With twelve captures the site is 526 MB and about 160,000 files, over half the
1 GB Pages limit; the model and graph files are served compressed by the CDN.

A capture arrives from the archive with its tile pyramid already built, so
putting one on the site costs about 8 seconds rather than the 117 it takes to
regenerate.

## API

| Route | Returns |
|---|---|
| `GET /api/captures` | capture summaries |
| `GET /api/captures/<name>` | full manifest |
| `GET /tiles/<name>/<z>/<x>/<y>.png` | traffic tile, transparent if none; `?clean=1` for pure colours |
| `GET /api/model` | whether the model is loaded, its version and counts |
| `GET /model.geojson?part=major\|minor&v=<version>` | the model as gzipped vector features: `w` weight, `s` source, `c` coverage, `h` road class, `f` free-flow km/h, `o` reveal order |
| `GET /api/graph` | whether the road graph is loaded, its version and counts |
| `GET /graph.geojson?part=nodes\|links&v=<version>` | the graph as gzipped vector features. Nodes: `n` id, `d` neighbours, `x` cut point, `r` road class. Links: `e` id, `o` one-way, `t` two-way, `r` class, `l` length |
| `GET /api/layers` | the layer index: id, name, description, legend, summary, url |
| `GET /layers/<key>` | one layer |

Versioned payloads are cached for good when `v` names the served version and
revalidated by ETag otherwise. Only one server may hold the port; a second one
refuses to start and names the process to stop. No API keys anywhere; only
Pillow and numpy are needed.
