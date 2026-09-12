"""Google traffic colour classes and pixel classification.

Class ids are the file contract used everywhere downstream:
    0 none, 1 green (free flow), 2 orange (Google draws it amber), 3 red, 4 dark red.
`VC_RATIO` is the volume/capacity estimate per class from docs/idea.md, the
bridge from a colour to a number BPR can use.

The reference colours were MEASURED on the 2026-09-12 test capture (2.7 M
opaque pixels), not copied from a legend. Google's current style draws every
line as a fill with a darker border of the same hue (green, amber) or inside a
white casing (red, dark red; red also has a bordered variant #f24e42/#98423a).
Anti-aliasing blends fill and border, so each class lists its fill and border
colours and a pixel takes the class of its nearest reference colour if that is
within TOLERANCE. Pure white casing is farther than TOLERANCE from every
reference and decodes as "none"; the pinkish pixels where a red fill blends
into its casing are recovered by `casing_blend` (fill coverage t) and count for
the fill's class when t >= CASING_T_MIN, so thin cased lines keep their width.
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
COLORS_HEX = {0: "#9e9e9e", 1: "#16e098", 2: "#ffcf43", 3: "#d1352b", 4: "#a92727"}  # the fills
TOLERANCE = 50.0
CASING_T_MIN = 0.4   # a casing-blend pixel counts for its class when the fill covers >= 40% of it

_RGB = np.asarray([c for _, refs in PALETTE.values() for c in refs], dtype=np.int32)
_IDS = np.asarray([cid for cid, refs in PALETTE.values() for _ in refs], dtype=np.int8)


def casing_blend(rgb: np.ndarray, refs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Google draws red and dark red inside a WHITE casing, so a line's edge
    pixels are fill blended with white: p = t*C + (1-t)*white. For every pixel
    fit t against each reference colour C (channels where C is itself near
    white carry no information and are skipped) and return (index of the best
    fitting reference, t). t is the fill coverage: 1 = pure fill, 0 = pure
    casing; a poor fit (channels disagree by more than 0.08) gives t = 0."""
    p = rgb.astype(np.float64)
    denom = 255.0 - refs.astype(np.float64)                             # (K,3)
    valid = denom > 8.0
    t = (255.0 - p[:, None, :]) / np.where(valid, denom, 1.0)[None]     # (N,K,3)
    t = np.where(valid[None], t, 0.0)
    tm = t.sum(axis=2) / valid.sum(axis=1)[None, :]                     # (N,K)
    spread = (np.abs(t - tm[:, :, None]) * valid[None]).max(axis=2)
    tm = np.where((spread < 0.08) & (tm >= 0.0) & (tm <= 1.0), tm, 0.0)
    best = tm.argmax(axis=1)
    return best, tm[np.arange(len(best)), best]


def classify(rgba: np.ndarray, tolerance: float = TOLERANCE) -> np.ndarray:
    """rgba: (N,4) uint8 -> (N,) class ids. Transparent or off-palette -> 0."""
    rgba = np.asarray(rgba)
    out = np.zeros(rgba.shape[0], dtype=np.int8)
    opaque = rgba[:, 3] > 128
    if not opaque.any():
        return out
    rgb = rgba[opaque, :3].astype(np.int32)
    d = ((rgb[:, None, :] - _RGB[None, :, :]) ** 2).sum(axis=2)   # (n, references)
    best = d.argmin(axis=1)
    ok = d[np.arange(len(best)), best] < tolerance ** 2
    cls = np.where(ok, _IDS[best], 0).astype(np.int8)
    miss = np.flatnonzero(~ok)                                     # maybe a cased line's edge
    if miss.size:
        idx, t = casing_blend(rgb[miss], _RGB)
        cls[miss] = np.where(t >= CASING_T_MIN, _IDS[idx], 0)
    out[opaque] = cls
    return out
