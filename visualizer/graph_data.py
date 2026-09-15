"""Serve the road graph itself: the junctions and the directed edges between them.

    GET /api/graph                  what is loaded, and how much of it
    GET /graph.geojson?part=nodes   junctions as points
    GET /graph.geojson?part=links   directed edges as lines, one feature per road

The road layers answer "how bad is the traffic". This answers "what does the
router actually walk": where the junctions are, how connected each one is, and
which way each edge runs. It is the same graph A* searches, so a wrong turn
restriction or an unreachable fragment is visible here before any route is run.

Junctions and cut points
------------------------
`build_graph.py` cuts a road at every intersection AND every 150 m, so a third
of the nodes are not junctions at all, just the points where a long road was
chopped up. They are given negative ids, and every one of them has exactly two
neighbours. Both facts are carried through so the map can hide them: drawn
together they double the apparent number of intersections in Dhaka.

Node features
    n  node id (negative = a cut point, not a real junction)
    d  neighbour count: 1 dead end, 2 pass-through, 3 T junction, 4+ crossroads
    x  1 when this is a cut point rather than an intersection
    r  rank of the most important road touching it, 0 motorway .. 7 living street

Link features carry one line per road segment, not per directed edge. A two-way
street is two edges on identical geometry, so drawing both put one line exactly
on top of the other and doubled the payload for nothing. The pair is merged and
the direction information kept:
    e  edge id           o  1 when the segment is one-way
    t  1 when two-way    r  road class rank
    l  length in metres
For a one-way segment the coordinate order IS the direction of travel, which is
what the arrows follow.
"""

import gzip
import hashlib
import json
import threading
import time
from collections import defaultdict
from pathlib import Path

CLASS_ORDER = ["motorway", "trunk", "primary", "secondary", "tertiary",
               "unclassified", "residential", "living_street", "service"]
COORD_DP = 5
MAJOR_MAX_RANK = 4          # motorway .. tertiary


def class_rank(highway: str) -> int:
    base = (highway or "").replace("_link", "")
    return CLASS_ORDER.index(base) if base in CLASS_ORDER else CLASS_ORDER.index("unclassified")


