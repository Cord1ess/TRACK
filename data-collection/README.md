# TRACK data collection

Capture of Google's traffic layer over Dhaka, without an API key. Google's tile
server returns the traffic overlay as its own transparent 256 px PNG tile when
only that layer is asked for, so a capture is plain HTTPS requests.

```
pip install -r requirements.txt
python tests/selftest.py --network             # checks every component and one live tile
python collector/capture.py --name my-capture  # the whole metro area from config.json: 4,928 tiles, about 1 minute
python collector/capture.py --name small --bbox 23.765,23.745,90.405,90.385
python collector/reaudit.py --name my-capture  # after a palette change in config.json: rewrite the stats, keep the tiles
```

## Layout

```
config.json          bbox, zoom (17 is the native maximum), rate, palette, thresholds
collector/
  grid.py            Web Mercator and tile maths
  fetch.py           the tile URL, rate limiter, strict PNG validation, retrying fetch
  tiles.py           coverage and the per-tile audit
  analyze.py         palette classification, traffic fraction
  checks.py          checks before and after a capture
  capture.py         one capture run -> captures/<name>/
  reaudit.py         recompute palette-dependent stats of an existing capture
storage/             upload.py, download.py and migrate.py: a private Hugging Face dataset
                     is where captures live. Two files per capture (manifest.json and
                     capture.tar.gz, about 18.5 MB: the tiles plus the visualizer's
                     tile caches, which cost 95 s a capture to rebuild and seconds
                     to download)
scheduler/           daily_check.py: health report over the archive
tests/selftest.py
captures/            not committed, and normally empty: a capture is archived and the
                     local copy removed. Workflows fetch back the newest few to build the
                     site; `storage/download.py --to captures --latest 3` does the same
                     locally.
```

The scheduled capture is the GitHub Actions workflow in
`.github/workflows/collect.yml`, every 10 minutes. See the root README.

## What a capture contains

```
captures/<name>/
  manifest.json      zoom, bbox, tile_range, coverage, validation stats, palette, checks,
                     status, seconds per stage
  tiles/z17_x_y.png  every validated tile
  tiles.csv.gz       one row per expected tile: bytes, sha256, opaque and traffic fraction
  log.txt
```

Status: `ok` complete and clean, `partial` usable but some tiles unrecoverable,
`failed` unusable. `capture.py` exits 0 for ok or partial, 2 for failed, so the
workflow stops on a bad capture.

## Checks in every run

| Stage | Check |
|---|---|
| before | config valid, bbox inside a Dhaka sanity box, disk space, grid maths against an independently computed tile, name unused |
| per tile | accepted only if a complete PNG that decodes to exactly 256 px and is a transparent overlay; retries on HTTP 5xx and 429, network errors and integrity failures |
| retry pass | every tile that failed the sweep is fetched again |
| gap fill | empty tiles ringed by traffic are fetched again and merged |
| after | coverage against the threshold, traffic present, coloured pixels still match the palette (warning below 80 %: Google restyled), tile bounds map back to tile coordinates, every tile re-read from disk and compared with what was fetched, no error response that cost a tile, manifest complete |

## Facts

- A capture archives its tile caches rather than rebuilding them. Measured: 18.5 MB stored against 2.9, and a twelve-capture site build drops from about 14 minutes to about 4. At 144 captures a day the archive grows roughly 2.7 GB a day, so the 100 GB tier lasts about five weeks; `scheduler/daily_check.py` prints the remaining headroom every morning.
- Zoom 18 renders no traffic; 17 is the ceiling. Only 256 px tiles are served.
- Residential lanes carry no traffic colour at any zoom. Google has no probe data there.
- Fetching a tile again returns identical bytes, so there is no flicker to recover within a capture. The checks guard against transport failures.
- A rate test from a home connection got clean responses at every step up to about 145 requests a second sustained, with no rise in latency, so the limit was not found. The collector runs at 96 a second with one open connection per worker, which fetches 4,928 tiles in about 50 seconds, and halves its rate for the rest of the run on a 429 or 503 (`rate_final` and `slow_downs` in the manifest). GitHub Actions shares its addresses with many users; if Google throttles there, the run slows down instead of failing.
- The capture area is the whole Dhaka metro area: 23.902086 to 23.711736 N, 90.326774 to 90.499678 E, 64 by 77 = 4,928 tiles at zoom 17. `config.json` holds it; `algorithms/track_algos/graph/build_graph.py` carries the same box and must change with it.
- Time of day decides how much traffic a capture holds. A 04:26 capture painted 972 of 4,928 tiles. Capture during the day for dense data.
- Google's palette, measured on 2.7 million opaque pixels: green #16e098 with border #049c65, amber #ffcf43 and #f5c025, red #d1352b inside a white casing (a bordered variant #f24e42 and #98423a also occurs), dark red #a92727 and #702323. A pixel takes the class of its nearest reference colour within tolerance 50. `config.json`, `algorithms/track_algos/decode/palette.py` and `visualizer/server.py` carry the same values and must change together.
