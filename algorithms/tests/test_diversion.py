"""The diversion's coordinator: it moves traffic off a jam onto a detour with
room, keeps every vehicle, never fills the detour past yellow, and never makes
the city's total time worse."""

import numpy as np

from track_algos.graph.road_graph import RoadGraph
from track_algos.simulation.diversion import YELLOW_VC, DiversionModel, run_diversion


def jam_with_a_detour():
    """A -> B: a short main road (jammed in the test) and a side street detour A -> C -> B."""
    g = RoadGraph()
    g.add_node(0, 90.400, 23.750)
    g.add_node(1, 90.402, 23.750)
    g.add_node(2, 90.401, 23.751)
    g.add_edge(0, 1, highway="primary", capacity_vph=2400, free_flow_kmph=50)   # edge 0: the main road
    g.add_edge(0, 2, highway="residential", capacity_vph=2400, free_flow_kmph=40)
    g.add_edge(2, 1, highway="residential", capacity_vph=2400, free_flow_kmph=40)
    return g


def test_moves_traffic_off_the_jam_and_keeps_every_vehicle():
    g = jam_with_a_detour()
    levels = np.array([3, 0, 0], dtype=np.uint8)              # main road dark red, detour green
    model = DiversionModel(g)
    actual = model.actual_load(levels)
    model.find_new_jams(actual)
    assert len(model.diversions) == 1 and model.diversions[0].detour == [1, 2]
    for _ in range(10):
        model.adjust(actual)
    load = model.with_track(actual)
    moved = actual[0] - load[0]
    assert moved > 0                                         # traffic left the jam
    assert np.allclose(load[1:] - actual[1:], moved)         # and every vehicle arrived on the detour
    assert (load[1:] / model.capacity[1:] <= YELLOW_VC + 1e-9).all()    # detour still yellow at most
    assert model.vehicle_hours(load) < model.vehicle_hours(actual)      # the city is faster


def test_nothing_moves_when_nothing_is_jammed():
    g = jam_with_a_detour()
    levels = np.array([0, 0, 0], dtype=np.uint8)
    result = run_diversion(g, [("dhaka-2026-09-22T1305Z", levels)])
    last = result["steps"][-1]
    assert result["detours"] == 0 and last["diverted_vph"] == 0
    assert last["actual"] == last["track"]
