"""Dijkstra: least-cost paths from one node to every other node.

A* with no heuristic. Right for one-to-all questions (distances from a hub,
reachability); A* is cheaper for a single origin and destination.
"""

import heapq
from typing import Callable, Hashable

from ..graph.road_graph import Edge, RoadGraph


def dijkstra(graph: RoadGraph, start: Hashable, cost_fn: Callable[[Edge], float] | None = None,
             goal: Hashable | None = None):
    """Return (dist, prev): dist[node] is the least cost from start, prev[node]
    is (previous node, edge id). Stops early once `goal` is settled."""
    cost_fn = cost_fn or (lambda e: e.free_flow_s)
    dist = {start: 0.0}
    prev: dict = {}
    done = set()
    frontier = [(0.0, 0, start)]
    counter = 0
    while frontier:
        d, _, node = heapq.heappop(frontier)
        if node in done:
            continue
        done.add(node)
        if node == goal:
            break
        for nbr, edge in graph.neighbors(node):
            nd = d + cost_fn(edge)
            if nd < dist.get(nbr, float("inf")):
                dist[nbr] = nd
                prev[nbr] = (node, edge.id)
                counter += 1
                heapq.heappush(frontier, (nd, counter, nbr))
    return dist, prev


def path_to(prev: dict, start: Hashable, goal: Hashable):
    """Rebuild (node_path, edge_id_path) from a `prev` map, or (None, None)."""
    if goal != start and goal not in prev:
        return None, None
    nodes, edges = [goal], []
    while nodes[-1] != start:
        p, eid = prev[nodes[-1]]
        nodes.append(p)
        edges.append(eid)
    return nodes[::-1], edges[::-1]
