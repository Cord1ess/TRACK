"""Serve TRACK's own traffic data to the map as vector features.

    GET /api/model                     what is loaded, and how much of it
    GET /model.geojson?part=major      motorway .. tertiary  (small, paints first)
    GET /model.geojson?part=minor      everything smaller    (the bulk)
    GET /model.geojson                 both at once

Why vectors and not rendered tiles
----------------------------------
The weights and the slowdown curve are things to tune, and tuning is only useful
if the map answers immediately. Rendered tiles cannot: every slider move would
invalidate the whole cache and re-draw thousands of lines server-side. Shipping
the geometry once lets the browser re-colour the whole city on the GPU as a
slider moves, and vector lines stay crisp at every zoom instead of resampling.

Two directions, one line
------------------------
The graph is directed, so a two-way street is two edges with identical geometry
running opposite ways. Drawn as they are they sit exactly on top of each other:
half the work for nothing, and whichever happens to draw last decides the colour.
So a pair becomes ONE feature carrying one direction's weight in `w` and the
other's in `wb`.

Which direction wins is not arbitrary. A MEASURED direction always beats a
predicted one, so a line Google painted is never overwritten by something we
inferred; the prediction is only ever the fallback for roads with no data at
all. Between two directions of the same kind the busier one wins, so congestion
is never hidden behind a clear direction.

Each feature carries the raw ingredients rather than a colour:

    i  edge id of the direction `w` came from
    o  reveal order, 0 on the arterial network rising to 1 far out in the
       side streets, so the map can grow the network outward rather than
       switching it on all at once
    w  weight on the default 25/55/85/105 scale (measured if either way was)
    j  edge id of the other direction
    wb that direction's weight, absent if one-way
    sb 1 when the other direction is predicted while this one was measured
    s  0 measured by Google, 1 predicted by us
    c  coverage (what fraction of the road Google painted)
    h  road class rank, 0 motorway .. 7 living street
    f  free-flow speed, km/h        (so the map can show effective speed)

The browser re-maps `w` through whatever weight values the user has dialled in,
runs BPR over it, and styles from the result.

Inputs are whatever `algorithms/` last produced, re-read automatically when they
change on disk:

    output/graph/dhaka.json        the road graph (geometry)
    output/traffic/complete.csv    weight + source per edge (falls back to observed.csv)
"""

import bisect
import csv
import gzip
import heapq
import json
import threading
from collections import defaultdict
from pathlib import Path

CLASS_ORDER = ["motorway", "trunk", "primary", "secondary", "tertiary",
               "unclassified", "residential", "living_street", "service"]
MAJOR_MAX_RANK = 4          # motorway .. tertiary
COORD_DP = 5                # ~1.1 m, finer than the traffic lines we decoded
REVEAL_SPAN_M = 1200.0      # distance from a main road at which reveal order reaches 1


def class_rank(highway: str) -> int:
    base = (highway or "").replace("_link", "")
    return CLASS_ORDER.index(base) if base in CLASS_ORDER else CLASS_ORDER.index("unclassified")


def reveal_order(edges: list, is_major) -> dict:
    """Road-network distance in metres from the nearest arterial road, per node.

    A multi-source Dijkstra seeded at every node that touches a major road, so
    the value grows as you walk away from the main network. That is the order
    the map reveals in: arteries first, then the streets hanging off them, then
    the streets hanging off those."""
    adj = defaultdict(list)
    seeds = set()
    for e in edges:
        u, v, w = e["u"], e["v"], max(float(e.get("length_m", 0.0)), 1.0)
        adj[u].append((v, w))
        adj[v].append((u, w))
        if is_major(e):
            seeds.add(u)
            seeds.add(v)
    dist = {n: 0.0 for n in seeds}
    heap = [(0.0, n) for n in seeds]
    heapq.heapify(heap)
    while heap:
        d, n = heapq.heappop(heap)
        if d > dist.get(n, float("inf")):
            continue
        if d >= REVEAL_SPAN_M:                     # past the span, ordering stops mattering
            continue
        for m, w in adj[n]:
            nd = d + w
            if nd < dist.get(m, float("inf")):
                dist[m] = nd
                heapq.heappush(heap, (nd, m))
    return dist


