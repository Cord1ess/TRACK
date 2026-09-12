"""Dijkstra's algorithm: least-cost paths from one origin to EVERY node.

Same idea as A* with the heuristic set to zero, so it expands outward evenly.
That makes it the right tool for one-to-all questions (baseline travel times
from a hub to all zones for clustering, reachability, isochrones) and the
wrong tool for the thousands of origin->destination queries in the assignment
loop, where A*'s heuristic avoids the wasted expansion.
"""

import heapq
from typing import Callable, Hashable

from ..graph.road_graph import Edge, RoadGraph


def dijkstra(graph: RoadGraph, start: Hashable, cost_fn: Callable[[Edge], float] | None = None,
             goal: Hashable | None = None):
    """Return (dist, prev) where dist[node] = least cost from start and
    prev[node] = (previous node, edge id). If `goal` is given, stop early once
    it is settled."""
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
    """Rebuild (node_path, edge_id_path) from a Dijkstra `prev` map."""
    if goal != start and goal not in prev:
        return None, None
    nodes, edges = [goal], []
    while nodes[-1] != start:
        p, eid = prev[nodes[-1]]
        nodes.append(p)
        edges.append(eid)
    return nodes[::-1], edges[::-1]