class GraphData:
    """The graph as map-ready features, rebuilt only when the graph file changes.

    Same contract as the traffic model service: once something good has been
    served, a request is never answered with nothing. A rebuild that fails
    leaves the published features exactly as they were."""

    RETRY_S = 5.0

    def __init__(self, graph_path: Path):
        self.graph_path = Path(graph_path)
        self.lock = threading.Lock()
        self.build_lock = threading.Lock()
        self.payloads: dict[str, bytes] = {}
        self.counts: dict = {}
        self.version = ""
        self.stamp = None
        self.error = ""
        self._failed = None

    @property
    def ready(self) -> bool:
        return bool(self.payloads)

    def _stamp(self):
        try:
            return self.graph_path.stat().st_mtime_ns
        except OSError:
            return None

    def ensure(self) -> bool:
        st = self._stamp()
        with self.lock:
            have, current = bool(self.payloads), self.stamp
            if st is not None and st == current:
                self.error = ""
        if st is None:
            with self.lock:
                self.error = "graph file missing" + ("; serving the last complete data" if have else "")
            return have
        if st == current:
            return have
        if self._failed and self._failed[0] == st and time.monotonic() - self._failed[1] < self.RETRY_S:
            return have
        with self.build_lock:
            with self.lock:
                if self.payloads and self.stamp == st:
                    return True
            try:
                payloads, counts = self._build()
                if self._stamp() != st:
                    raise RuntimeError("graph changed while being read; will retry")
                digest = hashlib.sha1()
                for part in ("nodes", "links"):
                    digest.update(payloads[part])
                with self.lock:
                    self.payloads, self.counts = payloads, counts
                    self.version, self.stamp, self.error = digest.hexdigest()[:12], st, ""
                self._failed = None
            except Exception as e:          # published features stay exactly as they were
                with self.lock:
                    self.error = f"{type(e).__name__}: {e}"
                self._failed = (st, time.monotonic())
        with self.lock:
            return bool(self.payloads)

    def _build(self) -> tuple[dict, dict]:
        g = json.loads(self.graph_path.read_text(encoding="utf-8"))
        nodes = g["nodes"]
        edges = g["edges"]

        neighbours = defaultdict(set)
        best_rank = {}
        for e in edges:
            u, v = e["u"], e["v"]
            neighbours[u].add(v)
            neighbours[v].add(u)
            rank = class_rank(e.get("highway", ""))
            for n in (u, v):
                if rank < best_rank.get(n, 99):
                    best_rank[n] = rank

        node_feats = []
        degrees = defaultdict(int)
        cut_points = 0
        for key, pos in nodes.items():
            nid = int(key)
            degree = len(neighbours.get(nid, ()))
            # a cut point is a synthetic split: negative id and exactly two neighbours
            cut = 1 if (nid < 0 and degree == 2) else 0
            cut_points += cut
            degrees[min(degree, 5)] += 1
            node_feats.append({
                "type": "Feature",
                "properties": {"n": nid, "d": degree, "x": cut, "r": best_rank.get(nid, 5)},
                "geometry": {"type": "Point",
                             "coordinates": [round(float(pos[0]), COORD_DP), round(float(pos[1]), COORD_DP)]},
            })

        # merge the two directions of a two-way street into one line
        seen: dict[tuple, dict] = {}
        order: list[tuple] = []
        for e in edges:
            geom = e.get("geometry") or []
            if len(geom) < 2:
                continue
            key = (min(e["u"], e["v"]), max(e["u"], e["v"]), len(geom))
            prev = seen.get(key)
            if prev is None:
                seen[key] = {"edge": e, "twoway": 0}
                order.append(key)
            else:
                prev["twoway"] = 1

        link_feats = []
        oneway = 0
        for key in order:
            rec = seen[key]
            e = rec["edge"]
            is_oneway = 0 if rec["twoway"] else (1 if e.get("oneway") else 0)
            oneway += is_oneway
            link_feats.append({
                "type": "Feature",
                "properties": {"e": int(e["id"]), "o": is_oneway, "t": rec["twoway"],
                               "r": class_rank(e.get("highway", "")),
                               "l": round(float(e.get("length_m", 0)), 1)},
                "geometry": {"type": "LineString",
                             "coordinates": [[round(float(x), COORD_DP), round(float(y), COORD_DP)]
                                             for x, y in e["geometry"]]},
            })

        if not node_feats or not link_feats:
            raise ValueError("graph has no nodes or no edges")

        payloads = {}
        for part, feats in (("nodes", node_feats), ("links", link_feats)):
            raw = json.dumps({"type": "FeatureCollection", "features": feats},
                             separators=(",", ":")).encode("utf-8")
            payloads[part] = gzip.compress(raw, 6, mtime=0)   # same graph, same bytes

        counts = {
            "graph_id": g.get("graph_id", ""),
            "nodes": len(node_feats), "links": len(link_feats), "edges": len(edges),
            "junctions": len(node_feats) - cut_points, "cut_points": cut_points,
            "oneway": oneway,
            "degree": {str(k): v for k, v in sorted(degrees.items())},
            "bytes": len(payloads["nodes"]) + len(payloads["links"]),
            "bytes_nodes": len(payloads["nodes"]),
        }
        return payloads, counts

    def payload(self, part: str = "nodes") -> tuple[bytes | None, str]:
        self.ensure()
        with self.lock:
            if not self.payloads:
                return None, ""
            return self.payloads.get(part if part in self.payloads else "nodes"), self.version

    def info(self) -> dict:
        ok = self.ensure()
        with self.lock:
            return {"ready": ok, "version": self.version, "stale": bool(ok and self.error),
                    "error": self.error, "graph": str(self.graph_path),
                    **(self.counts if ok else {})}


def discover(output_dir: Path) -> GraphData:
    return GraphData(Path(output_dir) / "graph" / "dhaka.json")
