"""Read a capture of Google traffic tiles into a traffic weight per graph edge.

    python -m track_algos.decode.decoder --capture <dir> --graph output/graph/dhaka.json
        --out output/traffic/observed.csv [--geojson]

Every edge is sampled every step_m metres. Each sample steps sideways to the
left of travel (Bangladesh drives on the left, and Google draws each
direction's line on that side), reads a small window of pixels there and
classifies them. An edge's weight is the mean weight of its coloured samples;
its coverage is the share of samples that had any colour. Below min_coverage
the edge is reported as source=none, for impute.py to fill.

All sample points are projected at once and grouped by tile, so each tile is
opened exactly once.
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
    """The .meta.json beside a weight table: which graph its edge ids belong to."""
    p = Path(str(csv_path) + ".meta.json")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


class TileStore:
    """The tiles of one capture, opened on demand."""

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
        try:
            return np.asarray(Image.open(self.dir / "tiles" / f"z{self.zoom}_{tx}_{ty}.png").convert("RGBA"))
        except Exception:
            return None


def sample_points(graph: RoadGraph, step_m: float, trim_m: float = 10.0):
    """Points every step_m along every edge, with the travel direction.

    trim_m is skipped at each end so a side street's first samples do not
    read the bigger road it joins; an edge too short for that keeps its
    middle 40 %. Returns (edge_index, lon, lat, dlon, dlat, edge_ids).
    """
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
        lo = min(trim_m, 0.3 * total)
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
    """Class id (0..4) for every sample point, read from the tiles."""
    px, py = lonlat_to_pixel_np(lon, lat, store.zoom, store.tile_px)
    vx, vy = dlon, -dlat                      # travel direction on screen (y grows downward)
    norm = np.hypot(vx, vy)
    norm[norm == 0.0] = 1.0
    sx = px + (vy / norm) * offset_px         # rotate +90 degrees: left of travel
    sy = py - (vx / norm) * offset_px

    tp = store.tile_px
    tx = np.floor(sx / tp).astype(np.int64)
    ty = np.floor(sy / tp).astype(np.int64)
    ix = (np.floor(sx).astype(np.int64) - tx * tp).astype(np.int32)
    iy = (np.floor(sy).astype(np.int64) - ty * tp).astype(np.int32)

    cls = np.zeros(sx.size, dtype=np.int8)
    key = tx * (1 << 22) + ty                 # group the samples by tile
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
    """One row per edge: weight, coverage and colour mix."""
    store = TileStore(capture_dir)
    log(f"  capture z{store.zoom}, {len(store.available)} tiles, slot {store.slot_utc}")
    eidx, lon, lat, dlon, dlat, ids = sample_points(graph, step_m, trim_m)
    log(f"  {eidx.size} sample points along {ids.size} edges")
    cls = classify_samples(store, lon, lat, dlon, dlat, offset_px, win, log=log)

    ne = ids.size
    n_pts = np.bincount(eidx, minlength=ne).astype(float)
    coloured = cls > 0
    n_col = np.bincount(eidx[coloured], minlength=ne).astype(float)
    frac = {k: np.bincount(eidx[cls == k], minlength=ne) / np.maximum(n_pts, 1.0) for k in (1, 2, 3, 4)}
    coverage = n_col / np.maximum(n_pts, 1.0)

    # An observed road takes the colour Google drew on most of it, not the
    # average of its samples. Averaging invented values Google never showed: a
    # road drawn 85% green came out at 29.6, which is not a colour on the map
    # and put the road between two rungs. The dominant colour is a 1:1 copy of
    # what was painted, and the median edge has 100% of its coloured samples
    # agreeing, so this discards almost nothing.
    counts = np.stack([np.bincount(eidx[cls == k], minlength=ne) for k in (1, 2, 3, 4)], axis=1)
    # A tie goes to the worse colour, the same rule the spread uses. About 3 %
    # of painted roads tie, nearly all on two samples, one of each; breaking
    # them toward green turned 84 green-or-dark-red coin flips into "clear".
    dominant = 4 - counts[:, ::-1].argmax(axis=1)         # class id 1..4
    weight = np.where(n_col > 0, _WEIGHT_LUT[dominant], 0.0)
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
    """Write the table and, when meta is given, a .meta.json beside it.
    Columns beyond CSV_COLUMNS in the rows (impute adds `method`) are kept."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    extra = [k for k in (rows[0] if rows else {}) if k not in CSV_COLUMNS]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS + extra, extrasaction="ignore")
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
    """Edges as a styled FeatureCollection, optionally only rows of one source."""
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
    ap.add_argument("--trim-m", type=float, default=10.0, help="skip this much at each end of an edge")
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
