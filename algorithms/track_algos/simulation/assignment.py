"""Traffic assignment: route many trips across the city and let them feel each other.

Every road starts with its background traffic (vehicles per hour, from the
forecast) and has a capacity. Its travel time comes from BPR: the fuller the
road, the slower it is. New trips are added in one of two ways:

    all_at_once   Every trip takes its fastest route on the roads as they are
                  now, and only then does all the load land. Everyone picks the
                  same "best" road, so that road jams. This is the baseline:
                  what happens when every driver follows the same app.

    in_batches    The trips are split into batches. Each batch is routed with A*
                  on the current travel times, its vehicles are added to the
                  roads it used, and the travel times are recomputed before the
                  next batch. Later batches see the jams the earlier ones made
                  and go around them.

The two are compared by total vehicle-hours (every vehicle's time on every road,
added up) and by how many roads end up over capacity.
"""

from ..graph.road_graph import Edge, RoadGraph
from ..search.astar import astar, haversine_heuristic
from ..traffic.bpr import bpr_time


class Assignment:
    def __init__(self, graph: RoadGraph, background: dict | None = None, progress=None):
        """background: edge id -> vehicles per hour already on that road."""
        self.graph = graph
        self.background = dict(background or {})
        self.volume = dict(self.background)      # what is on each road now
        self.heuristic = haversine_heuristic(graph)
        self.routes: list[list[int]] = []       # the edges each trip used, in trip order
        self.vehicles: list[float] = []         # how many vehicles each trip carries
        self.unreachable = 0                    # trips with no route at all
        self.searches = 0                       # A* searches actually run
        self.trips_done = 0                     # trips routed (a repeat journey reuses a search)
        self.trips_total = 0                    # trips this run will route, for the progress bar
        self.progress = progress                # optional: reports what is running

    # ── the cost of a road ────────────────────────────────────────────────────

    def travel_time(self, edge: Edge) -> float:
        """Seconds to drive this road with the vehicles on it now (BPR)."""
        return bpr_time(edge.free_flow_s, self.volume.get(edge.id, 0.0), edge.capacity_vph)

    def volume_over_capacity(self, edge_id: int) -> float:
        edge = self.graph.edges[edge_id]
        return self.volume.get(edge_id, 0.0) / edge.capacity_vph if edge.capacity_vph else 0.0

    # ── routing ───────────────────────────────────────────────────────────────

    def route(self, origin: int, destination: int) -> list[int] | None:
        """The fastest route right now, as a list of edge ids."""
        _, edges, _ = astar(self.graph, origin, destination, self.travel_time, self.heuristic)
        self.searches += 1
        return edges

    def add_load(self, route: list[int], vehicles: float) -> None:
        for edge_id in route:
            self.volume[edge_id] = self.volume.get(edge_id, 0.0) + vehicles

    def _route_all(self, trips: list[tuple]) -> list[list[int] | None]:
        """Route each trip on the current travel times, without adding any load.

        Trips with the same start and end get the same route: the travel times
        do not change until the load is added, so A* runs once per distinct
        journey. Trips start and end at zone centres, so journeys repeat a lot."""
        routes, found = [], {}
        for origin, destination in trips:
            journey = (origin, destination)
            if journey not in found:
                found[journey] = self.route(origin, destination)
            route = found[journey]
            routes.append(route)
            if route is None:
                self.unreachable += 1
            self.trips_done += 1
            if self.progress and self.trips_done % 20 == 0:
                self.progress.step("A*", f"trip {self.trips_done:,} of {self.trips_total:,} routed, "
                                         f"{self.searches:,} A* searches", self.trips_done, self.trips_total)
        return routes

    def _keep(self, routes, vehicles_per_trip) -> None:
        """Put each routed trip's vehicles on the roads it uses."""
        for i, route in enumerate(routes):
            if route is None:
                continue
            vehicles = vehicles_per_trip[i] if isinstance(vehicles_per_trip, list) else vehicles_per_trip
            self.add_load(route, vehicles)
            self.routes.append(route)
            self.vehicles.append(vehicles)

    def all_at_once(self, trips: list[tuple], vehicles_per_trip) -> None:
        """Route every trip on today's roads first, then add all the load."""
        self.trips_total = max(self.trips_total, self.trips_done + len(trips))
        self._keep(self._route_all(trips), vehicles_per_trip)

    def in_batches(self, trips: list[tuple], batches: int, vehicles_per_trip,
                   after_batch=None) -> None:
        """Route the trips batch by batch, adding each batch's load before the next.

        vehicles_per_trip: one number for every trip, or a list, one per trip.
        after_batch(number, self) is called once each batch has landed."""
        self.trips_total = max(self.trips_total, self.trips_done + len(trips))
        size = max(1, -(-len(trips) // max(1, batches)))      # ceiling division
        for number, start in enumerate(range(0, len(trips), size), 1):
            batch = trips[start:start + size]
            share = vehicles_per_trip[start:start + size] if isinstance(vehicles_per_trip, list) \
                else vehicles_per_trip
            self._keep(self._route_all(batch), share)
            if after_batch:
                after_batch(number, self)

    # ── measuring ─────────────────────────────────────────────────────────────

    def vehicle_hours(self) -> float:
        """Every vehicle's time on every road, added up, in hours."""
        return sum(volume * self.travel_time(self.graph.edges[edge_id])
                   for edge_id, volume in self.volume.items() if volume > 0) / 3600.0

    def roads_over_capacity(self) -> int:
        return sum(1 for edge_id, volume in self.volume.items()
                   if volume > 0 and self.volume_over_capacity(edge_id) > 1.0)

    def mean_trip_minutes(self) -> float:
        """Average time of the routed trips, on the roads as they are now."""
        if not self.routes:
            return 0.0
        total = sum(sum(self.travel_time(self.graph.edges[e]) for e in route) for route in self.routes)
        return total / len(self.routes) / 60.0

    def measure(self) -> dict:
        return {"vehicle_hours": round(self.vehicle_hours(), 1),
                "roads_over_capacity": self.roads_over_capacity(),
                "mean_trip_minutes": round(self.mean_trip_minutes(), 2),
                "searches": self.searches, "unreachable": self.unreachable}
