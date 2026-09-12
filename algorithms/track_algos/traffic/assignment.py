"""Traffic assignment: the outer loop that makes the city "thin out".

Routing every trip on an empty network sends them all down the same fast
roads and simply moves the jam (Braess's paradox territory). Assignment fixes
that by feeding back the load:

    incremental assignment
        split total demand into batches (e.g. 10 x 10 %)
        for each batch:
            route every trip with A* on the CURRENT congested costs (BPR)
            add the batch's volume to the edges it used
            recompute every edge's BPR cost
        -> later batches see the roads earlier batches filled, and go around them

    method of successive averages (MSA)
        repeat: assign ALL demand on current costs (all-or-nothing), then blend
        volumes  v = (1 - 1/k) * v_prev + (1/k) * v_aon  at iteration k
        -> converges toward Wardrop user equilibrium, where no trip can lower its
           own time by switching route

Both call `astar` once per trip, so a run really is thousands of A* searches on
shifting costs. Every iteration is recorded as a frame (edge -> volume, v/c) so
the visualizer can animate the network going from red to balanced.
"""

from collections import Counter

from ..graph.road_graph import RoadGraph
from ..search.astar import astar, haversine_heuristic
from .bpr import bpr_time


class Assignment:
    def __init__(self, graph: RoadGraph, alpha: float = 0.15, beta: float = 4.0,
                 base_volume: dict | None = None):
        """base_volume: edge_id -> pre-existing volume (e.g. from decoded traffic,
        vc_ratio * capacity), so new trips are assigned on top of today's load."""
        self.g = graph
        self.alpha, self.beta = alpha, beta
        self.base = dict(base_volume or {})
        self.volume: dict[int, float] = dict(self.base)
        self.heuristic = haversine_heuristic(graph)
        self.frames: list[dict] = []
        self.searches = 0

    # ---- costs
    def cost(self, edge) -> float:
        return bpr_time(edge.free_flow_s, self.volume.get(edge.id, 0.0), edge.capacity_vph,
                        self.alpha, self.beta)

    def vc(self, eid: int) -> float:
        e = self.g.edges[eid]
        return self.volume.get(eid, 0.0) / e.capacity_vph if e.capacity_vph else float("inf")

    def snapshot(self, label: str) -> dict:
        vcs = {eid: round(self.vc(eid), 3) for eid in self.g.edges}
        loaded = [v for v in vcs.values() if v > 0]
        frame = {"label": label, "volume": {k: round(v, 1) for k, v in self.volume.items() if v > 0},
                 "vc": vcs, "searches": self.searches,
                 "stats": {"edges_loaded": len(loaded), "max_vc": round(max(loaded), 3) if loaded else 0.0,
                           "mean_vc_loaded": round(sum(loaded) / len(loaded), 3) if loaded else 0.0,
                           "over_capacity": sum(1 for v in loaded if v > 1.0),
                           "total_time_s": round(self.total_travel_time(), 1)}}
        self.frames.append(frame)
        return frame

    def total_travel_time(self) -> float:
        """Sum over edges of volume x congested time: the system-wide cost."""
        return sum(self.volume.get(eid, 0.0) * self.cost(e) for eid, e in self.g.edges.items())

    # ---- all-or-nothing on current costs
    def _assign_batch(self, trips: list[tuple], vehicles_per_trip: float) -> Counter:
        load = Counter()
        for o, d in trips:
            _, edges, _ = astar(self.g, o, d, self.cost, self.heuristic)
            self.searches += 1
            if edges:
                for eid in edges:
                    load[eid] += vehicles_per_trip
        return load

    # ---- incremental assignment
    def incremental(self, trips: list[tuple], batches: int = 10, vehicles_per_trip: float = 1.0) -> list[dict]:
        """trips: [(origin_node, dest_node)]. Returns the frames (one per batch)."""
        self.snapshot("before")
        n = len(trips)
        size = max(1, -(-n // batches))
        for i in range(0, n, size):
            load = self._assign_batch(trips[i:i + size], vehicles_per_trip)
            for eid, v in load.items():
                self.volume[eid] = self.volume.get(eid, 0.0) + v
            self.snapshot(f"batch {i // size + 1}")
        return self.frames

    # ---- MSA toward user equilibrium
    def msa(self, trips: list[tuple], iterations: int = 8, vehicles_per_trip: float = 1.0) -> list[dict]:
        self.snapshot("before")
        for k in range(1, iterations + 1):
            aon = self._assign_batch(trips, vehicles_per_trip)
            step = 1.0 / k
            new = {}
            for eid in set(self.volume) | set(aon):
                prev = self.volume.get(eid, 0.0) - self.base.get(eid, 0.0)
                new[eid] = self.base.get(eid, 0.0) + (1 - step) * prev + step * aon.get(eid, 0.0)
            self.volume = new
            self.snapshot(f"msa {k}")
        return self.frames

    # ---- export for the visualizer
    def frame_geojson(self, frame: dict) -> dict:
        from ..decode.palette import COLORS_HEX
        props = {}
        for eid, vc in frame["vc"].items():
            eid = int(eid)
            cls = 0 if vc <= 0 else 1 if vc < 0.45 else 2 if vc < 0.8 else 3 if vc < 1.1 else 4
            props[eid] = {"vc": vc, "cls": cls, "color": COLORS_HEX[cls], "width": 4 if cls else 1}
        gj = self.g.to_geojson(props)
        gj["properties"] = {"label": frame["label"], **frame["stats"]}
        return gj
