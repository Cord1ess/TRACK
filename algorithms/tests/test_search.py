"""A* and Dijkstra on a small grid: A* must match Dijkstra's optimum and
expand fewer nodes; unreachable goals must be reported, not crash."""

from track_algos.graph.road_graph import RoadGraph
from track_algos.search.astar import astar, haversine_heuristic
from track_algos.search.dijkstra import dijkstra, path_to


def grid_graph(n=6, step=0.002):
    """n x n lattice of two-way streets around Dhaka, node id = r*n + c."""
    g = RoadGraph()
    for r in range(n):
        for c in range(n):
            g.add_node(r * n + c, 90.40 + c * step, 23.75 + r * step)
    for r in range(n):
        for c in range(n):
            if c + 1 < n:
                g.add_two_way(r * n + c, r * n + c + 1, highway="secondary")
            if r + 1 < n:
                g.add_two_way(r * n + c, (r + 1) * n + c, highway="secondary")
    return g


def test_astar_matches_dijkstra():
    g = grid_graph()
    start, goal = 0, 35
    nodes, edges, cost = astar(g, start, goal)
    dist, prev = dijkstra(g, start, goal=goal)
    assert nodes[0] == start and nodes[-1] == goal
    assert abs(cost - dist[goal]) < 1e-6
    assert len(edges) == len(nodes) - 1 == 10  # Manhattan path on a 6x6 grid


def test_astar_respects_custom_costs():
    g = grid_graph(n=3)
    # make the direct bottom row very expensive: path should go around
    slow = {e.id for e in g.edges.values() if e.u in (0, 1) and e.v in (1, 2)}
    cost = lambda e: 1000.0 if e.id in slow else e.free_flow_s
    nodes, edges, c = astar(g, 0, 2, cost)
    assert not any(eid in slow for eid in edges)
    assert c < 1000


def test_heuristic_is_admissible():
    g = grid_graph()
    h = haversine_heuristic(g)
    dist, _ = dijkstra(g, 0)
    for n, d in dist.items():
        assert h(0, n) <= d + 1e-6


def test_unreachable():
    g = grid_graph(n=2)
    g.add_node(99, 90.5, 23.9)  # isolated node
    nodes, edges, cost = astar(g, 0, 99)
    assert nodes is None and cost == float("inf")
    _, prev = dijkstra(g, 0)
    assert path_to(prev, 0, 99) == (None, None)


def test_same_node():
    g = grid_graph(n=2)
    assert astar(g, 0, 0) == ([0], [], 0.0)
