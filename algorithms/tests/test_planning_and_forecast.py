"""The CSP solver and the forecast vote."""

import numpy as np

from track_algos.forecast.predict import majority_level, time_features
from track_algos.planning.csp import CSP


def test_csp_finds_exactly_the_valid_assignments():
    # three switches, at most one on, and switch c may never be on
    variables = ["a", "b", "c"]
    domains = {v: ["off", "on"] for v in variables}
    at_most_one = lambda asg: sum(v == "on" for v in asg.values()) <= 1
    never_c = lambda asg: asg.get("c") != "on"
    csp = CSP(variables, domains, [at_most_one, never_c])
    found = csp.solve()
    as_sets = {tuple(sorted(s.items())) for s in found}
    assert as_sets == {(("a", "off"), ("b", "off"), ("c", "off")),
                       (("a", "on"), ("b", "off"), ("c", "off")),
                       (("a", "off"), ("b", "on"), ("c", "off"))}
    assert csp.crossed_out > 0                    # forward checking did remove values


def test_csp_with_no_answer_finds_no_solutions():
    csp = CSP(["a"], {"a": [1, 2]}, [lambda asg: asg.get("a") not in (1, 2)])
    assert csp.solve() == []


def test_majority_vote_breaks_ties_toward_the_worse_level():
    neighbours = np.array([[0, 3, 1, 2],
                           [0, 0, 1, 2],
                           [1, 3, 2, 0],
                           [1, 0, 2, 0]], dtype=np.uint8)
    assert majority_level(neighbours).tolist() == [1, 3, 2, 2]


def test_time_of_day_wraps_around_midnight():
    f = time_features([1430, 10, 720], [0, 0, 0])
    near = np.linalg.norm(f[0] - f[1])           # 11:50 pm and 12:10 am
    far = np.linalg.norm(f[0] - f[2])            # 11:50 pm and noon
    assert near < 0.2 < far
    weekend = time_features([600], [1])[0]
    assert np.linalg.norm(weekend - time_features([600], [0])[0]) == 3.0
