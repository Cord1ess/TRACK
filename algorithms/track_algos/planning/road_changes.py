"""Which changes to the streets of one area would make its traffic flow better.

1. The options (the CSP's variables and domains), found in the area's road data:

     side streets   the two-way side streets that carry the most traffic:
                    two-way (as today), one-way either way, or closed.
                    A street made one-way gets both lanes in that direction,
                    so its capacity doubles.
     U-turn gaps    the short links OpenStreetMap draws between the two halves
                    of a divided main road: open (as today) or closed.

   Main roads (motorway, trunk, primary, secondary) are never changed.

2. The rules (the CSP's constraints):

     at most N changes     no more than `max_changes` options differ from today
     nobody is trapped     every junction that can reach every other junction
                           today still can, in both directions, and every
                           journey across the area possible today still is
     keep a way to turn    two U-turn gaps less than 400 m apart are not both closed

3. The search: backtracking with forward checking (csp.py) finds every layout
   that obeys the rules.

4. The traffic: the vehicles arriving on each main road at the edge of the
   area (from the forecast: green 25 % of capacity .. dark red 105 %) are shared
   among the main roads leaving it, in proportion to how busy those are. That
   is the standard way to cut one area out of a city traffic model. Traffic
   that starts or ends inside the area is not included.

5. The score: every valid layout is simulated. All of that traffic is routed
   with A* in batches (assignment.py) on the area's roads as the layout leaves
   them, so a change really moves the flow. The score is total vehicle-hours in
   the area: lower is better.
"""

import math
from collections import Counter
from dataclasses import dataclass, field

from ..geo import haversine_m
from ..graph.areas import CHANGE_RADIUS_M, STUDY_RADIUS_M, edges_within, subgraph
from ..graph.road_graph import Edge, RoadGraph
from ..simulation.assignment import Assignment
from ..simulation.city import LEVEL_LOAD
from .csp import CSP

SIDE_STREETS = {"tertiary", "unclassified", "residential", "living_street"}
MAIN_ROADS = {"trunk", "primary", "secondary", "tertiary"}       # U-turn gaps, and the area's gates
MOST_JOURNEYS = 150             # the busiest gate-to-gate journeys routed when scoring
TURN_BACK_M = 200.0             # an exit this close to the entry is the same road turning back
U_TURN_MAX_M = 60.0
U_TURNS_APART_M = 400.0
STREET_VALUES = ["two-way", "one-way forward", "one-way backward", "closed"]
U_TURN_VALUES = ["open", "closed"]


@dataclass
class Option:
    key: str                    # "street 3", "u-turn 1"
    kind: str                   # "street" or "u-turn"
    edges: list[int]            # street: [forward, backward]; u-turn: its links
    name: str
    values: list[str]
    current: str
    geometry: list = field(default_factory=list)
    heading: str = ""           # street: the direction "forward" drives

    def describe(self, value: str) -> str:
        if self.kind == "u-turn":
            return f"Close the U-turn gap on {self.name}"
        if value == "closed":
            return f"Close {self.name} to traffic"
        back = {"north": "south", "south": "north", "east": "west", "west": "east",
                "north-east": "south-west", "south-west": "north-east",
                "north-west": "south-east", "south-east": "north-west"}
        towards = self.heading if value == "one-way forward" else back[self.heading]
        return f"Make {self.name} one-way, heading {towards}"


