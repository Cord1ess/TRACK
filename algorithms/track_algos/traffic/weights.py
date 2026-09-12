"""The traffic weight scale: one number per road segment, higher = worse.

    green 25    yellow 55    red 85    dark red 105

This is TRACK's own measurement unit, not Google's. A Google tile only tells us
which of four colours a road was painted; the weight turns that into something
continuous that can be averaged along a road, interpolated between roads,
predicted for roads that have no data at all, and fed to BPR.

Reading the scale
-----------------
weight / 100 is the volume/capacity ratio BPR wants, so 105 means a road
carrying slightly more than it can handle, which is what dark red means. The
numbers are deliberately not evenly spaced: the gap from green to yellow is
large because that is where a road stops being free, and the gap from red to
dark red is small because both are already saturated.

Why a continuous weight rather than the colour class
----------------------------------------------------
A 150 m edge is rarely one pure colour. Labelling it by its dominant colour
throws away the mixture and makes an edge that is half green and half red look
identical to one that is entirely red. Averaging the weight over the sampled
points puts that edge near 55, which is both more honest and more useful: the
router sees a road that is partly blocked, not a wall.

The demotion ladder
-------------------
Most of Dhaka's road length has no Google data, and those roads still have to
be routable or there is nowhere to divert a jam to. The working assumption is
that a road with no data next to a congested road is one level better than it:
a side lane beside a dark red arterial is red, beside a red one it is yellow,
beside a yellow one it is green, and green stays green. `demote` applies that
on the continuous scale by interpolating along the ladder.

This is a prior, not a measurement. `measure_class_step` in impute.py checks it
against the roads that DO have data, and the hold-out evaluation reports what
it costs in accuracy.
"""

import numpy as np

from .bpr import bpr_time_from_ratio

# class id (see decode/palette.py) -> weight
WEIGHT = {0: 0.0, 1: 25.0, 2: 55.0, 3: 85.0, 4: 105.0}
CLASS_OF_WEIGHT = [(25.0, 1), (55.0, 2), (85.0, 3), (105.0, 4)]

LADDER = [25.0, 55.0, 85.0, 105.0]    # green, yellow, red, dark red
DEMOTED = [25.0, 25.0, 55.0, 85.0]    # each rung one step better
FLOOR, CEILING = 25.0, 105.0

# Google's own fill colours at each rung, so our layer can be compared to the
# captured tiles by eye without a mental colour-key translation.
RAMP_HEX = ["#16e098", "#ffcf43", "#d1352b", "#a92727"]
_RAMP_RGB = np.asarray([[22, 224, 152], [255, 207, 67], [209, 53, 43], [169, 39, 39]], dtype=float)


def demote(weight):
    """One step down the ladder, continuously. Scalar or array."""
    return np.interp(weight, LADDER, DEMOTED)


def vc_ratio(weight):
    """Weight -> volume/capacity ratio for BPR."""
    return np.asarray(weight, dtype=float) / 100.0 if np.ndim(weight) else weight / 100.0


def to_class(weight) -> int:
    """Nearest rung of the ladder, for reporting and for colouring by class."""
    w = float(weight)
    return min(CLASS_OF_WEIGHT, key=lambda p: abs(p[0] - w))[1]


def travel_time_s(free_flow_s: float, weight: float) -> float:
    """Congested travel time of an edge at this weight."""
    return bpr_time_from_ratio(free_flow_s, weight / 100.0)


def to_hex(weight) -> str:
    """Weight -> a colour on Google's own green/amber/red/dark-red ramp."""
    w = float(np.clip(weight, FLOOR, CEILING))
    rgb = [float(np.interp(w, LADDER, _RAMP_RGB[:, c])) for c in range(3)]
    return "#%02x%02x%02x" % tuple(int(round(v)) for v in rgb)


def to_hex_array(weights) -> list[str]:
    w = np.clip(np.asarray(weights, dtype=float), FLOOR, CEILING)
    cols = np.stack([np.interp(w, LADDER, _RAMP_RGB[:, c]) for c in range(3)], axis=1)
    return ["#%02x%02x%02x" % tuple(int(round(v)) for v in row) for row in cols]


def summary(weights) -> dict:
    """Distribution of a set of weights by ladder rung, for reports."""
    w = np.asarray(weights, dtype=float)
    if w.size == 0:
        return {"n": 0}
    edges = [0, 40, 70, 95, 1e9]                      # green | yellow | red | dark red
    names = ["green", "yellow", "red", "darkred"]
    counts = {n: int(((w >= edges[i]) & (w < edges[i + 1])).sum()) for i, n in enumerate(names)}
    return {"n": int(w.size), "mean": round(float(w.mean()), 1),
            "median": round(float(np.median(w)), 1), **counts}