class ModelData:
    """The graph plus the weight table, kept as ready-to-send payloads."""

    def __init__(self, graph_path: Path, csv_path: Path):
        self.graph_path, self.csv_path = Path(graph_path), Path(csv_path)
        self.stamp = None
        self.lock = threading.Lock()
        self.ready = False
        self.error = ""
        self.counts = {}
        self.payloads: dict[str, bytes] = {}

    def _stamp(self):
        try:
            return (self.graph_path.stat().st_mtime_ns, self.csv_path.stat().st_mtime_ns)
        except OSError:
            return None

    def ensure(self) -> bool:
        st = self._stamp()
        if st is None:
            self.ready, self.error = False, "graph or weights file missing"
            return False
        with self.lock:
            if st == self.stamp and self.ready:
                return True
            try:
                self._load()
                self.stamp = st
                self.ready, self.error = True, ""
            except Exception as e:          # a half-written file during a pipeline run
                self.ready, self.error = False, f"{type(e).__name__}: {e}"
            return self.ready

    def _check_pairing(self, g: dict, rows: dict) -> None:
        """Edge ids restart at 0 on every graph build, so a weight table from an
        older graph lines up numerically and would paint the wrong roads.
        Refuse rather than draw."""
        gid = g.get("graph_id")
        meta = {}
        try:
            meta = json.loads(Path(str(self.csv_path) + ".meta.json").read_text(encoding="utf-8"))
        except Exception:
            pass
        if gid and meta.get("graph_id") and meta["graph_id"] != gid:
            raise ValueError(f"{self.csv_path.name} belongs to graph {meta['graph_id']}, but the "
                             f"graph on disk is {gid}. Re-run the pipeline.")
        if not meta.get("graph_id") and len(rows) != len(g["edges"]):
            raise ValueError(f"{self.csv_path.name} has {len(rows)} rows but the graph has "
                             f"{len(g['edges'])} edges. Re-run the pipeline.")

    def _load(self) -> None:
        g = json.loads(self.graph_path.read_text(encoding="utf-8"))
        rows = {}
        with self.csv_path.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[int(r["edge_id"])] = r
        self._check_pairing(g, rows)

        order_from = reveal_order(g["edges"],
                                  lambda e: class_rank(e.get("highway", "")) <= MAJOR_MAX_RANK)

        # collapse each two-way pair into one drawn line, keeping both weights
        pairs: dict[tuple, dict] = {}
        order: list[tuple] = []
        for e in g["edges"]:
            eid = int(e["id"])
            r = rows.get(eid)
            geom = e["geometry"]
            if r is None or len(geom) < 2:
                continue
            key = (min(e["u"], e["v"]), max(e["u"], e["v"]), len(geom))
            far = min(order_from.get(e["u"], REVEAL_SPAN_M),
                      order_from.get(e["v"], REVEAL_SPAN_M))
            rec = {"id": eid, "w": float(r["weight"]),
                   "obs": r.get("source", "observed") == "observed",
                   "c": float(r.get("coverage", 0) or 0), "geom": geom,
                   "h": class_rank(e.get("highway", "")),
                   "f": float(e.get("free_flow_kmph", 30) or 30), "far": far}
            prev = pairs.get(key)
            if prev is None:
                pairs[key] = rec
                order.append(key)
            # measured beats predicted; between equals, the busier one wins
            elif (rec["obs"], rec["w"]) > (prev["obs"], prev["w"]):
                rec["other"] = prev
                pairs[key] = rec
            else:
                prev["other"] = rec

        # Rank the side streets by how far out they are rather than using the raw
        # distance: two thirds of them sit within a couple of hundred metres of a
        # main road, so the raw value would reveal most of the city in the first
        # moment and then trickle. Ranking makes the wave sweep at a steady rate.
        minor_far = sorted(pairs[k]["far"] for k in order
                           if pairs[k]["h"] > MAJOR_MAX_RANK)
        span = max(len(minor_far) - 1, 1)

        feats_major, feats_minor = [], []
        observed = 0
        for key in order:
            rec = pairs[key]
            other = rec.get("other")
            # rec is the measured direction whenever either direction was measured
            observed += rec["obs"]
            props = {
                "i": rec["id"], "w": round(rec["w"], 1), "s": 0 if rec["obs"] else 1,
                "c": round(max(rec["c"], other["c"] if other else 0), 2),
                "h": rec["h"], "f": round(rec["f"], 1),
                "o": 0.0 if rec["h"] <= MAJOR_MAX_RANK
                     else round(bisect.bisect_left(minor_far, rec["far"]) / span, 3),
            }
            if other:
                props["j"] = other["id"]
                props["wb"] = round(other["w"], 1)
                if rec["obs"] and not other["obs"]:
                    props["sb"] = 1
            feat = {"type": "Feature", "properties": props, "geometry": {
                "type": "LineString",
                "coordinates": [[round(float(x), COORD_DP), round(float(y), COORD_DP)]
                                for x, y in rec["geom"]]}}
            (feats_major if rec["h"] <= MAJOR_MAX_RANK else feats_minor).append(feat)

        if not order:
            raise ValueError("no edge ids in common between the graph and the weight table")

        self.payloads = {}
        for part, feats in (("major", feats_major), ("minor", feats_minor),
                            ("all", feats_major + feats_minor)):
            raw = json.dumps({"type": "FeatureCollection", "features": feats},
                             separators=(",", ":")).encode("utf-8")
            self.payloads[part] = gzip.compress(raw, 6)

        assert all(pairs[k]["obs"] or not pairs[k].get("other", {}).get("obs") for k in order), \
            "a predicted direction outranked a measured one"
        self.counts = {
            "edges": len(g["edges"]), "lines": len(order),
            "observed": observed, "predicted": len(order) - observed,
            "major": len(feats_major), "minor": len(feats_minor),
            "weights": self.csv_path.name,
            "bytes": len(self.payloads["all"]),
            "bytes_major": len(self.payloads["major"]),
        }

    def geojson(self, part: str = "all") -> bytes | None:
        if not self.ensure():
            return None
        return self.payloads.get(part if part in self.payloads else "all")

    def info(self) -> dict:
        ok = self.ensure()
        return {"ready": ok, "error": self.error, "graph": str(self.graph_path),
                "weights_path": str(self.csv_path), **(self.counts if ok else {})}


def discover(output_dir: Path) -> ModelData:
    """Point at whatever the algorithms folder has produced, preferring the
    complete (observed + predicted) table over the observed-only one."""
    out = Path(output_dir)
    csv_path = out / "traffic" / "complete.csv"
    if not csv_path.exists():
        csv_path = out / "traffic" / "observed.csv"
    return ModelData(out / "graph" / "dhaka.json", csv_path)
