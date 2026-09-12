"""Turn a capture of Google traffic tiles into a traffic weight per graph edge.

    python -m track_algos.decode.decoder --capture <dir> --graph output/graph/dhaka.json
        --out output/traffic/observed.csv [--geojson] [--step-m 5] [--offset-px 4]

How it works
------------
For every edge, walk along its geometry taking a sample every `step_m` metres.
Step sideways from each sample to find the pixel Google actually painted, read
a small window of pixels there, and classify them against the measured palette.
The edge's weight is the mean weight of its coloured samples; its coverage is
the fraction of samples that had any colour at all.

The sideways step is to the LEFT of travel. Bangladesh drives on the left, and
Google draws each direction's traffic line on the side of the road that
direction uses, so a two-way street shows two lines and each directed edge must
read its own. Get the side wrong and every edge reads its opposite direction's
traffic.

Coverage is the honesty column. Google paints nothing on most residential
streets, and it leaves gaps at junctions even on roads it does cover. An edge
below `--min-coverage` is reported as `source=none`: we did not observe it, and
impute.py will predict it rather than pretend it was free.

Scale
-----
76 k edges at a 5 m step is about 1.5 M sample points, so the work is done on
whole arrays: every sample point is projected at once, grouped by the tile it
falls in, and each tile is opened exactly once.
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from ..geo import haversine_m_np, lonlat_to_pixel_np
from ..graph.road_graph import RoadGraph
from ..traffic.weights import WEIGHT, to_class, to_hex_array
from .palette import classify

_WEIGHT_LUT = np.asarray([WEIGHT[k] for k in (0, 1, 2, 3, 4)], dtype=float)
CSV_COLUMNS = ["slot_utc", "edge_id", "cls", "weight", "f_green", "f_orange", "f_red",
               "f_darkred", "coverage", "vc_ratio", "n_samples", "source"]


def read_meta(csv_path) -> dict:
    """The sidecar written beside a weight table: which graph its ids belong to."""
    p = Path(str(csv_path) + ".meta.json")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


class TileStore:
    """The tiles of one capture, opened on demand and kept as arrays."""

    def __init__(self, capture_dir: Path):
        self.dir = Path(capture_dir)
        m = json.loads((self.dir / "manifest.json").read_text(encoding="utf-8"))
        self.zoom = int(m["zoom"])
        self.tile_px = int(m.get("tile_px", 256))
        self.slot_utc = m.get("captured_utc", "")
        self.available = set()
        rx = re.compile(r"^z(\d+)_(\d+)_(\d+)\.png$")
        for p in (self.dir / "tiles").glob("*.png"):
            g = rx.match(p.name)
            if g and int(g.group(1)) == self.zoom:
                self.available.add((int(g.group(2)), int(g.group(3))))

    def array(self, tx: int, ty: int):
        """(tile_px, tile_px, 4) uint8, or None when the tile is not in the capture."""
        if (tx, ty) not in self.available:
            return None
        p = self.dir / "tiles" / f"z{self.zoom}_{tx}_{ty}.png"
        try:
            return np.asarray(Image.open(p).convert("RGBA"))
        except Exception:
            return None


def sample_points(graph: RoadGraph, step_m: float, trim_m: float = 10.0):
    """For every edge, points every step_m along it with the travel direction.

    `trim_m` is skipped at each end. Where two roads meet, the bigger road's
    traffic line passes within a few metres of the smaller road's first sample,
    so without trimming every side street inherits the arterial's colour for its
    first stretch and a quiet lane reads as a jam. On an edge too short to trim
    that much, the middle 40 % is sampled instead, so no edge is ever skipped.

    Returns flat arrays (edge_index, lon, lat, dlon, dlat) plus the edge ids."""
    ids, eidx, lons, lats, dlons, dlats = [], [], [], [], [], []
    for i, e in enumerate(graph.edges.values()):
        ids.append(e.id)
        g = np.asarray(e.geometry, dtype=float)
        if g.shape[0] < 2:
            g = np.asarray([e.geometry[0], e.geometry[0]], dtype=float)
        seg = g[1:] - g[:-1]
        seglen = haversine_m_np(g[:-1, 0], g[:-1, 1], g[1:, 0], g[1:, 1])
        cum = np.concatenate([[0.0], np.cumsum(seglen)])
        total = float(cum[-1])
        lo = min(trim_m, 0.3 * total)               # short edges: keep the middle 40 %
        span = max(total - 2.0 * lo, 0.0)
        n = max(2, int(span // step_m) + 1)
        s = np.linspace(lo, total - lo, n)
        j = np.clip(np.searchsorted(cum, s, side="right") - 1, 0, len(seg) - 1)
        denom = np.where(seglen[j] == 0.0, 1.0, seglen[j])
        t = ((s - cum[j]) / denom)[:, None]
        pts = g[j] + seg[j] * t
        eidx.append(np.full(n, i, dtype=np.int32))
        lons.append(pts[:, 0]); lats.append(pts[:, 1])
        dlons.append(seg[j][:, 0]); dlats.append(seg[j][:, 1])
    return (np.concatenate(eidx), np.concatenate(lons), np.concatenate(lats),
            np.concatenate(dlons), np.concatenate(dlats), np.asarray(ids, dtype=np.int64))


def classify_samples(store: TileStore, lon, lat, dlon, dlat, offset_px: float, win: int,
                     log=print) -> np.ndarray:
    """Class id (0..4) for every sample point, read from the capture's tiles."""
    px, py = lonlat_to_pixel_np(lon, lat, store.zoom, store.tile_px)
    # travel direction in pixel space (y grows downward, so north is -y)
    vx, vy = dlon, -dlat
    norm = np.hypot(vx, vy)
    norm[norm == 0.0] = 1.0
    # left of travel on screen = rotate the direction by +90 degrees
    sx = px + (vy / norm) * offset_px
    sy = py - (vx / norm) * offset_px

    tp = store.tile_px
    tx = np.floor(sx / tp).astype(np.int64)
    ty = np.floor(sy / tp).astype(np.int64)
    ix = (np.floor(sx).astype(np.int64) - tx * tp).astype(np.int32)
    iy = (np.floor(sy).astype(np.int64) - ty * tp).astype(np.int32)

    cls = np.zeros(sx.size, dtype=np.int8)
    key = tx * (1 << 22) + ty
    order = np.argsort(key, kind="stable")
    bounds = np.flatnonzero(np.concatenate([[True], key[order][1:] != key[order][:-1], [True]]))
    offsets = [(dx, dy) for dy in range(-win, win + 1) for dx in range(-win, win + 1)]
    done = 0
    for b0, b1 in zip(bounds[:-1], bounds[1:]):
        idx = order[b0:b1]
        arr = store.array(int(tx[idx[0]]), int(ty[idx[0]]))
        done += 1
        if done % 400 == 0:
            log(f"    {done} tiles read")
        if arr is None:
            continue
        counts = np.zeros((idx.size, 4), dtype=np.int16)
        for dx, dy in offsets:
            yy = np.clip(iy[idx] + dy, 0, tp - 1)
            xx = np.clip(ix[idx] + dx, 0, tp - 1)
            c = classify(arr[yy, xx])
            for k in (1, 2, 3, 4):
                counts[:, k - 1] += (c == k)
        best = counts.argmax(axis=1) + 1
        cls[idx] = np.where(counts.max(axis=1) > 0, best, 0).astype(np.int8)
    return cls