def compass(edge: Edge) -> str:
    (x1, y1), (x2, y2) = edge.geometry[0], edge.geometry[-1]
    angle = math.degrees(math.atan2((x2 - x1) * math.cos(math.radians(y1)), y2 - y1)) % 360
    return ["north", "north-east", "east", "south-east",
            "south", "south-west", "west", "north-west"][int((angle + 22.5) // 45) % 8]


def base_class(highway: str) -> str:
    return (highway or "").replace("_link", "")


def reachable(edges: dict, start: int, forward: bool = True) -> set:
    """Every node reachable from start (or that can reach start) over these edges."""
    step: dict[int, list[int]] = {}
    for e in edges.values():
        a, b = (e.u, e.v) if forward else (e.v, e.u)
        step.setdefault(a, []).append(b)
    seen, stack = {start}, [start]
    while stack:
        for nxt in step.get(stack.pop(), []):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


class RoadChangePlanner:
    def __init__(self, graph: RoadGraph, area: dict, levels, routes: list, route_vehicles: list,
                 names: dict | None = None, streets: int = 8):
        self.graph = graph
        self.area = area
        self.levels = levels
        self.names = names or {}
        self.study = subgraph(graph, edges_within(graph, area, STUDY_RADIUS_M))
        self.change_ids = set(edges_within(graph, area, CHANGE_RADIUS_M))

        # The connected core: junctions that can reach each other both ways
        # today. Found from the busiest junction: the nodes it reaches and that
        # reach it.
        degree = Counter([e.u for e in self.study.edges.values()] + [e.v for e in self.study.edges.values()])
        self.hub = degree.most_common(1)[0][0]
        self.core = reachable(self.study.edges, self.hub, True) & reachable(self.study.edges, self.hub, False)

        self.options = self._find_streets(streets) + self._find_u_turns()
        self._number_repeated_names()
        self.today = {o.key: o.current for o in self.options}     # each option as it is today
        self.trips = self._gate_demand() + self._trips_through(routes, route_vehicles)
        self._trap_cache: dict = {}

    # ── 1. options ─────────────────────────────────────────────────────────────

    def _name(self, edge_id: int) -> str:
        return self.names.get(edge_id) or f"an unnamed {base_class(self.graph.edges[edge_id].highway).replace('_', ' ')} street"

    def _find_streets(self, how_many: int) -> list[Option]:
        by_pair: dict[tuple, list[Edge]] = {}
        for edge_id in self.change_ids:
            e = self.graph.edges[edge_id]
            if base_class(e.highway) in SIDE_STREETS and e.u in self.core and e.v in self.core:
                by_pair.setdefault((min(e.u, e.v), max(e.u, e.v)), []).append(e)
        candidates = []
        for pair, edges in by_pair.items():
            forward = [e for e in edges if e.u == pair[0]]
            backward = [e for e in edges if e.u == pair[1]]
            if len(forward) == 1 and len(backward) == 1:       # exactly one street, both ways
                f, b = forward[0], backward[0]
                load = sum(LEVEL_LOAD[self.levels[e.id]] * e.capacity_vph for e in (f, b))
                candidates.append((load, f, b))
        candidates.sort(key=lambda c: -c[0])                     # the busiest first
        options = []
        for i, (_, f, b) in enumerate(candidates[:how_many], 1):
            options.append(Option(f"street {i}", "street", [f.id, b.id], self._name(f.id),
                                  STREET_VALUES, "two-way", f.geometry, compass(f)))
        return options

    def _number_repeated_names(self) -> None:
        """Two blocks of the same street are different options: number them."""
        counts = Counter(o.name for o in self.options)
        seen: Counter = Counter()
        for o in self.options:
            if counts[o.name] > 1:
                seen[o.name] += 1
                o.name = f"{o.name} (block {seen[o.name]})"

    def _find_u_turns(self) -> list[Option]:
        into: dict[int, list[Edge]] = {}
        out_of: dict[int, list[Edge]] = {}
        for e in self.study.edges.values():
            into.setdefault(e.v, []).append(e)
            out_of.setdefault(e.u, []).append(e)

        def bearing(e: Edge) -> float:
            (x1, y1), (x2, y2) = e.geometry[0], e.geometry[-1]
            return math.degrees(math.atan2((x2 - x1) * math.cos(math.radians(y1)), y2 - y1)) % 360

        def main_one_way(e: Edge) -> bool:
            return e.oneway and base_class(e.highway) in MAIN_ROADS

        gaps: dict[tuple, list[Edge]] = {}
        for edge_id in self.change_ids:
            link = self.graph.edges[edge_id]
            if link.length_m > U_TURN_MAX_M or link.u not in self.core or link.v not in self.core:
                continue
            arriving = [e for e in into.get(link.u, []) if main_one_way(e) and e.id != link.id]
            leaving = [e for e in out_of.get(link.v, []) if main_one_way(e) and e.id != link.id]
            # a U-turn: you arrive on one carriageway and leave on the other, heading back
            if any(abs((bearing(a) - bearing(b) + 180) % 360 - 180) > 150
                   for a in arriving for b in leaving):
                gaps.setdefault((min(link.u, link.v), max(link.u, link.v)), []).append(link)
        options = []
        for i, links in enumerate(gaps.values(), 1):
            main = next((e for e in into.get(links[0].u, []) if main_one_way(e)), links[0])
            options.append(Option(f"u-turn {i}", "u-turn", [e.id for e in links], self._name(main.id),
                                  U_TURN_VALUES, "open", links[0].geometry))
        return options

    # ── 2. rules ───────────────────────────────────────────────────────────────

    def layout_edges(self, layout: dict) -> dict:
        """The area's roads under a layout: edge id -> Edge, with removed
        directions gone and a one-way street's capacity doubled. Options not in
        the layout stay as they are today."""
        edges = dict(self.study.edges)
        for option in self.options:
            value = layout.get(option.key, option.current)
            if option.kind == "u-turn":
                if value == "closed":
                    for edge_id in option.edges:
                        edges.pop(edge_id, None)
                continue
            forward, backward = option.edges
            if value == "closed":
                edges.pop(forward, None)
                edges.pop(backward, None)
            elif value in ("one-way forward", "one-way backward"):
                keep, drop = (forward, backward) if value == "one-way forward" else (backward, forward)
                edges.pop(drop, None)
                e = edges[keep]
                edges[keep] = Edge(**{**e.__dict__, "capacity_vph": e.capacity_vph * 2})
        return edges

    def _changes(self, layout: dict) -> int:
        return sum(1 for key, value in layout.items() if value != self.today[key])

    def rule_budget(self, max_changes: int):
        return lambda layout: self._changes(layout) <= max_changes

    def rule_nobody_trapped(self, layout: dict) -> bool:
        # Undecided options count as they are today, which is the most open
        # they can be, so if the network is already broken nothing can mend it.
        changed = frozenset((k, v) for k, v in layout.items() if v != self.today[k])
        if changed not in self._trap_cache:
            edges = self.layout_edges(dict(changed))
            ok = (self.core <= reachable(edges, self.hub, True)
                  and self.core <= reachable(edges, self.hub, False)
                  and self._journeys_possible(edges))
            self._trap_cache[changed] = ok
        return self._trap_cache[changed]

    def rule_keep_a_way_to_turn(self, layout: dict) -> bool:
        closed = [o for o in self.options if o.kind == "u-turn" and layout.get(o.key) == "closed"]
        for i, a in enumerate(closed):
            for b in closed[i + 1:]:
                if haversine_m(*a.geometry[0], *b.geometry[0]) < U_TURNS_APART_M:
                    return False
        return True

    def problem(self, max_changes: int) -> CSP:
        return CSP(variables=[o.key for o in self.options],
                   domains={o.key: o.values for o in self.options},
                   constraints=[self.rule_budget(max_changes),        # cheapest first
                                self.rule_keep_a_way_to_turn,
                                self.rule_nobody_trapped])

    # ── 4. score ───────────────────────────────────────────────────────────────

    def _gate_demand(self) -> list[tuple]:
        """Traffic crossing the area: (enter, leave, vehicles per hour).

        A gate is a main road crossing the edge of the area. The vehicles on each
        road coming in are shared among the roads going out, in proportion to how
        busy the roads going out are; an exit within TURN_BACK_M of the entry is
        the same road turning back, so it is left out. The busiest MOST_JOURNEYS
        journeys are kept."""
        inside = set(self.study.nodes)
        arriving: Counter = Counter()
        departing: Counter = Counter()
        for e in self.graph.edges.values():
            if base_class(e.highway) not in MAIN_ROADS:
                continue
            vehicles = LEVEL_LOAD[self.levels[e.id]] * e.capacity_vph
            if e.u not in inside and e.v in inside:
                arriving[e.v] += vehicles
            elif e.u in inside and e.v not in inside:
                departing[e.u] += vehicles
        total_out = sum(departing.values())
        if not total_out:
            return []
        # a journey must be possible on today's roads; the one-way halves of a
        # divided road cannot always turn back inside the circle
        can_reach = {a: reachable(self.study.edges, a) for a in arriving}
        def apart(a, b):
            return haversine_m(*self.graph.nodes[a], *self.graph.nodes[b])
        # crossing the area, not turning straight back on the road you came in on
        journeys = [(a, b, arriving[a] * departing[b] / total_out)
                    for a in arriving for b in departing
                    if b in can_reach[a] and apart(a, b) > TURN_BACK_M]
        journeys.sort(key=lambda j: -j[2])
        self.gates = {"in": len(arriving), "out": len(departing),
                      "vehicles_arriving": round(sum(arriving.values()))}
        return journeys[:MOST_JOURNEYS]

    def _journeys_possible(self, edges: dict) -> bool:
        """Every journey that is possible today is still possible."""
        by_entry: dict[int, set] = {}
        for enter, leave, _ in self.trips:
            by_entry.setdefault(enter, set()).add(leave)
        return all(exits <= reachable(edges, enter) for enter, exits in by_entry.items())

    def _trips_through(self, routes: list, route_vehicles: list) -> list[tuple]:
        """The city trips that pass through the area: (enter, leave, vehicles),
        identical journeys added together so each is routed once."""
        inside = set(self.study.edges)
        through: Counter = Counter()
        for route, vehicles in zip(routes, route_vehicles):
            hits = [e for e in route if e in inside]
            if not hits:
                continue
            enter, leave = self.graph.edges[hits[0]].u, self.graph.edges[hits[-1]].v
            if enter != leave and enter in self.core and leave in self.core:
                through[(enter, leave)] += vehicles
        return [(a, b, v) for (a, b), v in sorted(through.items())]

    def score(self, layout: dict, batches: int = 4) -> dict:
        """Total vehicle-hours on the area's roads under this layout."""
        edges = self.layout_edges(layout)
        roads = RoadGraph()
        for e in edges.values():
            for node in (e.u, e.v):
                if node not in roads.nodes:
                    roads.add_node(node, *self.graph.nodes[node])
            roads.edges[e.id] = e
            roads.out[e.u].append(e.id)

        trips = [(a, b) for a, b, _ in self.trips]
        vehicles = [v for _, _, v in self.trips]
        run = Assignment(roads)                     # all the traffic is routed: nothing is fixed
        run.in_batches(trips, batches, vehicles)
        return {"vehicle_hours": run.vehicle_hours(), "unreachable": run.unreachable}

    # ── all together ───────────────────────────────────────────────────────────

    def plan(self, max_changes: int = 2, keep: int = 10, progress=None) -> dict:
        csp = self.problem(max_changes)
        if progress:
            progress.step("CSP", f"{len(self.options)} options, at most {max_changes} changes")
        layouts = csp.solve(progress)

        baseline = self.score({})
        results = []
        for n, layout in enumerate(layouts, 1):
            if progress:
                progress.step("A* scoring", f"layout {n:,} of {len(layouts):,}", n, len(layouts))
            changed = {k: v for k, v in layout.items() if v != self.today[k]}
            if not changed:
                continue
            result = self.score(changed)
            if result["unreachable"]:
                continue                           # a trip lost its way: not a real layout
            results.append((result["vehicle_hours"], changed))

        results.sort(key=lambda r: r[0])
        by_key = {o.key: o for o in self.options}
        best = [{"vehicle_hours": round(hours, 1),
                 "change_pct": round(100.0 * (hours - baseline["vehicle_hours"]) / baseline["vehicle_hours"], 2),
                 "changes": [{"option": key, "value": value, "text": by_key[key].describe(value)}
                             for key, value in sorted(changed.items())]}
                for hours, changed in results[:keep]]
        return {
            "options": [{"key": o.key, "kind": o.kind, "name": o.name, "values": o.values,
                         "current": o.current, "heading": o.heading,
                         "geometry": [[round(x, 5), round(y, 5)] for x, y in o.geometry]}
                        for o in self.options],
            "csp": {"variables": len(self.options), "max_changes": max_changes,
                    "choices_tried": csp.tried, "values_crossed_out": csp.crossed_out,
                    "dead_ends": csp.dead_ends, "valid_layouts": len(layouts)},
            "trips_through": len(self.trips),
            "vehicles_through": round(sum(v for _, _, v in self.trips)),
            "gates": getattr(self, "gates", {}),
            "baseline_vehicle_hours": round(baseline["vehicle_hours"], 1),
            "layouts_scored": len(results),
            "layouts_better": sum(1 for hours, _ in results if hours < baseline["vehicle_hours"]),
            "best": best,
        }
