"""Diversion over time: what if Dhaka's traffic had been steered around the jams?

Two timelines run side by side through the real captures, from a start time to
an end time:

    actual       what Google recorded at each capture
    with TRACK   the same amount of traffic at each moment, but a coordinator
                 moves some of it off the jams onto detours, a little at a time

Only the routes differ. The amount of traffic always comes from the real
capture at that moment, so rush hour builds and fades exactly as it did, and the
two timelines can be compared at any time, including the last.

Each step (ROUNDS_PER_CAPTURE steps per capture interval):

  1. Traffic   every road's vehicles per hour come from the capture:
               green 25 % of capacity .. dark red 105 % (city.LEVEL_LOAD).
  2. Jams      a jammed stretch is a run of red or dark red main roads of one
               class, up to STRETCH_M long. The first time it jams, A* finds the
               fastest way from its start to its end that avoids it, within
               SEARCH_M: its detour. (A jammed side street is nearly always a
               colour the spread copied from the main road beside it, so only
               main roads are detoured.)
  3. Moving    for each detour, worst jam first, the coordinator tries moving
               SWITCH of the stretch's traffic onto the detour, or back. A move
               is made only if it lowers the city's total vehicle-hours, and a
               detour road is never filled past YELLOW_VC (still yellow).
               Counting the whole city, not one driver, is the point: a car
               leaving a dark red road saves time for everyone still on it.
  4. Loads     actual traffic, minus what left each stretch, plus what joined
               each detour. Travel times by BPR; every road recoloured.

Vehicles are conserved: what leaves a stretch at its start rejoins at its end.

Honest limit: a colour is a level, not a count, so vehicles come from the level
and the road's capacity, and drivers are assumed to follow the coordinator. At
rush hour most detours run into other jammed main roads, so the gain is small:
the result says how far Dhaka's own street network can absorb its traffic.
"""

import math

import numpy as np

from ..graph.road_graph import RoadGraph
from ..search.astar import astar, haversine_heuristic
from ..traffic.bpr import ALPHA, BETA, MAX_VC, bpr_time
from .city import LEVEL_LOAD

ROUNDS_PER_CAPTURE = 3      # steps between two captures (about 8 minutes apart)
SWITCH = 0.10               # share of a stretch's traffic that may move per step
STRETCH_M = 1000.0          # longest jammed stretch given one detour
SEARCH_M = 800.0            # a detour stays this close to its stretch (plus half its length)
YELLOW_VC = 0.70            # a detour road is filled to at most this: still yellow
JAM_VC = 0.70               # red or worse: a road at or above this is jammed
NEW_DETOURS_PER_STEP = 400  # A* searches for new jams per step, worst jams first
MAX_DETOUR_FACTOR = 3.0     # a detour may be at most this many times the stretch's length...
MIN_DETOUR_M = 1500.0       # ...or this long, whichever is more
MAIN_ROADS = {"motorway", "trunk", "primary", "secondary", "tertiary"}


def road_class(highway: str) -> str:
    return (highway or "").replace("_link", "")


class Diversion:
    """One jammed stretch, its detour, and the share of its traffic using it."""

    def __init__(self, stretch: list[int], detour: list[int]):
        self.stretch = stretch
        self.detour = detour
        self.share = 0.0

    def through(self, actual: np.ndarray) -> float:
        """Traffic that runs the whole stretch: the least loaded road along it."""
        return float(actual[self.stretch].min())


