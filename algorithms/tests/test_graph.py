"""RoadGraph construction, defaults, JSON round trip, and the Overpass
assembler on a hand-made OSM response (no network)."""

from track_algos.geo import haversine_m
from track_algos.graph.build_graph import MIN_PIECE_M, build
from track_algos.graph.road_graph import RoadGraph, class_defaults


def test_edge_defaults_and_two_way():
    g = RoadGraph()
    g.add_node(0, 90.40, 23.75)
    g.add_node(1, 90.41, 23.75)
    a, b = g.add_two_way(0, 1, highway="primary")
    assert a.u == 0 and a.v == 1 and b.u == 1 and b.v == 0
    assert a.lanes == 2 and a.capacity_vph == 2400 and a.free_flow_kmph == 50
    assert abs(a.length_m - haversine_m(90.40, 23.75, 90.41, 23.75)) < 1e-6
    assert abs(a.free_flow_s - a.length_m / (50 / 3.6)) < 1e-6
    assert b.geometry == list(reversed(a.geometry))
    assert class_defaults("primary_link") == class_defaults("primary")


def test_json_roundtrip(tmp_path):
    g = RoadGraph()
    for i, (lon, lat) in enumerate([(90.40, 23.75), (90.41, 23.75), (90.41, 23.76)]):
        g.add_node(i, lon, lat)
    g.add_two_way(0, 1, highway="secondary")
    g.add_edge(1, 2, highway="tertiary", oneway=True)
    p = tmp_path / "g.json"
    g.save(p)
    h = RoadGraph.load(p)
    assert len(h) == 3 and h.nodes == g.nodes
    assert [e.v for _, e in h.neighbors(1)] == [0, 2]
    assert h.nearest_node(90.4101, 23.7601) == 2
    gj = g.to_geojson({0: {"cls": 3}})
    assert gj["features"][0]["properties"]["cls"] == 3


def test_long_edges_are_cut_to_the_length_cap():
    """A 600 m way with no intersections still becomes several edges, so one
    edge describes one traffic condition instead of averaging a jam with a
    clear stretch."""
    osm = {"elements": [
        {"type": "node", "id": 1, "lon": 90.400, "lat": 23.750},
        {"type": "node", "id": 2, "lon": 90.4059, "lat": 23.750},     # ~600 m east
        {"type": "way", "id": 10, "nodes": [1, 2], "tags": {"highway": "primary"}},
    ]}
    uncut, _ = build(osm, max_edge_m=0)
    assert len(uncut.edges) == 2                                       # one two-way pair
    assert uncut.edges[0].length_m > 500

    g, stats = build(osm, max_edge_m=150.0)
    lengths = [e.length_m for e in g.edges.values()]
    # a trailing stub is folded back, so the guarantee is cap + MIN_PIECE_M
    assert len(g.edges) > 2 and max(lengths) <= 150.0 + MIN_PIECE_M
    assert min(lengths) >= MIN_PIECE_M, "no degenerate slivers"
    assert abs(sum(lengths) - 2 * uncut.edges[0].length_m) < 1.0        # no length lost
    assert stats["split_ways"] == 1 and stats["synthetic_nodes"] > 0
    # the cut points are new nodes and must be negative so they cannot collide with OSM ids
    assert all(n < 0 for n in g.nodes if n not in (1, 2))
    # the chain must stay connected end to end
    assert any(e.u == 1 for e in g.edges.values()) and any(e.v == 2 for e in g.edges.values())


def test_build_from_osm_splits_at_junctions_and_handles_oneway():
    # Way A: 1-2-3 (two-way). Way B: 3-4 (oneway=yes). Way C: 2-5 (oneway=-1). Node 2 and 3 are junctions.
    osm = {"elements": [
        {"type": "node", "id": 1, "lon": 90.400, "lat": 23.750},
        {"type": "node", "id": 2, "lon": 90.402, "lat": 23.750},
        {"type": "node", "id": 3, "lon": 90.404, "lat": 23.750},
        {"type": "node", "id": 4, "lon": 90.406, "lat": 23.750},
        {"type": "node", "id": 5, "lon": 90.402, "lat": 23.752},
        {"type": "way", "id": 10, "nodes": [1, 2, 3], "tags": {"highway": "primary", "lanes": "3"}},
        {"type": "way", "id": 11, "nodes": [3, 4], "tags": {"highway": "secondary", "oneway": "yes"}},
        {"type": "way", "id": 12, "nodes": [2, 5], "tags": {"highway": "residential", "oneway": "-1"}},
        {"type": "way", "id": 13, "nodes": [5, 4], "tags": {"highway": "service"}},   # dropped class
    ]}
    g, stats = build(osm, max_edge_m=0)          # junction splitting only, no length cap
    # A split at node 2: (1-2),(2-3) each two-way = 4 edges; B one edge; C one reversed edge
    assert len(g.edges) == 6
    assert stats["twoway_edges"] == 4 and stats["oneway_edges"] == 2
    assert not any(e.highway == "service" for e in g.edges.values())
    b = next(e for e in g.edges.values() if e.highway == "secondary")
    assert (b.u, b.v) == (3, 4) and b.oneway
    c = next(e for e in g.edges.values() if e.highway == "residential")
    assert (c.u, c.v) == (5, 2), "oneway=-1 must reverse direction"
    a = next(e for e in g.edges.values() if e.highway == "primary")
    assert a.lanes == 3 and a.capacity_vph == 3600
