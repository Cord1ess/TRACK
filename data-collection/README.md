# TRACK Data Collection

Keyless capture of Google's traffic layer over Dhaka. Google's tile server serves the
traffic overlay as its own transparent 256 px PNG tile when only that layer is asked
for, so a capture is plain HTTPS requests: no API key, no browser.

```
pip install -r requirements.txt
python tests/selftest.py --network             # proves every component + one live tile
python collector/capture.py --name my-capture  # whole metro area from config.json (~4,900 tiles, ~13 min)
python collector/capture.py --name small --bbox 23.765,23.745,90.405,90.385
python collector/reaudit.py --name my-capture  # after a palette change in config.json: rewrite stats, keep tiles
```

## Layout

```
config.json           bbox, zoom (17 = native max), rate, palette, thresholds
collector/
  grid.py             Web Mercator / tile math (single source of truth)
  fetch.py            the tile URL, polite limiter, strict PNG validation, retrying fetch
  tiles.py            coverage, per-tile audit (tiles.csv.gz), 16x16 block mosaics
  analyze.py          palette classification, traffic fraction, quantise
  checks.py           preflight + post-capture checks
  capture.py          one capture run -> captures/<name>/
  reaudit.py          recompute palette-dependent stats of an existing capture (no re-fetch)
storage/              optional remote copy (Hugging Face): upload.py, download.py
scheduler/            daily capture system (next phase; see README there)
tests/selftest.py
captures/
  test-capture-2026-09-12/   first full-city capture (taken ~1 AM Dhaka, sparse traffic)
```

## What a capture contains

```
captures/<name>/
  manifest.json      zoom, bbox, tile_range, coverage, validation stats, palette, checks, status
  tiles/z17_x_y.png  every validated tile (native data, exact position from x/y)
  blocks/            16x16-tile mosaics with bounds + sha256 (compact archive)
  tiles.csv.gz       one row per expected tile: bytes, sha256, opaque/traffic fraction
  preview_on_white.png, log.txt
```

## Failsafes in every run

| Stage | Check |
|---|---|
| preflight | config valid, bbox inside a Dhaka sanity box, disk space, grid math vs an independently computed anchor tile, name unused |
| per tile | accepted only if complete PNG (IEND present), decodes, exactly 256 px, transparent overlay; retries on HTTP 5xx/429, network errors and integrity failures |
| retry pass | every tile that failed the sweep is fetched again |
| gap-fill | empty tiles ringed by traffic are re-fetched and merged (`fill_gaps` in config) |
| post | coverage vs threshold, traffic present, coloured pixels still match the palette (warning below 80%: Google restyled), block bounds map back to tile origins, block counts reconcile, files re-read and re-hashed, manifest complete |

Status: `ok` complete and clean, `partial` usable but some tiles unrecoverable, `failed` unusable.

## Facts worth remembering

- Zoom 18 renders no traffic; zoom 17 is the ceiling. Only 256 px tiles are served.
- Residential lanes carry no traffic colour at any zoom: Google has no probe data there.
- Re-fetching a tile returns byte-identical content, so within one capture there is no flicker to recover; the failsafes guard against transport failures, not data variance.
- Rate 8 requests/second over a home connection showed zero throttling across 4,928 tiles.
- The capture area is the whole Dhaka metropolitan area: 23.902086/23.711736 N/S, 90.326774/90.499678 W/E,
  which is 64 x 77 = 4,928 tiles at zoom 17. `config.json` is the single source of truth for it;
  `algorithms/track_algos/graph/build_graph.py` carries the same box and must be changed with it.
- Time of day dominates how much traffic a capture contains. A 04:26 capture painted 972 of 4,928 tiles;
  a 01:10 one painted 1,159 of 2,380 over a smaller area. Capture during the day for dense data.
- Google's traffic palette, measured on the test capture (2.7 M opaque pixels): green #16e098 fill with #049c65 border, amber #ffcf43 / #f5c025, red #d1352b inside a white casing (a bordered variant #f24e42 / #98423a also occurs), dark red #a92727 / #702323. A pixel takes the class of its nearest reference colour within tolerance 50; the white casing never matches. `config.json`, `algorithms/track_algos/decode/palette.py` and `visualizer/server.py` carry the same values and must be changed together.