def decode_capture(capture_dir, graph: RoadGraph, offset_px: float = 4.0, step_m: float = 5.0,
                   win: int = 1, min_coverage: float = 0.25, trim_m: float = 10.0,
                   log=print) -> list[dict]:
    """One row per edge: its weight, coverage and colour mix."""
    store = TileStore(capture_dir)
    log(f"  capture z{store.zoom}, {len(store.available)} tiles, slot {store.slot_utc}")
    eidx, lon, lat, dlon, dlat, ids = sample_points(graph, step_m, trim_m)
    log(f"  {eidx.size} sample points along {ids.size} edges")
    cls = classify_samples(store, lon, lat, dlon, dlat, offset_px, win, log=log)

    ne = ids.size
    n_pts = np.bincount(eidx, minlength=ne).astype(float)
    coloured = cls > 0
    n_col = np.bincount(eidx[coloured], minlength=ne).astype(float)
    wsum = np.bincount(eidx[coloured], weights=_WEIGHT_LUT[cls[coloured]], minlength=ne)
    frac = {k: np.bincount(eidx[cls == k], minlength=ne) / np.maximum(n_pts, 1.0) for k in (1, 2, 3, 4)}
    coverage = n_col / np.maximum(n_pts, 1.0)
    weight = np.where(n_col > 0, wsum / np.maximum(n_col, 1.0), 0.0)
    observed = coverage >= min_coverage

    rows = []
    for i in range(ne):
        w = float(weight[i]) if observed[i] else 0.0
        rows.append({
            "slot_utc": store.slot_utc, "edge_id": int(ids[i]),
            "cls": to_class(w) if observed[i] else 0, "weight": round(w, 2),
            "f_green": round(float(frac[1][i]), 4), "f_orange": round(float(frac[2][i]), 4),
            "f_red": round(float(frac[3][i]), 4), "f_darkred": round(float(frac[4][i]), 4),
            "coverage": round(float(coverage[i]), 4), "vc_ratio": round(w / 100.0, 4),
            "n_samples": int(n_pts[i]), "source": "observed" if observed[i] else "none",
        })
    n_obs = int(observed.sum())
    log(f"  observed {n_obs}/{ne} edges ({100.0 * n_obs / ne:.1f}%) at coverage >= {min_coverage}")
    return rows


