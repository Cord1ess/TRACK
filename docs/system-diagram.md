# TRACK system diagram

Draw a system diagram with these boxes and arrows. Left to right: Google,
GitHub Actions, storage, the website.

## Boxes

1. Google Maps tile server. Live traffic as small map images, no API key.
2. Collector. Downloads 4,928 tiles of Dhaka, about 1 minute.
3. Pipeline. Reads the tile colours onto a road graph, predicts the roads Google does not paint, runs the algorithms (A*, Dijkstra, K-means, KNN, logistic regression, traffic assignment) and writes one map layer each.
4. Site builder. Packs the map page and all data into static files.
5. Hugging Face dataset (private). Archive of every capture.
6. GitHub Pages. Hosts the site: https://cord1ess.github.io/TRACK/
7. Browser. The map: captured traffic, the model, the road graph, algorithm layers.

Boxes 2 to 4 run inside one GitHub Actions workflow.

## Arrows

- Google -> Collector: tiles
- Collector -> Pipeline: capture folder
- Pipeline -> Site builder: road weights and map layers
- Site builder -> GitHub Pages: static site
- Collector -> Hugging Face: capture archive
- Hugging Face -> Site builder: past captures, when a fresh deploy has none
- GitHub Pages -> Browser: page and data
- Browser -> GitHub Pages: checks for new data every 5 seconds

## Loop

The workflow takes about 7 minutes and starts itself again when it finishes.
