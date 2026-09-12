"""Synthetic origin-destination demand with a gravity model.

No per-vehicle trip data exists for Dhaka, so demand is generated: the city is
divided into zones (each anchored to a graph node), every zone gets a weight
(how busy it is, e.g. from observed congestion or population), and the number
of trips between zones i and j follows the gravity model used in Dhaka-specific
transport research:

    T_ij = k * w_i * w_j / d_ij ** gamma          (i != j)

Busier zones produce and attract more trips; distance suppresses them. The
matrix is then sampled into individual (origin, destination) pairs for the
assignment loop, which routes each one with A*.
"""

import random

from ..geo import haversine_m


def gravity_matrix(zones: list[dict], gamma: float = 1.5, k: float = 1.0) -> dict:
    """zones: [{"id", "lon", "lat", "weight"}] -> {(i, j): trips}.
    Weights are relative; scale with `k` or normalise afterwards."""
    T = {}
    for a in zones:
        for b in zones:
            if a["id"] == b["id"]:
                continue
            d = max(200.0, haversine_m(a["lon"], a["lat"], b["lon"], b["lat"]))  # floor to avoid blow-up
            T[(a["id"], b["id"])] = k * a["weight"] * b["weight"] / (d / 1000.0) ** gamma
    return T


def sample_od_pairs(matrix: dict, n_trips: int, seed: int = 0) -> list[tuple]:
    """Draw n_trips (origin_zone, destination_zone) pairs in proportion to the matrix."""
    rng = random.Random(seed)
    pairs = list(matrix.keys())
    weights = [matrix[p] for p in pairs]
    return rng.choices(pairs, weights=weights, k=n_trips)


def zones_from_grid(graph, bbox: dict, rows: int, cols: int, weight_fn=None) -> list[dict]:
    """Simple zoning: an rows x cols grid over the bbox, each cell anchored to
    its nearest graph node. weight_fn(lon, lat) -> weight, default 1."""
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