def write_csv(rows: list[dict], path, meta: dict | None = None) -> None:
    """Write the table, and beside it a small `.meta.json` recording which graph
    the edge ids belong to. Anything reading the table can then refuse to pair
    it with a different graph."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    if meta is not None:
        path.with_suffix(path.suffix + ".meta.json").write_text(
            json.dumps({"rows": len(rows), **meta}, indent=1), encoding="utf-8")


def read_csv(path) -> list[dict]:
    with Path(path).open(encoding="utf-8") as f:
        out = []
        for r in csv.DictReader(f):
            for k in ("edge_id", "cls", "n_samples"):
                r[k] = int(r[k])
            for k in ("weight", "f_green", "f_orange", "f_red", "f_darkred", "coverage", "vc_ratio"):
                r[k] = float(r[k])
            out.append(r)
    return out


def write_geojson(rows: list[dict], graph: RoadGraph, path, only_source: str | None = None) -> int:
    """Edges as a styled FeatureCollection for the visualizer. `only_source`
    keeps just the rows with that source, so observed and predicted data can be
    toggled as separate layers and compared."""
    keep = [r for r in rows if only_source is None or r["source"] == only_source]
    colours = to_hex_array([r["weight"] for r in keep]) if keep else []
    props = {}
    for r, hexcol in zip(keep, colours):
        props[r["edge_id"]] = {
            "cls": r["cls"], "weight": r["weight"], "color": hexcol,
            "coverage": r["coverage"], "source": r["source"],
            "width": 2.0 + 2.5 * (r["weight"] - 25.0) / 80.0,
        }
    gj = graph.to_geojson(props)
    gj["features"] = [f for f in gj["features"] if f["properties"]["id"] in props]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(gj), encoding="utf-8")
    return len(gj["features"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capture", type=Path, required=True)
    ap.add_argument("--graph", type=Path, default=Path("output/graph/dhaka.json"))
    ap.add_argument("--out", type=Path, default=Path("output/traffic/observed.csv"))
    ap.add_argument("--offset-px", type=float, default=4.0)
    ap.add_argument("--step-m", type=float, default=5.0)
    ap.add_argument("--win", type=int, default=1)
    ap.add_argument("--min-coverage", type=float, default=0.25)
    ap.add_argument("--trim-m", type=float, default=10.0,
                    help="skip this much at each end of an edge (junction bleed guard)")
    ap.add_argument("--geojson", action="store_true")
    args = ap.parse_args()

    graph = RoadGraph.load(args.graph)
    gid = graph.graph_id()
    print(f"graph: {len(graph.edges)} edges, id {gid}")
    rows = decode_capture(args.capture, graph, args.offset_px, args.step_m, args.win,
                          args.min_coverage, args.trim_m)
    write_csv(rows, args.out, {"graph_id": gid, "capture": args.capture.name,
                               "offset_px": args.offset_px, "step_m": args.step_m,
                               "trim_m": args.trim_m, "min_coverage": args.min_coverage})
    print(f"  -> {args.out}")
    if args.geojson:
        gj = args.out.with_suffix(".geojson")
        n = write_geojson(rows, graph, gj, only_source="observed")
        print(f"  {n} observed edges -> {gj}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
