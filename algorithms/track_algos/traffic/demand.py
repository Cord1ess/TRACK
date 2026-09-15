"""Synthetic trip demand from a gravity model.

Trips between zones i and j:  T_ij = k * w_i * w_j / d_ij ** gamma
Busier zones make and attract more trips; distance suppresses them.
"""

import random

from ..geo import haversine_m


def gravity_matrix(zones: list[dict], gamma: float = 1.5, k: float = 1.0) -> dict:
    """zones: [{"id", "lon", "lat", "weight"}] -> {(i, j): trips}."""
    T = {}
    for a in zones:
        for b in zones:
            if a["id"] == b["id"]:
                continue
            d = max(200.0, haversine_m(a["lon"], a["lat"], b["lon"], b["lat"]))   # floor: no blow-up at 0 m
            T[(a["id"], b["id"])] = k * a["weight"] * b["weight"] / (d / 1000.0) ** gamma
    return T


def sample_od_pairs(matrix: dict, n_trips: int, seed: int = 0) -> list[tuple]:
    """Draw n_trips (origin, destination) pairs in proportion to the matrix."""
    rng = random.Random(seed)
    pairs = list(matrix.keys())
    return rng.choices(pairs, weights=[matrix[p] for p in pairs], k=n_trips)


def zones_from_grid(graph, bbox: dict, rows: int, cols: int, weight_fn=None) -> list[dict]:
    """A rows x cols grid over the box, each cell anchored to its nearest graph node."""
    zones = []
    dlat = (bbox["north"] - bbox["south"]) / rows
    dlon = (bbox["east"] - bbox["west"]) / cols
    for r in range(rows):
        for c in range(cols):
            lat = bbox["south"] + (r + 0.5) * dlat
            lon = bbox["west"] + (c + 0.5) * dlon
            node = graph.nearest_node(lon, lat)
            nlon, nlat = graph.nodes[node]
            zones.append({"id": r * cols + c, "lon": nlon, "lat": nlat, "node": node,
                          "weight": weight_fn(lon, lat) if weight_fn else 1.0})
    return zones
