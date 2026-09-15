"""Reads the colours in a traffic tile.

Google paints traffic as coloured lines: green, amber, red, dark red. This
file decides which of those a pixel is, and how much of a tile is painted.
Everything else in the collector uses it to check a capture is sound.

    classify_pixels   which traffic colour each pixel is, or none
    traffic_fraction  how much of one image is painted, per colour
    palette_match     what share of coloured pixels we still recognise
    measure_colours   the commonest colours actually present, to check ours
"""

import io
from collections import Counter

import numpy as np
from PIL import Image


def load_image(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def image_info(data: bytes) -> dict:
    """Format, size, mode and whether the image has real transparency."""
    img = load_image(data)
    info = {"format": img.format, "w": img.width, "h": img.height, "mode": img.mode,
            "transparent": False, "alpha_min": 255, "alpha_zero_frac": 0.0}
    if img.mode in ("RGBA", "LA", "P"):
        a = np.asarray(img.convert("RGBA"))[:, :, 3]
        info["alpha_min"] = int(a.min())
        info["alpha_zero_frac"] = round(float((a == 0).mean()), 4)
        info["transparent"] = bool(a.min() < 255)
    return info


def palette_refs(palette: dict) -> list[tuple[str, np.ndarray]]:
    """Put the palette from config.json into one shape: a list of
    (colour name, array of reference colours).

    A colour can list several references because Google draws each line as a
    bright fill with a darker border of the same hue. Listing both means the
    border is recognised as the same traffic level, not discarded."""
    out = []
    for name, v in palette.items():
        arr = np.asarray(v, dtype=np.int32)
        out.append((name, arr.reshape(-1, 3)))
    return out


CASING_T_MIN = 0.4   # an edge pixel counts only if the colour fills at least 40% of it


def casing_blend(rgb: np.ndarray, refs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Rescue the pale pixels along the edge of a line.

    Red and dark red lines are drawn inside a white outline, so the pixels at
    a line's edge are part colour and part white and match no reference
    exactly. For each pixel this works out how much of it is colour: 1 means
    pure colour, 0 means pure white. Returns which colour it looks like and
    how much of it there is. A pixel whose channels disagree is not a blend of
    anything and gets 0."""
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


def classify_pixels(rgb: np.ndarray, palette: dict, tolerance: float) -> np.ndarray:
    """Decide which traffic colour each pixel is. Returns one number per
    pixel: a position in the palette, or -1 for no traffic colour.

    Two passes. First, a pixel takes the closest reference colour, if it is
    close enough. Second, a pixel that matched nothing may still be the pale
    edge of a line, so casing_blend gets a look at it. White, grey and black
    fail both and come back as -1."""
    rgb = np.asarray(rgb, dtype=np.int32).reshape(-1, 3)
    flat = [(i, c) for i, (_, refs) in enumerate(palette_refs(palette)) for c in refs]
    cls_of_ref = np.asarray([i for i, _ in flat], dtype=np.int8)
    refs = np.asarray([c for _, c in flat], dtype=np.int32)
    best = np.full(rgb.shape[0], -1, dtype=np.int8)
    best_d = np.full(rgb.shape[0], float(tolerance) ** 2 + 1, dtype=np.float64)
    for k, c in enumerate(refs):
        d = ((rgb - c) ** 2).sum(axis=1)
        hit = d < best_d
        best[hit] = cls_of_ref[k]
        best_d[hit] = d[hit]
    miss = np.flatnonzero(best < 0)
    if miss.size:
        idx, t = casing_blend(rgb[miss], refs)
        best[miss] = np.where(t >= CASING_T_MIN, cls_of_ref[idx], -1)
    return best


def palette_match(img: Image.Image, palette: dict, tolerance: float) -> dict:
    """Of the pixels that are clearly some colour, what share do we recognise?

    Near 100% on a healthy capture. If Google restyles its traffic layer, this
    drops, and it is the first place that shows. The capture still keeps its
    tiles: only the colour names would be wrong, and reaudit.py can redo them."""
    rgba = np.asarray(img.convert("RGBA")).reshape(-1, 4)
    rgb = rgba[rgba[:, 3] > 128][:, :3].astype(np.int32)
    coloured = rgb[(rgb.max(axis=1) - rgb.min(axis=1)) > 40]
    if coloured.shape[0] == 0:
        return {"coloured": 0, "matched": 0, "frac": 1.0}
    matched = int((classify_pixels(coloured, palette, tolerance) >= 0).sum())
    return {"coloured": int(coloured.shape[0]), "matched": matched,
            "frac": round(matched / coloured.shape[0], 4)}


def traffic_fraction(img: Image.Image, palette: dict, tolerance: float,
                     sample: int = 512) -> dict:
    """What share of an image is painted with traffic, and in which colours.
    Large images are shrunk first, since a share does not need every pixel."""
    if max(img.size) > sample:
        img = img.copy()
        img.thumbnail((sample, sample), Image.NEAREST)
    rgba = np.asarray(img.convert("RGBA"))
    alpha = rgba[:, :, 3].reshape(-1)
    rgb = rgba[:, :, :3].reshape(-1, 3)
    opaque = alpha > 128
    n = rgb.shape[0]
    if not opaque.any():
        return {"traffic_frac": 0.0, "opaque_frac": 0.0, "classes": {}}
    cls = classify_pixels(rgb[opaque], palette, tolerance)
    names = list(palette.keys())
    counts = Counter(cls.tolist())
    classes = {names[k]: round(v / n, 5) for k, v in counts.items() if k >= 0}
    return {"traffic_frac": round(sum(classes.values()), 5),
            "opaque_frac": round(float(opaque.mean()), 4), "classes": classes}


def measure_colours(img: Image.Image, top: int = 12, sample: int = 1024) -> list[dict]:
    """The commonest colours actually in an image. Used to read Google's real
    palette off a capture rather than trusting the values in config.json."""
    if max(img.size) > sample:
        img = img.copy()
        img.thumbnail((sample, sample), Image.NEAREST)
    rgba = np.asarray(img.convert("RGBA")).reshape(-1, 4)
    rgba = rgba[rgba[:, 3] > 200]
    if rgba.shape[0] == 0:
        return []
    rgb = rgba[:, :3].astype(np.int32)
    rgb = rgb[(rgb.max(axis=1) - rgb.min(axis=1)) > 40]  # drop greys / whites
    if rgb.shape[0] == 0:
        return []
    q = (rgb // 8) * 8 + 4
    keys, counts = np.unique(q, axis=0, return_counts=True)
    order = np.argsort(-counts)[:top]
    total = rgb.shape[0]
    return [{"rgb": [int(v) for v in keys[i]], "frac": round(float(counts[i] / total), 4)}
            for i in order]


def quantize(img: Image.Image, palette: dict, tolerance: float) -> Image.Image:
    """RGBA -> paletted PNG: index 0 transparent, 1..n palette classes,
    n+1 'other opaque'. Several times smaller; lossy on anti-aliased edges."""
    rgba = np.asarray(img.convert("RGBA"))
    h, w = rgba.shape[:2]
    alpha = rgba[:, :, 3].reshape(-1)
    rgb = rgba[:, :, :3].reshape(-1, 3)
    idx = np.zeros(h * w, dtype=np.uint8)
    opaque = alpha > 128
    if opaque.any():
        cls = classify_pixels(rgb[opaque], palette, tolerance)
        idx[opaque] = np.where(cls >= 0, cls + 1, len(palette) + 1).astype(np.uint8)
    out = Image.fromarray(idx.reshape(h, w), mode="P")
    pal = [0, 0, 0] + [int(c) for _, refs in palette_refs(palette) for c in refs[0]] + [128, 128, 128]
    pal += [0] * (768 - len(pal))
    out.putpalette(pal)
    out.info["transparency"] = 0
    return out


def png_bytes(img: Image.Image, optimize: bool = True) -> bytes:
    buf = io.BytesIO()
    kw = {"optimize": optimize}
    if img.mode == "P" and img.info.get("transparency") is not None:
        kw["transparency"] = img.info["transparency"]
    img.save(buf, "PNG", **kw)
    return buf.getvalue()
