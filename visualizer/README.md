# TRACK Visualizer

A dev server and map UI that shows captured traffic, TRACK's own derived data, and
algorithm outputs on a clean, keyless vector basemap (MapLibre GL + CARTO styles, the
same stack mapcn uses).

```
pip install -r requirements.txt
python server.py                      # http://127.0.0.1:8765
python server.py --build-pyramid test-capture-2026-09-12   # optional: pre-build lower zooms
```

## What it does

- Lists every capture under `../data-collection/captures/` and draws the selected one
  as a real raster tile layer, full resolution at the native zoom.
- Builds lower-zoom tiles on demand by max-alpha pooling (thin traffic lines survive)
  and caches them under `<capture>/_pyramid/`, so panning out stays fast.
- **Draws TRACK's own traffic data as vector lines**, crisp at every zoom and
  re-styleable live (see below).
- Lists every `*.geojson` under `../algorithms/output/` (and `<capture>/layers/`) as a
  toggleable layer. Features styled by `color` (hex) or `cls` (0 none, 1 green, 2 orange,
  3 red, 4 dark red) and optional `width`. This is how routes and assignment frames
  get drawn.
- Light and dark CARTO basemaps, either of which can be switched off entirely for a
  plain background, plus a labels toggle and a fade slider.

## Layout

A top bar for what you are looking at, map controls on the left, a tabbed panel on the
right (Layers, Style, Weights), a legend bottom right, a timeline bottom centre, and the
position readout bottom left. The panel folds away with `\` when you want the whole map.

## Comparing our data against Google's

The **TRACK model** section draws `../algorithms/output/traffic/complete.csv` over the
graph as vector lines, on the same green/amber/red ramp measured from Google's own tiles.
It sits directly above the captured raster in the same map, so the two can be compared
directly:

| Control | What it shows |
|---|---|
| `All` | every road: decoded plus predicted, the full dataset the router uses |
| `Observed` | only roads Google painted, as the decoder read them. Flip against the capture to judge decode fidelity |
| `Predicted` | only the roads we filled in, the ~90 % Google never paints |
| **A/B compare** (`C`) | shows exactly one of the capture and our data, and swaps them |

Turn on the inspect tool (magnifier, or `I`) and click a road to see its weight, source,
coverage, free-flow speed and the slowdown the current settings give it.

Hotkeys: `T` capture, `M` our data, `C` A/B flip, `P` pure colours, `L` labels,
`B` base map, `F` fit, `I` inspect, `N` reset bearing, `1`/`2` light/dark, `\` panel,
space play/pause, arrows step the timeline, `?` for the full list.

## Tuning weights live

The **Weights** tab changes what each traffic level is worth (green / yellow / red /
dark red) and the shape of the slowdown curve (jam factor and steepness). Nothing is
recomputed on the server: the model layer ships each road's raw numbers, and the sliders
rebuild the MapLibre style expression, so the whole city re-colours on the GPU as you
drag. Switch **Colour roads by** to `Delay` or `Speed` to see the effect, since `Level`
is a fixed reference that by definition does not move.

Changes are live on the map only. To bake them into the data, re-run the pipeline:

```
cd ../algorithms && python pipeline.py --only impute --demote-strength 0.6 --decay-m 180
```

## Timeline

The bar at the bottom is a week of 15-minute slots, the shape the daily collection will
fill. Slots that have a capture are marked; drag, scroll or arrow along it, or press play
to sweep the week. With one capture there is one marked slot, and the readout says how
many of the 672 are collected.

The server notices when the graph or the weight table change on disk and rebuilds in
the background once the files stop changing, so re-running `python pipeline.py` in
`../algorithms/` is enough: the page checks every five seconds and swaps the new data in
without a reload. Until that pipeline has run, the panel says so and the controls stay
disabled. A weight table built against a different graph is refused rather than drawn,
because edge ids restart at 0 on every build and would otherwise line up against the
wrong roads.

## Reliability

The map is built so that whatever the controls say is on screen stays on screen. A
browser suite drives the app through every control, theme switches, capture switches,
server restarts and a few hundred random actions while checking every 60 ms that no road
layer has gone missing, hidden, or stuck part-way through an animation.

- **Theme switches carry our data across.** The basemap changes underneath; the road
  layers and their data are never removed or downloaded again. Both basemap styles are
  fetched once, so quick repeated switches cannot land out of order.
- **Nothing is torn down to change what it shows.** A capture switch swaps the tile
  address on the existing layer, and new model data replaces the old only once it has
  loaded, so the old roads stay drawn in the meantime.
- **Layer changes are never skipped.** They wait for the style itself to be ready, not
  for every tile to finish loading, which while panning is almost never.
- **Animations cannot strand a layer.** Each reveal has a timer that forces it fully
  visible, survives exceptions, and finishes at once in a background tab.
- **A watchdog repairs drift every second**, re-adding anything missing and re-applying
  every control.
- **The server never serves nothing.** Until a rebuild succeeds, the last complete data
  keeps being served and the panel says so; a half-written or missing input file cannot
  blank the map. Failed loads in the page retry with backoff while the drawn roads stay.

Model data is versioned by content, so an unchanged payload costs a cached read or a 304
rather than a fresh 1.7 MB download.

## API

| Route | Returns |
|---|---|
| `GET /api/captures` | capture summaries |
| `GET /api/captures/<name>` | full manifest |
| `GET /tiles/<name>/<z>/<x>/<y>.png` | traffic tile (transparent if none); `?clean=1` for pure colours |
| `GET /api/model` | whether our derived data is loaded, and how much of it |
| `GET /model.geojson?part=major\|minor&v=<version>` | our data as gzipped vector features: `w` weight, `s` source, `c` coverage, `h` road class, `f` free-flow km/h, `o` reveal order. Cached for good when `v` names the served version; otherwise revalidated by ETag |
| `GET /api/layers` | available GeoJSON layers |
| `GET /layers/<id>` | one layer |

Only one server may hold the port: a second `python server.py` on a busy port now
refuses to start and tells you which process to stop. (On Windows it would otherwise
bind anyway and sit there silently while the old process kept answering.)

No API keys anywhere. Only Pillow and numpy are needed.
