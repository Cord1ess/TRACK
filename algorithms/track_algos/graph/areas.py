"""Named areas of Dhaka, the roads inside them, and the roads' street names.

The centres are taken from OpenStreetMap's own named features, not from memory:

    Farmgate    the ways named "Foot over Bridge, Farmgate",  23.7577 N, 90.3903 E
    Mohakhali   the ways named "Mohakhali Foot Over Bridge",  23.7776 N, 90.3983 E

Two circles are drawn around each centre:

    CHANGE_RADIUS_M  roads inside it may be changed by the road-change planner
    STUDY_RADIUS_M   roads inside it are simulated, so traffic pushed off a
                     changed road has somewhere to go around it
"""

import json
from pathlib import Path

from ..geo import haversine_m
from .road_graph import Edge, RoadGraph

AREAS = {
    "farmgate":  {"name": "Farmgate",  "lon": 90.3903, "lat": 23.7577},
    "mohakhali": {"name": "Mohakhali", "lon": 90.3983, "lat": 23.7776},
}
CHANGE_RADIUS_M = 600.0
STUDY_RADIUS_M = 1000.0


def distance_to_centre(graph: RoadGraph, node: int, area: dict) -> float:
    lon, lat = graph.nodes[node]
    return haversine_m(lon, lat, area["lon"], area["lat"])


def edges_within(graph: RoadGraph, area: dict, radius_m: float) -> list[int]:
    """Edges whose both ends lie within radius_m of the area's centre."""
    near = {n for n in graph.nodes if distance_to_centre(graph, n, area) <= radius_m}
    return [e.id for e in graph.edges.values() if e.u in near and e.v in near]


def subgraph(graph: RoadGraph, edge_ids) -> RoadGraph:
    """A new graph holding only these edges (with the same ids) and their ends."""
    small = RoadGraph()
    for edge_id in edge_ids:
        e = graph.edges[edge_id]
        for node in (e.u, e.v):
            if node not in small.nodes:
                small.add_node(node, *graph.nodes[node])
        small.edges[e.id] = Edge(**{**e.__dict__})
        small.out[e.u].append(e.id)
    small._next_edge = max(small.edges, default=-1) + 1
    return small


def street_names(osm_path: Path, graph: RoadGraph, edge_ids) -> dict[int, str]:
    """Edge id -> the street it belongs to, from the OpenStreetMap download the
    graph was built from. An edge's points are OSM nodes, and the street is the
    named way that contains them. Unnamed or unknown edges get no entry."""
    try:
        osm = json.loads(Path(osm_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    elements = osm.get("elements", [])
    node_at = {(round(e["lon"], 7), round(e["lat"], 7)): e["id"]
               for e in elements if e.get("type") == "node"}
    ways_of: dict[int, list[str]] = {}
    for way in elements:
        name = way.get("tags", {}).get("name:en") or way.get("tags", {}).get("name")
        if way.get("type") == "way" and name:
            for node in way.get("nodes", []):
                ways_of.setdefault(node, []).append(name)
    names = {}
    for edge_id in edge_ids:
        counts: dict[str, int] = {}
        for lon, lat in graph.edges[edge_id].geometry:
            for name in ways_of.get(node_at.get((round(lon, 7), round(lat, 7))), []):
                counts[name] = counts.get(name, 0) + 1
        if counts:
            names[edge_id] = max(counts, key=counts.get)      # the name most of its points carry
    return names
