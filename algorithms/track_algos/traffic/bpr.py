"""BPR volume-delay function: travel time as a function of volume over capacity.

    time = free_flow_time * (1 + ALPHA * (volume / capacity) ** BETA)

ALPHA is set so a dark red road (v/c 1.05) runs at a quarter of its free-flow
speed. With the textbook 0.15 the four traffic levels differ by under 20 % and
the router ignores traffic.
"""

JAM_VC = 1.05        # v/c ratio of a dark red road on TRACK's weight scale
JAM_FACTOR = 4.0     # how many times slower than free flow that road runs
BETA = 4.0
ALPHA = (JAM_FACTOR - 1.0) / JAM_VC ** BETA   # about 2.47


def bpr_time(free_flow_s: float, volume: float, capacity: float,
             alpha: float = ALPHA, beta: float = BETA) -> float:
    """Congested travel time in seconds."""
    if capacity <= 0:
        return float("inf")
    return free_flow_s * (1.0 + alpha * (volume / capacity) ** beta)


def bpr_time_from_ratio(free_flow_s: float, vc_ratio: float,
                        alpha: float = ALPHA, beta: float = BETA) -> float:
    """Congested travel time from a volume/capacity ratio."""
    return free_flow_s * (1.0 + alpha * vc_ratio ** beta)


def delay_factor(vc_ratio: float, alpha: float = ALPHA, beta: float = BETA) -> float:
    """How many times slower than free flow a road at this v/c ratio runs."""
    return 1.0 + alpha * vc_ratio ** beta
