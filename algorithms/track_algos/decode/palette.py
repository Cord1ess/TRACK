"""Google's traffic colours and how a pixel is classified.

Class ids: 0 none, 1 green, 2 orange, 3 red, 4 dark red.

The colours were measured on the 2026-09-12 capture. Google draws each line as
a fill with a darker border (green, amber) or inside a white casing (red, dark
red), so each class lists its fill and border. A pixel takes the class of its
nearest reference colour within TOLERANCE. Pixels where a red fill blends into
its white casing miss every reference; `casing_blend` recovers them.
"""

import numpy as np

PALETTE = {  # name: (class id, reference colours: fill first, then border)
    "green":   (1, ((22, 224, 152), (4, 156, 101))),
    "orange":  (2, ((255, 207, 67), (245, 192, 37))),
    "red":     (3, ((209, 53, 43), (242, 78, 66), (152, 66, 58))),
    "darkred": (4, ((169, 39, 39), (112, 35, 35))),
}
CLASS_NAMES = {0: "none", 1: "green", 2: "orange", 3: "red", 4: "darkred"}
VC_RATIO = {0: 0.0, 1: 0.3, 2: 0.6, 3: 1.0, 4: 1.2}
COLORS_HEX = {0: "#9e9e9e", 1: "#16e098", 2: "#ffcf43", 3: "#d1352b", 4: "#a92727"}
TOLERANCE = 50.0
CASING_T_MIN = 0.4   # a casing-blend pixel counts when the fill covers at least 40 % of it

_RGB = np.asarray([c for _, refs in PALETTE.values() for c in refs], dtype=np.int32)
_IDS = np.asarray([cid for cid, refs in PALETTE.values() for _ in refs], dtype=np.int8)


def casing_blend(rgb: np.ndarray, refs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Fit each pixel as p = t * C + (1 - t) * white for every reference C.

    Returns (index of the best fitting reference, t). t is the fill coverage,
    1 pure fill, 0 pure casing; a poor fit gives t = 0.
    """
    p = rgb.astype(np.float64)
    denom = 255.0 - refs.astype(np.float64)                             # (K,3)
    valid = denom > 8.0                                                 # channels near white say nothing
    t = (255.0 - p[:, None, :]) / np.where(valid, denom, 1.0)[None]     # (N,K,3)
    t = np.where(valid[None], t, 0.0)
    tm = t.sum(axis=2) / valid.sum(axis=1)[None, :]                     # (N,K)
    spread = (np.abs(t - tm[:, :, None]) * valid[None]).max(axis=2)
    tm = np.where((spread < 0.08) & (tm >= 0.0) & (tm <= 1.0), tm, 0.0)
    best = tm.argmax(axis=1)
    return best, tm[np.arange(len(best)), best]


def classify(rgba: np.ndarray, tolerance: float = TOLERANCE) -> np.ndarray:
    """(N,4) uint8 pixels to (N,) class ids. Transparent or off-palette is 0."""
    rgba = np.asarray(rgba)
    out = np.zeros(rgba.shape[0], dtype=np.int8)
    opaque = rgba[:, 3] > 128
    if not opaque.any():
        return out
    rgb = rgba[opaque, :3].astype(np.int32)
    d = ((rgb[:, None, :] - _RGB[None, :, :]) ** 2).sum(axis=2)
    best = d.argmin(axis=1)
    ok = d[np.arange(len(best)), best] < tolerance ** 2
    cls = np.where(ok, _IDS[best], 0).astype(np.int8)
    miss = np.flatnonzero(~ok)
    if miss.size:
        idx, t = casing_blend(rgb[miss], _RGB)
        cls[miss] = np.where(t >= CASING_T_MIN, _IDS[idx], 0)
    out[opaque] = cls
    return out
