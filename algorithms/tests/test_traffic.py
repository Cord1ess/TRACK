"""BPR, gravity demand and the assignment loop. The key property to prove:
assignment SPREADS load, so the final network has lower peak v/c and lower
total travel time than routing everyone all-or-nothing on free-flow costs."""

from track_algos.graph.road_graph import RoadGraph
from track_algos.search.astar import astar
from track_algos.traffic.assignment import Assignment
from track_algos.traffic.bpr import ALPHA, BETA, JAM_FACTOR, JAM_VC, bpr_time, bpr_time_from_ratio, delay_factor
from track_algos.traffic.demand import gravity_matrix, sample_od_pairs, zones_from_grid


def test_bpr_shape():
    assert bpr_time(100, 0, 1000) == 100                        # empty road = free flow
    assert bpr_time(100, 2000, 1000) > bpr_time(100, 1500, 1000) > bpr_time(100, 1000, 1000)
    assert bpr_time(100, 10, 0) == float("inf")                 # no capacity = impassable
    assert bpr_time_from_ratio(100, 0.5) == bpr_time(100, 500, 1000)


def test_bpr_calibrated_to_a_dhaka_jam():
    """alpha is pinned so a dark red road runs at 1/JAM_FACTOR of free flow.
    With the textbook alpha=0.15 the four traffic levels differ by under 20 %,
    which would make the router ignore traffic entirely."""
    assert abs(delay_factor(JAM_VC) - JAM_FACTOR) < 1e-9
    assert abs(bpr_time_from_ratio(100, JAM_VC) - 100 * JAM_FACTOR) < 1e-6
    green, yellow, red, dark = (delay_factor(v) for v in (0.25, 0.55, 0.85, JAM_VC))
    assert green < 1.05, "green must be essentially free flow"
    assert 1.1 < yellow < 1.5 and 2.0 < red < 3.0
    assert dark > 3.5 and dark > 3 * yellow, "the levels must actually separate"
    assert BETA == 4.0 and 2.0 < ALPHA < 3.0


def two_route_network():
    """A -> B via a short fast road (cap 100) or a longer slow road (cap 1000)."""
    g = RoadGraph()
    g.add_node(0, 90.40, 23.75)
    g.add_node(1, 90.41, 23.75)
    g.add_node(2, 90.405, 23.76)
    g.add_edge(0, 1, highway="primary", capacity_vph=100, free_flow_kmph=50)        # short, small
    g.add_edge(0, 2, highway="trunk", capacity_vph=1000, free_flow_kmph=40)
    g.add_edge(2, 1, highway="trunk", capacity_vph=1000, free_flow_kmph=40)         # detour, big
    return g


def test_incremental_assignment_spreads_load():
    g = two_route_network()
    trips = [(0, 1)] * 400
    # all-or-nothing on free flow: everyone takes the short road
    _, aon_edges, _ = astar(g, 0, 1)
    assert aon_edges == [0]
    naive = Assignment(g)
    naive.volume = {0: 400.0}
    naive_time = naive.total_travel_time()
    naive_max_vc = naive.vc(0)

    a = Assignment(g)
    frames = a.incremental(trips, batches=10)
    assert len(frames) == 11 and frames[0]["label"] == "before"
    assert a.searches == 400
    # load is now split across both routes
    assert a.volume.get(0, 0) > 0 and a.volume.get(1, 0) > 0
    assert a.vc(0) < naive_max_vc
    assert a.total_travel_time() < naive_time
    assert frames[-1]["stats"]["over_capacity"] < 1 or frames[-1]["stats"]["max_vc"] < naive_max_vc


def test_msa_converges_toward_balance():
    g = two_route_network()
    a = Assignment(g)
    frames = a.msa([(0, 1)] * 400, iterations=12)
    short, detour = a.volume.get(0, 0), a.volume.get(1, 0)
    assert short > 0 and detour > 0
    # at equilibrium both routes carry load and the short road is not wildly over capacity
    assert a.vc(0) < 4.0
    assert frames[-1]["stats"]["total_time_s"] <= frames[1]["stats"]["total_time_s"]


def test_frame_geojson_styles_edges():
    g = two_route_network()
    a = Assignment(g)
    a.incremental([(0, 1)] * 50, batches=2)
    gj = a.frame_geojson(a.frames[-1])
    assert gj["type"] == "FeatureCollection" and len(gj["features"]) == 3
    assert all("cls" in f["properties"] and "color" in f["properties"] for f in gj["features"])


def test_gravity_demand():
    g = two_route_network()
    bbox = {"north": 23.77, "south": 23.74, "east": 90.42, "west": 90.39}
    zones = zones_from_grid(g, bbox, 2, 2, weight_fn=lambda lon, lat: 1.0 + (lon > 90.405))
    T = gravity_matrix(zones, gamma=1.5)
    assert all(v > 0 for v in T.values()) and len(T) == 4 * 3
    pairs = sample_od_pairs(T, 100, seed=1)
    assert len(pairs) == 100 and all(o != d for o, d in pairs)
