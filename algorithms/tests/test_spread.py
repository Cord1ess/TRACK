"""The spread that fills every road Google does not paint.

The rule: a road touching a painted road takes its colour, then each junction
further out is one level milder, down to green. The nearest painted road
decides; equally near ones of different colours, the worse wins. A road with
no path to any painted road is green.

`reference` below applies that rule the slow, obvious way, one search from
every painted road, and the real predict() must agree with it exactly on
random networks, whatever order the roads are listed in.
"""

import random
from collections import deque

import numpy as np

from track_algos.traffic.impute import Config, _adjacency, predict

LAD = [25.0, 55.0, 85.0, 105.0]
G, O, R, D = 0, 1, 2, 3


def neighbours(us, vs):
    at = {}
    for i, (u, v) in enumerate(zip(us, vs)):
        at.setdefault(u, []).append(i)
        at.setdefault(v, []).append(i)
    nb = [set() for _ in us]
    for es in at.values():
        for i in es:
            nb[i].update(j for j in es if j != i)
    return [sorted(s) for s in nb]


def bfs(nbrs, s):
    d = [-1] * len(nbrs)
    d[s] = 0
    q = deque([s])
    while q:
        i = q.popleft()
        for j in nbrs[i]:
            if d[j] < 0:
                d[j] = d[i] + 1
                q.append(j)
    return d


def reference(nbrs, painted, per_rung=1, claim="nearest"):
    lost = lambda d: 0 if d <= 1 else -(-(d - 1) // per_rung)
    dist = {s: bfs(nbrs, s) for s in painted}
    out = []
    for j in range(len(nbrs)):
        if j in painted:
            out.append(LAD[painted[j]])
            continue
        c = [(dist[s][j], r) for s, r in painted.items() if dist[s][j] >= 0]
        if not c:
            out.append(25.0)
        elif claim == "nearest":
            dm = min(d for d, _ in c)
            out.append(LAD[max(0, max(r for d, r in c if d == dm) - lost(dm))])
        else:
            out.append(LAD[max(max(0, r - lost(d)) for d, r in c)])
    return out


def run(us, vs, painted, per_rung=1, claim="nearest"):
    n = len(us)
    w = np.zeros(n)
    m = np.zeros(n, dtype=bool)
    for i, r in painted.items():
        w[i], m[i] = LAD[r], True
    tab = {"adj": _adjacency(us, vs), "rank": np.ones(n), "xy": np.zeros((n, 2))}
    det = {}
    out, method = predict(tab, w, m, Config(hops_per_rung=per_rung, claim=claim), details=det)
    return list(out), list(method), list(det["hops"])


def chain(k):
    """k roads end to end: road i joins road i + 1."""
    return list(range(k)), list(range(1, k + 1))


def test_your_rule_red_red_orange_green():
    us, vs = chain(6)
    out, method, hops = run(us, vs, {0: R})
    assert out == [85.0, 85.0, 55.0, 25.0, 25.0, 25.0]
    assert hops == [0, 1, 2, 3, 4, 5]
    assert method == [0, 1, 2, 2, 2, 2]              # observed, adjacent, spread...


def test_dark_red_steps_down_one_level_per_junction():
    us, vs = chain(6)
    out, _, _ = run(us, vs, {0: D})
    assert out == [105.0, 105.0, 85.0, 55.0, 25.0, 25.0]


def test_nearest_painted_road_decides():
    # a clear road right next door beats a jam two junctions away
    us, vs = chain(4)
    out, _, _ = run(us, vs, {0: G, 3: D})
    assert out == [25.0, 25.0, 105.0, 105.0]


def test_tie_goes_to_the_worse_colour_in_either_order():
    us, vs = [0, 1, 1], [1, 2, 3]                    # road 1 touches roads 0 and 2... all share node 1
    for painted in ({0: G, 2: D}, {0: D, 2: G}):
        out, _, _ = run(us, vs, painted)
        assert out[1] == 105.0 and out[2 if painted[0] == D else 0] == 25.0


def test_blank_capture_is_all_clear_not_zero():
    us, vs = chain(5)
    out, method, hops = run(us, vs, {})
    assert out == [25.0] * 5
    assert method == [3] * 5 and hops == [-1] * 5


def test_unconnected_roads_are_assumed_clear():
    us, vs = [0, 1, 10, 11], [1, 2, 11, 12]          # two separate pieces
    out, method, hops = run(us, vs, {0: D})
    assert out == [105.0, 105.0, 25.0, 25.0]
    assert method[2:] == [3, 3] and hops[2:] == [-1, -1]


def test_painted_roads_never_change():
    us, vs = chain(5)
    out, _, _ = run(us, vs, {0: G, 1: D, 2: O})
    assert out[:3] == [25.0, 105.0, 55.0]


def test_adjacency_matches_brute_force():
    rng = random.Random(3)
    for _ in range(300):
        k = rng.randint(2, 20)
        us = [rng.randrange(k) for _ in range(rng.randint(1, 30))]
        vs = [rng.randrange(k) for _ in us]                          # self-loops allowed
        ptr, idx = _adjacency(us, vs)
        got = [sorted(idx[ptr[i]:ptr[i + 1]].tolist()) for i in range(len(us))]
        assert got == neighbours(us, vs)


def test_matches_reference_and_ignores_road_order():
    rng = random.Random(1)
    for _ in range(400):
        k = rng.randint(4, 25)
        us, vs = [], []
        for _ in range(rng.randint(3, 35)):
            u, v = rng.randrange(k), rng.randrange(k)
            if u != v:
                us.append(u); vs.append(v)
                if rng.random() < 0.6:
                    us.append(v); vs.append(u)
        if not us:
            continue
        n = len(us)
        painted = {i: rng.randrange(4) for i in rng.sample(range(n), rng.randint(0, max(1, n // 3)))}
        per_rung, claim = rng.choice([1, 2, 3]), rng.choice(["nearest", "worst"])
        out, _, _ = run(us, vs, painted, per_rung, claim)
        assert out == reference(neighbours(us, vs), painted, per_rung, claim)
        p = list(range(n))
        rng.shuffle(p)
        new = {old: i for i, old in enumerate(p)}
        out2, _, _ = run([us[i] for i in p], [vs[i] for i in p],
                         {new[i]: r for i, r in painted.items()}, per_rung, claim)
        assert [out2[new[i]] for i in range(n)] == out
