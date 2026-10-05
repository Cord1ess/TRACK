"""A* search: the least-cost path from one node to another.

Each node's priority is f = g + h: the cost so far (g) plus an estimate of the
cost left (h). The estimate is the straight-line distance to the goal driven at
the network's top speed. Nothing can beat that, so the estimate never
overestimates, and the first time the goal comes off the queue its path is the
best one. The edge cost function is passed in: free-flow time, congested BPR
time, or anything else.
"""

import heapq
import math
from typing import Callable, Hashable

from ..geo import EARTH_R
from ..graph.road_graph import Edge, RoadGraph


def haversine_heuristic(graph: RoadGraph, max_speed_kmph: float | None = None) -> Callable:
    """Straight-line time to the goal at the network's top speed.

    Every junction's position is turned into metres east and north once, so
    each estimate is one square root rather than five trigonometric calls; on a
    congested city A* makes tens of thousands of estimates per search. Across
    Dhaka the flat-map distance is within a fraction of a percent of the true
    one, and it is shrunk by 1 % so it can never overestimate."""
    top = max_speed_kmph or max((e.free_flow_kmph for e in graph.edges.values()), default=50.0)
    top_ms = top / 3.6
    lat0 = sum(lat for _, lat in graph.nodes.values()) / max(1, len(graph.nodes))
    east = math.cos(math.radians(lat0)) * math.pi / 180.0 * EARTH_R
    north = math.pi / 180.0 * EARTH_R
    xy = {n: (lon * east, lat * north) for n, (lon, lat) in graph.nodes.items()}

    def h(node: Hashable, goal: Hashable) -> float:
        (x1, y1), (x2, y2) = xy[node], xy[goal]
        return 0.99 * math.hypot(x2 - x1, y2 - y1) / top_ms
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
