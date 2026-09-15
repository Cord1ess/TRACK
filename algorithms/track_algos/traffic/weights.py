"""TRACK's traffic weight: one number per road, higher is worse.

    green 25    yellow 55    red 85    dark red 105

weight / 100 is the volume/capacity ratio BPR uses. The weight is continuous,
so a road that is half green and half red averages to about 55 instead of
being forced into one class.

The demotion ladder is the assumption for roads Google never paints: a road
with no data beside a congested road is one level better than it.
"""

import numpy as np

from .bpr import bpr_time_from_ratio

WEIGHT = {0: 0.0, 1: 25.0, 2: 55.0, 3: 85.0, 4: 105.0}   # class id -> weight
CLASS_OF_WEIGHT = [(25.0, 1), (55.0, 2), (85.0, 3), (105.0, 4)]

LADDER = [25.0, 55.0, 85.0, 105.0]    # green, yellow, red, dark red
DEMOTED = [25.0, 25.0, 55.0, 85.0]    # each rung one step better
FLOOR, CEILING = 25.0, 105.0

# Google's fill colours at each rung, so our layer compares to the tiles by eye.
RAMP_HEX = ["#16e098", "#ffcf43", "#d1352b", "#a92727"]
_RAMP_RGB = np.asarray([[22, 224, 152], [255, 207, 67], [209, 53, 43], [169, 39, 39]], dtype=float)


def demote(weight):
    """One step down the ladder, interpolated. Scalar or array."""
    return np.interp(weight, LADDER, DEMOTED)


def vc_ratio(weight):
    """Weight to volume/capacity ratio."""
    return np.asarray(weight, dtype=float) / 100.0


def to_class(weight) -> int:
    """Nearest rung of the ladder."""
    w = float(weight)
    return min(CLASS_OF_WEIGHT, key=lambda p: abs(p[0] - w))[1]


def travel_time_s(free_flow_s: float, weight: float) -> float:
    """Congested travel time of an edge at this weight."""
    return bpr_time_from_ratio(free_flow_s, weight / 100.0)


def to_hex(weight) -> str:
    """Weight to a colour on Google's green/amber/red/dark-red ramp."""
    w = float(np.clip(weight, FLOOR, CEILING))
    rgb = [float(np.interp(w, LADDER, _RAMP_RGB[:, c])) for c in range(3)]
    return "#%02x%02x%02x" % tuple(int(round(v)) for v in rgb)


def to_hex_array(weights) -> list[str]:
    w = np.clip(np.asarray(weights, dtype=float), FLOOR, CEILING)
    cols = np.stack([np.interp(w, LADDER, _RAMP_RGB[:, c]) for c in range(3)], axis=1)
    return ["#%02x%02x%02x" % tuple(int(round(v)) for v in row) for row in cols]


def summary(weights) -> dict:
    """Count of weights per rung, plus mean and median."""
    w = np.asarray(weights, dtype=float)
    if w.size == 0:
        return {"n": 0}
    bounds = [0, 40, 70, 95, 1e9]
    names = ["green", "yellow", "red", "darkred"]
    counts = {n: int(((w >= bounds[i]) & (w < bounds[i + 1])).sum()) for i, n in enumerate(names)}
    return {"n": int(w.size), "mean": round(float(w.mean()), 1),
            "median": round(float(np.median(w)), 1), **counts}
