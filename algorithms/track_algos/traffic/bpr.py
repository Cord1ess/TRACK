"""BPR (Bureau of Public Roads) volume-delay function.

    travel_time = free_flow_time * (1 + alpha * (volume / capacity) ** beta)

Calibration
-----------
The textbook parameters are alpha=0.15, beta=4, fitted to American highways
where a road at capacity is only 15 % slower than an empty one. Dhaka is not
that road. With alpha=0.15 our four traffic levels come out almost identical:

    green   v/c 0.25 -> 1.00x      red      v/c 0.85 -> 1.08x
    yellow  v/c 0.55 -> 1.01x      dark red v/c 1.05 -> 1.18x

A router handed those costs ignores traffic completely and just takes the
shortest path, which would make the entire project inert. So alpha is
recalibrated by pinning the one point we can reason about: a dark red road is
at a standstill, roughly a quarter of its free-flow speed (a 50 km/h arterial
crawling at about 12 km/h, which matches what dark red looks like in Dhaka).
Solving 1 + alpha * 1.05**4 = 4 gives alpha ~= 2.47, and the four levels then
separate the way they should:

    green   1.01x     red      2.29x
    yellow  1.23x     dark red 4.00x

beta stays at 4: the shape of the curve (flat while a road is quiet, steep near
capacity) is what makes the assignment loop spread traffic, and that shape is
not in dispute. Only the depth of the jam was wrong.

JAM_FACTOR is the one number to tune if the simulation reroutes too eagerly or
too reluctantly; everything else follows from it.
"""

JAM_VC = 1.05        # the v/c ratio our weight scale assigns to dark red
JAM_FACTOR = 4.0     # a dark red road runs at 1/4 of its free-flow speed
BETA = 4.0
ALPHA = (JAM_FACTOR - 1.0) / JAM_VC ** BETA   # ~= 2.47


def bpr_time(free_flow_s: float, volume: float, capacity: float,
             alpha: float = ALPHA, beta: float = BETA) -> float:
    """Congested travel time in seconds for the given volume on an edge."""
    if capacity <= 0:
        return float("inf")
    return free_flow_s * (1.0 + alpha * (volume / capacity) ** beta)


def bpr_time_from_ratio(free_flow_s: float, vc_ratio: float,
                        alpha: float = ALPHA, beta: float = BETA) -> float:
    """Same, from a volume/capacity ratio (e.g. straight from a decoded weight)."""
    return free_flow_s * (1.0 + alpha * vc_ratio ** beta)


def delay_factor(vc_ratio: float, alpha: float = ALPHA, beta: float = BETA) -> float:
    """How many times slower than free flow a road at this v/c ratio runs."""
    return 1.0 + alpha * vc_ratio ** beta
