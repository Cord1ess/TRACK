"""Group the captures into traffic scenarios with K-means.

Each capture is summarised as GRID x GRID numbers: for every cell of a grid
laid over the city, the average traffic level of the roads in it (0 = all
green .. 3 = all dark red). Two captures whose numbers are close had the same
pattern of congestion across the city.

K-means then groups the captures by those numbers alone. It is never told the
time. Afterwards each group is described by the times it contains, so you can
see whether the groups it found are real rush hours, quiet nights and weekends.
"""

import numpy as np

from ..graph.build_graph import DHAKA
from ..graph.road_graph import RoadGraph
from ..ml.kmeans import KMeans
from .history import History

GRID = 5
FIVE_NAMES = ["lightest", "light", "moderate", "heavy", "heaviest"]


def scenario_name(rank: int, k: int) -> str:
    """Scenarios are named by how congested they are; rank 0 is the lightest."""
    return FIVE_NAMES[rank] if k == 5 else f"{rank + 1} of {k} (1 = lightest)"


def road_cells(graph: RoadGraph, grid: int = GRID) -> np.ndarray:
    """Which grid cell each road's midpoint falls in, 0 .. grid*grid - 1."""
    cells = np.zeros(len(graph.edges), dtype=int)
    for e in graph.edges.values():
        lon, lat = e.geometry[len(e.geometry) // 2]
        col = int((lon - DHAKA["west"]) / (DHAKA["east"] - DHAKA["west"]) * grid)
        row = int((lat - DHAKA["south"]) / (DHAKA["north"] - DHAKA["south"]) * grid)
        cells[e.id] = min(max(row, 0), grid - 1) * grid + min(max(col, 0), grid - 1)
    return cells


def capture_features(history: History, cells: np.ndarray, grid: int = GRID) -> np.ndarray:
    """(captures, grid*grid): average level of the roads in each cell."""
    features = np.zeros((len(history.names), grid * grid))
    roads_in_cell = np.bincount(cells, minlength=grid * grid).clip(min=1)
    for i in range(len(history.names)):
        features[i] = np.bincount(cells, weights=history.levels[i], minlength=grid * grid) / roads_in_cell
    return features


def hour_label(hour: int) -> str:
    return f"{hour % 12 or 12} {'am' if hour < 12 else 'pm'}"


def find_scenarios(history: History, graph: RoadGraph, k: int = 5, progress=None) -> dict:
    if progress:
        progress.step("K-means", f"grouping {len(history.names):,} captures into {k} scenarios")
    features = capture_features(history, road_cells(graph))
    model = KMeans(k=k, seed=0).fit(features)
    labels = model.labels_

    hours = history.minute_of_day // 60
    weekend = history.weekend
    found = []
    for group in range(k):
        members = np.flatnonzero(labels == group)
        if members.size == 0:
            continue
        counts = np.bincount(hours[members], minlength=24)
        top_hours = [int(h) for h in np.argsort(-counts)[:3] if counts[h] > 0]
        found.append({
            "group": group,
            "captures": int(members.size),
            "share": round(members.size / len(labels), 3),
            "mean_level": round(float(features[members].mean()), 3),
            "weekend_share": round(float(weekend[members].mean()), 3),
            "common_hours": [hour_label(h) for h in top_hours],
            "hours_histogram": counts.tolist(),
        })
    # name each scenario by how congested it is, lightest first
    found.sort(key=lambda s: s["mean_level"])
    for rank, scenario in enumerate(found):
        scenario["name"] = scenario_name(rank, len(found))
    order = {s["group"]: rank for rank, s in enumerate(found)}
    return {"k": k, "grid": GRID, "scenarios": found,
            "capture_scenario": [order[int(g)] for g in labels],
            "capture_names": history.names}
