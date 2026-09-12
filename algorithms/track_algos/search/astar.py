"""A* search: the router at the heart of TRACK.

A* finds the least-cost path from ONE origin to ONE destination. It is Dijkstra
with a sense of direction: every node's priority is
    f(n) = g(n) + h(n)
where g is the cost so far and h is a heuristic estimate of the remaining cost.
As long as h never overestimates (it is "admissible"), the first time the goal
is popped its path is optimal. We use straight-line (haversine) distance divided
by the fastest speed in the network, which can never exceed the real travel time,
so optimality holds.

Why A* and not Dijkstra here: every query in the assignment loop is
origin -> destination, and the heuristic keeps the search pointed at the
destination instead of flooding the whole city. That saving grows with graph
size. Dijkstra (one-to-all) lives next door for the cases that need it.

`cost_fn(edge)` is injected, so the same router works on free-flow time, on
BPR-congested time inside the assignment loop, or on predicted future costs
for the personal A-to-B feature.
"""

import heapq
from typing import Callable, Hashable

from ..geo import haversine_m
from ..graph.road_graph import Edge, RoadGraph


def haversine_heuristic(graph: RoadGraph, max_speed_kmph: float | None = None) -> Callable:
    """Admissible heuristic: straight-line distance at the network's top speed."""
    top = max_speed_kmph or max((e.free_flow_kmph for e in graph.edges.values()), default=50.0)
    top_ms = top / 3.6

    def h(node: Hashable, goal: Hashable) -> float:
        lon1, lat1 = graph.nodes[node]
        lon2, lat2 = graph.nodes[goal]
        return haversine_m(lon1, lat1, lon2, lat2) / top_ms
    return h


def astar(graph: RoadGraph, start: Hashable, goal: Hashable,
          cost_fn: Callable[[Edge], float] | None = None,
          heuristic: Callable[[Hashable, Hashable], float] | None = None):
    """Return (node_path, edge_id_path, total_cost) or (None, None, inf) if unreachable.

    graph:      RoadGraph (nodes + directed edges)
    cost_fn:    edge -> non-negative cost; default free-flow travel time (s)
    heuristic:  (node, goal) -> estimate of remaining cost; default haversine time
    """
    cost_fn = cost_fn or (lambda e: e.free_flow_s)
    heuristic = heuristic or haversine_heuristic(graph)
    if start == goal:
        return [start], [], 0.0

    g_score = {start: 0.0}
    came_from: dict = {}          # node -> (prev_node, edge_id)
    closed = set()
    counter = 0                    # tie-breaker keeps heap ordering deterministic
    frontier = [(heuristic(start, goal), counter, start)]

    while frontier:
        f, _, node = heapq.heappop(frontier)
        if node in closed:
            continue
        if node == goal:
            nodes, edges = [goal], []
            while nodes[-1] in came_from:
                prev, eid = came_from[nodes[-1]]
                nodes.append(prev)
                edges.append(eid)
            return nodes[::-1], edges[::-1], g_score[goal]
        closed.add(node)
        for nbr, edge in graph.neighbors(node):
            if nbr in closed:
                continue
            tentative = g_score[node] + cost_fn(edge)
            if tentative < g_score.get(nbr, float("inf")):
                g_score[nbr] = tentative
                came_from[nbr] = (node, edge.id)
                counter += 1
                heapq.heappush(frontier, (tentative + heuristic(nbr, goal), counter, nbr))
    return None, None, float("inf")
