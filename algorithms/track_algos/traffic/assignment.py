"""Traffic assignment: route every trip on congested costs and feed the load back.

incremental: split the demand into batches. Route each batch with A* on the
current BPR costs, add its volume to the edges it used, recompute the costs.
Later batches go around the roads earlier ones filled.

msa: assign all demand on the current costs, then blend the volumes,
v = (1 - 1/k) v_prev + (1/k) v_new at iteration k. Converges toward user
equilibrium, where no trip can lower its own time by changing route.

Every iteration is recorded as a frame (edge -> volume, v/c) for the visualizer.
"""

from collections import Counter

from ..decode.palette import COLORS_HEX
from ..graph.road_graph import RoadGraph
from ..search.astar import astar, haversine_heuristic
from .bpr import ALPHA, BETA, bpr_time


class Assignment:
    def __init__(self, graph: RoadGraph, alpha: float = ALPHA, beta: float = BETA,
                 base_volume: dict | None = None):
        """base_volume: edge_id -> volume already on the road, so new trips
        are assigned on top of today's traffic."""
        self.g = graph
        self.alpha, self.beta = alpha, beta
        self.base = dict(base_volume or {})
        self.volume: dict[int, float] = dict(self.base)
        self.heuristic = haversine_heuristic(graph)
        self.frames: list[dict] = []
        self.searches = 0

    def cost(self, edge) -> float:
        return bpr_time(edge.free_flow_s, self.volume.get(edge.id, 0.0), edge.capacity_vph,
                        self.alpha, self.beta)

    def vc(self, eid: int) -> float:
        e = self.g.edges[eid]
        return self.volume.get(eid, 0.0) / e.capacity_vph if e.capacity_vph else float("inf")

    def total_travel_time(self) -> float:
        """Sum over edges of volume times congested time."""
        return sum(self.volume.get(eid, 0.0) * self.cost(e) for eid, e in self.g.edges.items())

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

    def _assign_batch(self, trips: list[tuple], vehicles_per_trip: float) -> Counter:
        """Route every trip on the current costs; return the load per edge."""
        load = Counter()
        for o, d in trips:
            _, edges, _ = astar(self.g, o, d, self.cost, self.heuristic)
            self.searches += 1
            for eid in edges or ():
                load[eid] += vehicles_per_trip
        return load

    def all_at_once(self, trips: list[tuple], vehicles_per_trip: float = 1.0) -> dict:
        """Every trip on the current costs with no feedback: the naive baseline."""
        for eid, v in self._assign_batch(trips, vehicles_per_trip).items():
            self.volume[eid] = self.volume.get(eid, 0.0) + v
        return self.snapshot("all at once")

    def incremental(self, trips: list[tuple], batches: int = 10, vehicles_per_trip: float = 1.0) -> list[dict]:
        """trips: [(origin_node, dest_node)]. Returns one frame per batch."""
        self.snapshot("before")
        size = max(1, -(-len(trips) // batches))
        for i in range(0, len(trips), size):
            for eid, v in self._assign_batch(trips[i:i + size], vehicles_per_trip).items():
                self.volume[eid] = self.volume.get(eid, 0.0) + v
            self.snapshot(f"batch {i // size + 1}")
        return self.frames

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

    def frame_geojson(self, frame: dict) -> dict:
        """One frame as a styled FeatureCollection."""
        props = {}
        for eid, vc in frame["vc"].items():
            eid = int(eid)
            cls = 0 if vc <= 0 else 1 if vc < 0.45 else 2 if vc < 0.8 else 3 if vc < 1.1 else 4
            props[eid] = {"vc": vc, "cls": cls, "color": COLORS_HEX[cls], "width": 4 if cls else 1}
        gj = self.g.to_geojson(props)
        gj["properties"] = {"label": frame["label"], **frame["stats"]}
        return gj
