"""The city-wide experiment: thousands of A* searches, with and without feedback.

    1. Forecast    every road's level at the chosen day and time comes in from
                   the forecaster (simulate.py does that part).
    2. Background  each road's forecast level becomes the traffic already on it:
                   vehicles = (weight / 100) x capacity, so green carries 25 %
                   of capacity, yellow 55 %, red 85 %, dark red 105 %.
    3. Demand      a 6 x 6 grid of zones over the city. The gravity model draws
                   the trips between them; busier zones send and attract more.
                   Each trip stands for `vehicles_per_trip` vehicles.
    4. All at once every trip routed with A* on the forecast roads, then all
                   the load lands together (the baseline).
    5. In batches  the same trips, routed batch by batch with the load fed back
                   between batches.
    6. Compare     vehicle-hours, roads over capacity, average trip time.

It also keeps what the map needs to replay the run (the level of every road
the new trips touched, after every batch) and every trip's route, which the
road-change planner reuses to know which trips pass through an area.

Honest limit: Google shows how busy roads are, not where people travel. The
trips here are synthetic, so the result says what TRACK's routing would do for
that demand, not what happened on Dhaka's streets.
"""

import numpy as np

from ..graph.build_graph import DHAKA
from ..graph.road_graph import RoadGraph
from .assignment import Assignment
from .demand import gravity_matrix, sample_od_pairs, zones_from_grid

LEVEL_LOAD = (0.25, 0.55, 0.85, 1.05)     # volume / capacity for green .. dark red
ZONE_GRID = 6


def background_volume(graph: RoadGraph, levels: np.ndarray) -> dict:
    """Vehicles per hour already on each road, from its forecast level."""
    return {e.id: LEVEL_LOAD[levels[e.id]] * e.capacity_vph for e in graph.edges.values()}


def load_level(volume_over_capacity: float) -> int:
    """Back from load to a colour, on the same scale: under 0.4 green, under 0.7
    yellow, under 0.95 red, else dark red (the midpoints between the levels)."""
    v = volume_over_capacity
    return 0 if v < 0.4 else 1 if v < 0.7 else 2 if v < 0.95 else 3


def make_trips(graph: RoadGraph, levels: np.ndarray, trips: int, seed: int = 0):
    """Zones on a grid, weighted by how congested the roads in them are, and
    `trips` origin-destination pairs drawn from the gravity model."""
    cell_level = np.zeros(ZONE_GRID * ZONE_GRID)
    cell_roads = np.zeros(ZONE_GRID * ZONE_GRID)
    width = (DHAKA["east"] - DHAKA["west"]) / ZONE_GRID
    height = (DHAKA["north"] - DHAKA["south"]) / ZONE_GRID

    def cell_of(lon, lat):
        col = min(max(int((lon - DHAKA["west"]) / width), 0), ZONE_GRID - 1)
        row = min(max(int((lat - DHAKA["south"]) / height), 0), ZONE_GRID - 1)
        return row * ZONE_GRID + col

    for e in graph.edges.values():
        cell = cell_of(*e.geometry[len(e.geometry) // 2])
        cell_level[cell] += levels[e.id]
        cell_roads[cell] += 1
    mean_level = cell_level / np.maximum(cell_roads, 1)

    # a zone's pull: 1 for an all-green zone, up to 4 for an all-dark-red one
    zones = zones_from_grid(graph, DHAKA, ZONE_GRID, ZONE_GRID,
                            weight_fn=lambda lon, lat: 1.0 + mean_level[cell_of(lon, lat)])
    matrix = gravity_matrix(zones, gamma=1.5)
    node_of = {z["id"]: z["node"] for z in zones}
    pairs = sample_od_pairs(matrix, trips, seed=seed)
    return [(node_of[a], node_of[b]) for a, b in pairs], zones


def run_city(graph: RoadGraph, levels: np.ndarray, trips: int = 2000, batches: int = 10,
             vehicles_per_trip: float = 10.0, seed: int = 0, progress=None) -> dict:
    background = background_volume(graph, levels)
    if progress:
        progress.step("gravity model", f"drawing {trips:,} trips between {ZONE_GRID * ZONE_GRID} zones")
    od, zones = make_trips(graph, levels, trips, seed)

    before = Assignment(graph, background).measure()

    naive = Assignment(graph, background, progress)
    naive.trips_total = 2 * len(od)                   # both runs, for the progress bar
    naive.all_at_once(od, vehicles_per_trip)

    batched = Assignment(graph, background, progress)
    batched.trips_done, batched.trips_total = naive.trips_done, 2 * len(od)
    after_each_batch = []
    snapshots = []

    def record(number, assignment):
        after_each_batch.append(assignment.measure())
        snapshots.append(dict(assignment.volume))
        if progress:
            progress.step("assignment", f"batch {number} of {batches} landed",
                          assignment.trips_done, assignment.trips_total)

    batched.in_batches(od, batches, vehicles_per_trip, after_batch=record)

    # The roads the new trips touched, and their colour at each step of the replay:
    # before, all at once, then after each batch.
    touched = sorted({e for route in naive.routes + batched.routes for e in route})

    def level_of(volume: dict, edge_id: int) -> int:
        edge = graph.edges[edge_id]
        return load_level(volume.get(edge_id, 0.0) / edge.capacity_vph if edge.capacity_vph else 0.0)

    steps = [background, naive.volume] + snapshots
    replay = ["".join(str(level_of(v, e)) for v in steps) for e in touched]

    return {
        "trips": len(od), "batches": batches, "vehicles_per_trip": vehicles_per_trip,
        "vehicles": round(len(od) * vehicles_per_trip),
        "searches": naive.searches + batched.searches,
        "results": {"before": before, "all_at_once": naive.measure(), "in_batches": batched.measure(),
                    "after_each_batch": after_each_batch},
        "zones": [{"id": z["id"], "lon": z["lon"], "lat": z["lat"], "weight": round(z["weight"], 2)}
                  for z in zones],
        "replay": {"steps": ["before", "all at once"] + [f"batch {i + 1}" for i in range(len(snapshots))],
                   "edges": touched,
                   "geometry": [[[round(x, 5), round(y, 5)] for x, y in graph.edges[e].geometry]
                                for e in touched],
                   "levels": replay},
        "routes": batched.routes,
        "route_vehicles": batched.vehicles,
        "forecast_levels": "".join(str(int(v)) for v in levels),
    }
