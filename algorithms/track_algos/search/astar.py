"""A* search: the least-cost path from one node to another.

Each node's priority is f = g + h, cost so far plus an estimate of the cost
left. The estimate is straight-line distance at the network's top speed, which
never exceeds the real travel time, so the first path found is the best one.
The edge cost function is injected: free-flow time, BPR-congested time, or
anything else.
"""

import heapq
from typing import Callable, Hashable

from ..geo import haversine_m
from ..graph.road_graph import Edge, RoadGraph


def haversine_heuristic(graph: RoadGraph, max_speed_kmph: float | None = None) -> Callable:
    """Straight-line time at the network's top speed; never overestimates."""
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
    """Return (node_path, edge_id_path, total_cost), or (None, None, inf) if unreachable."""
    cost_fn = cost_fn or (lambda e: e.free_flow_s)
    heuristic = heuristic or haversine_heuristic(graph)
    if start == goal:
        return [start], [], 0.0

    g_score = {start: 0.0}
    came_from: dict = {}          # node -> (previous node, edge id)
    closed = set()
    counter = 0                   # tie-breaker so the heap order is deterministic
    frontier = [(heuristic(start, goal), counter, start)]

    while frontier:
        _, _, node = heapq.heappop(frontier)
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