class DiversionModel:
    def __init__(self, graph: RoadGraph):
        self.graph = graph
        n = len(graph.edges)
        self.capacity = np.array([graph.edges[i].capacity_vph for i in range(n)], dtype=float)
        self.free_flow = np.array([graph.edges[i].free_flow_s for i in range(n)], dtype=float)
        self.length = np.array([graph.edges[i].length_m for i in range(n)], dtype=float)
        self.main = np.array([road_class(graph.edges[i].highway) in MAIN_ROADS for i in range(n)])
        self.into: dict[int, list[int]] = {}
        for e in graph.edges.values():
            self.into.setdefault(e.v, []).append(e.id)
        self.heuristic = haversine_heuristic(graph)
        lat0 = float(np.mean([lat for _, lat in graph.nodes.values()]))
        east, north = 111320.0 * math.cos(math.radians(lat0)), 110540.0
        self.xy = {node: (lon * east, lat * north) for node, (lon, lat) in graph.nodes.items()}
        self.diversions: list[Diversion] = []
        self.in_use = np.zeros(n, dtype=bool)        # on a stretch or a detour already
        self.searches = 0

    # ── loads, times, colours ─────────────────────────────────────────────────

    def actual_load(self, levels: np.ndarray) -> np.ndarray:
        return np.asarray(LEVEL_LOAD)[levels] * self.capacity

    def with_track(self, actual: np.ndarray) -> np.ndarray:
        load = actual.copy()
        for d in self.diversions:
            moved = d.share * d.through(actual)
            if moved > 0:
                load[d.stretch] -= moved
                load[d.detour] += moved
        return np.maximum(load, 0.0)

    def hours(self, edges, load) -> float:
        """Vehicle-hours on these roads at these loads (BPR)."""
        vc = np.minimum(load / self.capacity[edges], MAX_VC)
        return float((load * self.free_flow[edges] * (1.0 + ALPHA * vc ** BETA)).sum()) / 3600.0

    def vehicle_hours(self, load: np.ndarray) -> float:
        return self.hours(np.arange(load.size), load)

    def levels_of(self, load: np.ndarray) -> np.ndarray:
        """Back to a colour, at the midpoints between the levels' loads."""
        vc = load / self.capacity
        return np.select([vc < 0.4, vc < 0.7, vc < 0.95], [0, 1, 2], 3).astype(np.uint8)

    # ── finding jams and their detours ────────────────────────────────────────

    def _next(self, edge_id: int, jammed: np.ndarray, forward: bool) -> int | None:
        """The road that carries straight on from this one, if it is jammed too."""
        e = self.graph.edges[edge_id]
        here = self.graph.nodes[e.v if forward else e.u]
        back = self.graph.nodes[e.u if forward else e.v]
        heading = math.atan2(here[1] - back[1], here[0] - back[0])
        candidates = self.graph.out.get(e.v, []) if forward else self.into.get(e.u, [])
        best, best_turn = None, math.radians(50)
        for c in candidates:
            o = self.graph.edges[c]
            if not jammed[c] or self.in_use[c] or road_class(o.highway) != road_class(e.highway):
                continue
            if (forward and o.v == e.u) or (not forward and o.u == e.v):
                continue                                  # turning back on itself
            a = self.graph.nodes[o.u if forward else o.v]
            b = self.graph.nodes[o.v if forward else o.u]
            turn = abs((math.atan2(b[1] - a[1], b[0] - a[0]) - heading + math.pi) % (2 * math.pi) - math.pi)
            if turn < best_turn:
                best, best_turn = c, turn
        return best

    def _stretch(self, edge_id: int, jammed: np.ndarray) -> list[int]:
        stretch, metres = [edge_id], self.length[edge_id]
        self.in_use[edge_id] = True
        for forward in (True, False):
            here = edge_id
            while metres < STRETCH_M:
                nxt = self._next(here, jammed, forward)
                if nxt is None:
                    break
                self.in_use[nxt] = True
                stretch = stretch + [nxt] if forward else [nxt] + stretch
                metres += self.length[nxt]
                here = nxt
        return stretch

    def _detour(self, stretch: list[int], load: np.ndarray) -> list[int] | None:
        start, end = self.graph.edges[stretch[0]].u, self.graph.edges[stretch[-1]].v
        avoid = set(stretch)
        (x1, y1), (x2, y2) = self.xy[start], self.xy[end]
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        reach2 = (SEARCH_M + self.length[stretch].sum() / 2) ** 2
        xy = self.xy

        def cost(edge):
            if edge.id in avoid:
                return float("inf")
            x, y = xy[edge.v]
            if (x - cx) ** 2 + (y - cy) ** 2 > reach2:
                return float("inf")                      # outside the search circle
            return bpr_time(edge.free_flow_s, load[edge.id], edge.capacity_vph)

        _, path, _ = astar(self.graph, start, end, cost, self.heuristic)
        self.searches += 1
        if not path:
            return None
        limit = max(MAX_DETOUR_FACTOR * self.length[stretch].sum(), MIN_DETOUR_M)
        return path if self.length[path].sum() <= limit else None

    def find_new_jams(self, load: np.ndarray, budget: int = NEW_DETOURS_PER_STEP) -> int:
        vc = load / self.capacity
        jammed = (vc >= JAM_VC) & self.main
        found = 0
        for edge_id in np.argsort(-np.where(jammed, vc, 0.0)):    # worst main-road jam first
            if found >= budget or not jammed[edge_id]:
                break
            if self.in_use[edge_id]:
                continue
            stretch = self._stretch(int(edge_id), jammed)
            detour = self._detour(stretch, load)
            if detour:
                self.diversions.append(Diversion(stretch, detour))
                self.in_use[detour] = True
                found += 1
        return found

    # ── one step of the coordinator ───────────────────────────────────────────

    def adjust(self, actual: np.ndarray) -> None:
        """Worst jam first: move SWITCH of a stretch's traffic onto its detour,
        or back, whichever lowers the city's vehicle-hours; or leave it."""
        load = self.with_track(actual)
        worst_first = sorted(self.diversions, key=lambda d: -float((load[d.stretch] / self.capacity[d.stretch]).max()))
        for d in worst_first:
            through = d.through(actual)
            if through <= 0:
                continue
            s, t = d.stretch, d.detour
            room = float((YELLOW_VC * self.capacity[t] - load[t]).min())
            options = (min(SWITCH, max(room, 0.0) / through, 1.0 - d.share) * through,     # more onto the detour
                       -min(SWITCH, d.share) * through)                                    # some back
            now = self.hours(s, load[s]) + self.hours(t, load[t])
            best, best_saving = 0.0, 0.0
            for amount in options:
                if amount == 0:
                    continue
                after = self.hours(s, np.maximum(load[s] - amount, 0.0)) + self.hours(t, load[t] + amount)
                if now - after > best_saving:
                    best, best_saving = amount, now - after
            if best:
                d.share += best / through
                load[s] = np.maximum(load[s] - best, 0.0)
                load[t] += best


