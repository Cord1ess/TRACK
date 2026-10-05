"""BPR, gravity demand and the assignment loop. The key property to prove:
assignment SPREADS load, so the final network has lower peak v/c and lower
total travel time than routing everyone all-or-nothing on free-flow costs."""

from track_algos.graph.road_graph import RoadGraph
from track_algos.search.astar import astar
from track_algos.simulation.assignment import Assignment
from track_algos.traffic.bpr import ALPHA, BETA, JAM_FACTOR, JAM_VC, bpr_time, bpr_time_from_ratio, delay_factor
from track_algos.simulation.demand import gravity_matrix, sample_od_pairs, zones_from_grid


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


def test_batches_spread_load_that_all_at_once_piles_up():
    g = two_route_network()
    trips = [(0, 1)] * 400
    _, free_flow_route, _ = astar(g, 0, 1)
    assert free_flow_route == [0]                       # on an empty network everyone takes the short road

    naive = Assignment(g)
    naive.all_at_once(trips, vehicles_per_trip=1.0)
    assert naive.volume == {0: 400.0}                   # all of them, on the short road

    batched = Assignment(g)
    seen = []
    batched.in_batches(trips, batches=10, vehicles_per_trip=1.0,
                       after_batch=lambda n, a: seen.append(n))
    # 400 identical trips: one A* search per batch, shared by the batch's 40 trips
    assert seen == list(range(1, 11)) and batched.searches == 10
    assert batched.volume.get(0, 0) > 0 and batched.volume.get(1, 0) > 0     # split across both routes
    assert batched.volume_over_capacity(0) < naive.volume_over_capacity(0)
    assert batched.vehicle_hours() < naive.vehicle_hours()
    assert batched.measure()["roads_over_capacity"] <= naive.measure()["roads_over_capacity"]


def test_background_traffic_is_kept_and_counted():
    g = two_route_network()
    a = Assignment(g, background={0: 150.0})
    assert a.volume_over_capacity(0) == 1.5 and a.roads_over_capacity() == 1
    a.in_batches([(0, 1)] * 10, batches=2, vehicles_per_trip=[5.0] * 10)
    assert a.volume[0] >= 150.0 and len(a.routes) == 10 and a.unreachable == 0


def test_gravity_demand():
    g = two_route_network()
    bbox = {"north": 23.77, "south": 23.74, "east": 90.42, "west": 90.39}
    zones = zones_from_grid(g, bbox, 2, 2, weight_fn=lambda lon, lat: 1.0 + (lon > 90.405))
    T = gravity_matrix(zones, gamma=1.5)
    assert all(v > 0 for v in T.values()) and len(T) == 4 * 3
    pairs = sample_od_pairs(T, 100, seed=1)
    assert len(pairs) == 100 and all(o != d for o, d in pairs)
