# How TRACK collects its data

## What we collect

Google Maps shows live traffic as coloured lines on the roads: green is free,
yellow is slow, red is jammed, dark red is stopped. There is no download for
it, so TRACK collects the picture itself and reads the colours.

## The file that does it

`data-collection/collector/capture.py`. One run captures the whole Dhaka metro
area and saves it as one folder, `captures/<name>/`.

It uses four helpers in the same folder:

| File | Job |
|---|---|
| `grid.py` | turns the map area into a list of tiles to fetch |
| `fetch.py` | downloads one tile and checks it is a real, complete image |
| `tiles.py` | keeps count of what arrived and packs the tiles into mosaics |
| `checks.py` | checks before the run (settings, disk, area) and after (coverage, colours, files) |

## How it works

1. Google's map server sends the map as small square images called tiles, 256 pixels each. If you ask it for only the traffic layer, it sends the traffic lines alone on a transparent background. No account and no API key are needed.
2. The Dhaka metro area at the closest useful zoom is 4,928 tiles. `capture.py` requests them 96 per second over a few open connections, so the download takes about 50 seconds.
3. Every tile is checked on arrival: complete file, correct size, transparent background. A bad one is fetched again. Empty tiles surrounded by traffic are fetched again too, in case they were missed.
4. At the end the run checks itself: did every tile arrive, is there traffic in the picture, do the colours still match Google's palette. It writes `manifest.json` with the answers and a status: `ok`, `partial` or `failed`.

## What comes out

```
captures/<name>/
  manifest.json      when, where, how many tiles, coverage, status
  tiles/             the 4,928 images
  tiles.csv.gz       one line per tile: size, checksum, how much traffic
```

The tiles are then read by the decoder in `algorithms/`, which walks every
road on the map and turns the colour it finds into a number.

## How often

A GitHub Actions workflow (`.github/workflows/collect.yml`) runs a capture
every 10 minutes, all day. Each finished capture is archived to a private
Hugging Face dataset and the website is updated with the new data.

## Things to know

- Zoom 17 is the most detailed level that shows traffic. Zoom 18 shows nothing.
- Google only paints the main roads. Small residential streets have no traffic colour at any zoom, because Google has no data for them. That is why the algorithms have to predict them.
- Time of day matters. A capture at 4 am painted 972 of 4,928 tiles; daytime captures are much denser.
- Google's terms do not allow storing their map tiles. The project owner accepted that risk for academic use.
