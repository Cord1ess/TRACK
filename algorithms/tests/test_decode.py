"""Decoder on a synthetic capture: paint a green traffic line exactly where a
graph edge runs (offset to the left of travel), decode, and expect class 1 with
high coverage; a parallel edge with no line must decode to class 0."""

import json
import math

import numpy as np
from PIL import Image, ImageDraw

from track_algos.decode.decoder import decode_capture, write_csv, write_geojson
from track_algos.decode.palette import classify
from track_algos.geo import lonlat_to_pixel, pixel_to_lonlat
from track_algos.graph.road_graph import RoadGraph

Z, TP = 17, 256


def test_palette_classify():
    px = np.array([[22, 224, 152, 255], [4, 156, 101, 255],      # green fill, green border
                   [255, 207, 67, 255], [209, 53, 43, 255], [169, 39, 39, 255],
                   [242, 78, 66, 255], [152, 66, 58, 255],        # bordered red variant: fill, border
                   [13, 190, 127, 255],                          # anti-aliased green blend
                   [232, 154, 149, 255], [212, 147, 147, 255],   # red / dark red half blended into white casing
                   [255, 255, 255, 255], [250, 242, 242, 255],   # white casing, nearly pure casing
                   [200, 200, 200, 255], [0, 0, 0, 0]], dtype=np.uint8)
    assert classify(px).tolist() == [1, 1, 2, 3, 4, 3, 3, 1, 3, 4, 0, 0, 0, 0]


def make_capture(tmp_path, edge_geom, offset_px=4):
    """One zoom-17 tile with a green line drawn along edge_geom, offset left."""
    (x0, y0) = [int(v // TP) for v in lonlat_to_pixel(*edge_geom[0], Z)]
    tile = Image.new("RGBA", (TP, TP), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    pts = []
    for lon, lat in edge_geom:
        px, py = lonlat_to_pixel(lon, lat, Z)
        pts.append((px - x0 * TP, py - y0 * TP))
    # offset the drawn line to the LEFT of travel (screen coords, y down)
    (ax, ay), (bx, by) = pts[0], pts[-1]
    vx, vy = bx - ax, by - ay
    n = math.hypot(vx, vy)
    lx, ly = vy / n * offset_px, -vx / n * offset_px
    d.line([(ax + lx, ay + ly), (bx + lx, by + ly)], fill=(22, 224, 152, 255), width=5)
    cap = tmp_path / "cap"
    (cap / "tiles").mkdir(parents=True)
    tile.save(cap / "tiles" / f"z{Z}_{x0}_{y0}.png")
    (cap / "manifest.json").write_text(json.dumps({"zoom": Z, "tile_px": TP, "captured_utc": "2026-01-01T00:00:00Z"}))
    return cap


def test_decoder_reads_line_on_edge(tmp_path):
    # an east-west edge fully inside one tile
    lon0, lat0 = pixel_to_lonlat(98451 * TP + 40, 56635 * TP + 128, Z)
    lon1, lat1 = pixel_to_lonlat(98451 * TP + 216, 56635 * TP + 128, Z)
    g = RoadGraph()
    g.add_node(0, lon0, lat0)
    g.add_node(1, lon1, lat1)
    g.add_edge(0, 1, highway="primary")                     # edge 0: has a line
    lon2, lat2 = pixel_to_lonlat(98451 * TP + 40, 56635 * TP + 60, Z)
    lon3, lat3 = pixel_to_lonlat(98451 * TP + 216, 56635 * TP + 60, Z)
    g.add_node(2, lon2, lat2)
    g.add_node(3, lon3, lat3)
    g.add_edge(2, 3, highway="primary")                     # edge 1: no line
    cap = make_capture(tmp_path, [[lon0, lat0], [lon1, lat1]])
    rows = decode_capture(cap, g, offset_px=4.0, step_m=2.0, win=1, log=lambda *a, **k: None)
    by = {r["edge_id"]: r for r in rows}
    assert by[0]["cls"] == 1 and by[0]["coverage"] > 0.8
    assert by[0]["weight"] == 25.0 and by[0]["vc_ratio"] == 0.25
    assert by[0]["source"] == "observed"
    assert by[1]["cls"] == 0 and by[1]["coverage"] == 0.0 and by[1]["source"] == "none"
    write_csv(rows, tmp_path / "out.csv")
    write_geojson(rows, g, tmp_path / "out.geojson")
    # the observed-only layer must hold exactly the edge that had a line under it
    assert write_geojson(rows, g, tmp_path / "obs.geojson", only_source="observed") == 1
    gj = json.loads((tmp_path / "out.geojson").read_text())
    assert {f["properties"]["cls"] for f in gj["features"]} == {0, 1}
    assert (tmp_path / "out.csv").read_text().startswith("slot_utc,edge_id,cls")
