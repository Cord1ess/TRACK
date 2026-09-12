"""RoadGraph: the directed road network every algorithm operates on.

Nodes are intersections (lon, lat). Edges are directed road segments with the
attributes the traffic engine needs: length, free-flow travel time, capacity.
A two-way street is two edges (u->v and v->u), which is what lets the decoder
keep separate congestion per direction and lets assignment load each
direction independently.

The JSON form is the file contract with the decoder and the visualizer:
    {"nodes": {id: [lon, lat]}, "edges": [{id, u, v, length_m, highway, lanes,
     oneway, capacity_vph, free_flow_kmph, free_flow_s, geometry: [[lon,lat],...]}]}
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..geo import polyline_length_m

# Practical per-lane capacity (vehicles/hour) and free-flow speed by OSM class.
# Standard planning defaults; Dhaka mixed traffic makes these rough, which is
# fine because BPR only needs volume/capacity as a ratio.
CLASS_DEFAULTS = {
    "motorway":      {"cap_per_lane": 1900, "speed": 80, "lanes": 3},
    "trunk":         {"cap_per_lane": 1600, "speed": 60, "lanes": 3},
    "primary":       {"cap_per_lane": 1200, "speed": 50, "lanes": 2},
    "secondary":     {"cap_per_lane": 1000, "speed": 40, "lanes": 2},
    "tertiary":      {"cap_per_lane": 800,  "speed": 35, "lanes": 2},
    "unclassified":  {"cap_per_lane": 600,  "speed": 30, "lanes": 1},
    "residential":   {"cap_per_lane": 500,  "speed": 25, "lanes": 1},
    "living_street": {"cap_per_lane": 300,  "speed": 15, "lanes": 1},
    "service":       {"cap_per_lane": 300,  "speed": 15, "lanes": 1},
}


CLASS_ORDER = list(CLASS_DEFAULTS)   # most important first; index = "how small is this road"


def class_defaults(highway: str) -> dict:
    base = highway.replace("_link", "")
    return CLASS_DEFAULTS.get(base, CLASS_DEFAULTS["unclassified"])


def class_rank(highway: str) -> int:
    """0 for a motorway up to 8 for a service road. A bigger rank means a
    smaller road, which is what lets the imputer ask "is this street smaller
    than the roads I am copying from?"."""
    base = (highway or "").replace("_link", "")
    return CLASS_ORDER.index(base) if base in CLASS_ORDER else CLASS_ORDER.index("unclassified")


@dataclass
class Edge:
    id: int
    u: int
    v: int
    length_m: float
    highway: str = "unclassified"
    lanes: int = 1
    oneway: bool = False
    capacity_vph: float = 600.0
    free_flow_kmph: float = 30.0
    free_flow_s: float = 0.0
    geometry: list = field(default_factory=list)  # [[lon, lat], ...] from u to v

    def __post_init__(self):
        if not self.free_flow_s:
            self.free_flow_s = self.length_m / (self.free_flow_kmph / 3.6)


class RoadGraph:
    def __init__(self):
        self.nodes: dict[int, tuple[float, float]] = {}
        self.edges: dict[int, Edge] = {}
        self.out: dict[int, list[int]] = {}   # node -> outgoing edge ids
        self._next_edge = 0

    # ---- building
    def add_node(self, nid: int, lon: float, lat: float) -> None:
        self.nodes[nid] = (lon, lat)
        self.out.setdefault(nid, [])

    def add_edge(self, u: int, v: int, geometry: list | None = None, highway: str = "unclassified",
                 lanes: int | None = None, oneway: bool = False, length_m: float | None = None,
                 capacity_vph: float | None = None, free_flow_kmph: float | None = None) -> Edge:
        geom = geometry or [list(self.nodes[u]), list(self.nodes[v])]
        d = class_defaults(highway)
        lanes = lanes or d["lanes"]
        e = Edge(id=self._next_edge, u=u, v=v,
                 length_m=length_m if length_m is not None else polyline_length_m(geom),
                 highway=highway, lanes=lanes, oneway=oneway,
                 capacity_vph=capacity_vph if capacity_vph is not None else d["cap_per_lane"] * lanes,
                 free_flow_kmph=free_flow_kmph or d["speed"], geometry=geom)
        self.edges[e.id] = e
        self.out.setdefault(u, []).append(e.id)
        self.out.setdefault(v, [])
        self._next_edge += 1
        return e

    def add_two_way(self, u: int, v: int, geometry: list | None = None, **kw) -> tuple[Edge, Edge]:
        geom = geometry or [list(self.nodes[u]), list(self.nodes[v])]
        return self.add_edge(u, v, geom, **kw), self.add_edge(v, u, list(reversed(geom)), **kw)

    # ---- queries
    def neighbors(self, u: int):
        """Yield (v, edge) for every outgoing edge of u."""
        for eid in self.out.get(u, []):
            e = self.edges[eid]
            yield e.v, e

    def nearest_node(self, lon: float, lat: float) -> int:
        from ..geo import haversine_m
        return min(self.nodes, key=lambda n: haversine_m(lon, lat, *self.nodes[n]))

    # ---- persistence
    def graph_id(self) -> str:
        """Short content fingerprint. Edge ids restart at 0 on every build, so a
        weight table from an older graph would line up numerically with a newer
        one and silently describe different roads. Everything derived from a
        graph records this id, and the visualizer refuses a mismatch."""
        h = hashlib.sha1(f"{len(self.nodes)}|{len(self.edges)}|".encode())
        for e in self.edges.values():
            h.update(f"{e.id}:{e.u}:{e.v}:{e.length_m:.1f};".encode())
        return h.hexdigest()[:12]

    def to_dict(self) -> dict:
        return {"graph_id": self.graph_id(),
                "nodes": {str(k): list(v) for k, v in self.nodes.items()},
                "edges": [asdict(e) for e in self.edges.values()]}

    @classmethod
    def from_dict(cls, d: dict) -> "RoadGraph":
        g = cls()
        for k, (lon, lat) in d["nodes"].items():
            g.add_node(int(k), lon, lat)
        for e in d["edges"]:
            edge = Edge(**e)
            g.edges[edge.id] = edge
            g.out.setdefault(edge.u, []).append(edge.id)
            g.out.setdefault(edge.v, [])
            g._next_edge = max(g._next_edge, edge.id + 1)
        return g

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict()), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "RoadGraph":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def to_geojson(self, edge_props: dict | None = None) -> dict:
        """Edges as a FeatureCollection; edge_props maps edge id -> extra properties
        (e.g. cls, color, width) so the visualizer can style them."""
        feats = []
        for e in self.edges.values():
            props = {"id": e.id, "highway": e.highway, "length_m": round(e.length_m, 1),
                     "capacity_vph": e.capacity_vph, "free_flow_s": round(e.free_flow_s, 1)}
            if edge_props and e.id in edge_props:
                props.update(edge_props[e.id])
            feats.append({"type": "Feature", "properties": props,
                          "geometry": {"type": "LineString", "coordinates": e.geometry}})
        return {"type": "FeatureCollection", "features": feats}

    def __len__(self):
        return len(self.edges)