def run_diversion(graph: RoadGraph, captures: list[tuple[str, np.ndarray]], progress=None) -> dict:
    """captures: [(name, levels)] in time order, from the start to the end.

    Returns every step's measurements for both timelines, the with-TRACK level
    of every road at every step, and the roads where the two ever differ."""
    model = DiversionModel(graph)
    steps, track_levels, actual_levels = [], [], []
    total = 1 + len(captures) * ROUNDS_PER_CAPTURE

    def record(name, sub, actual, load):
        lv_actual, lv_track = model.levels_of(actual), model.levels_of(load)
        steps.append({
            "capture": name, "round": sub,
            "actual": {"vehicle_hours": round(model.vehicle_hours(actual), 1),
                       "levels": np.bincount(lv_actual, minlength=4).tolist()},
            "track": {"vehicle_hours": round(model.vehicle_hours(load), 1),
                      "levels": np.bincount(lv_track, minlength=4).tolist()},
            "detours": len(model.diversions),
            "diverted_vph": round(sum(d.share * d.through(actual) for d in model.diversions)),
            "searches": model.searches,
        })
        track_levels.append(lv_track)
        actual_levels.append(lv_actual)
        if progress:
            progress.live("diversion", {**steps[-1], "step": len(steps) - 1, "total": total,
                                        "levels": "".join(map(str, lv_track.tolist()))}, force=True)

    for i, (name, levels) in enumerate(captures):
        actual = model.actual_load(levels)
        if i == 0:
            record(name, 0, actual, actual)              # the start: both timelines agree
        for sub in range(1, ROUNDS_PER_CAPTURE + 1):
            if progress:
                progress.step("A*", f"{name}: detours for new jams ({len(model.diversions):,} so far)",
                              len(steps), total)
            model.find_new_jams(model.with_track(actual))
            if progress:
                progress.step("diversion", f"{name}: round {sub} of {ROUNDS_PER_CAPTURE}, moving traffic",
                              len(steps), total)
            model.adjust(actual)
            record(name, sub, actual, model.with_track(actual))

    # the roads where the timelines ever differ, with their shapes, for the map
    differs = np.zeros(len(graph.edges), dtype=bool)
    for a, t in zip(actual_levels, track_levels):
        differs |= a != t
    changed = np.flatnonzero(differs)
    return {
        "steps": steps,
        "levels": ["".join(map(str, lv.tolist())) for lv in track_levels],
        "changed": {
            "edges": changed.tolist(),
            "geometry": [[[round(x, 5), round(y, 5)] for x, y in graph.edges[int(e)].geometry] for e in changed],
            "actual": ["".join(map(str, lv[changed].tolist())) for lv in actual_levels],
            "track": ["".join(map(str, lv[changed].tolist())) for lv in track_levels],
        },
        "searches": model.searches, "detours": len(model.diversions),
        "rounds_per_capture": ROUNDS_PER_CAPTURE,
        "settings": {"switch": SWITCH, "stretch_m": STRETCH_M, "search_m": SEARCH_M,
                     "yellow_vc": YELLOW_VC, "jam_vc": JAM_VC},
    }
