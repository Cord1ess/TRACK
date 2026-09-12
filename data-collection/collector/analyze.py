"""Image analysis for the TRACK collector: traffic-colour classification,
per-image traffic fraction, palette measurement and palette quantisation.
Works on Pillow images; heavy lifting in numpy.
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
    """Normalise a palette to [(class name, (k,3) reference colours)].
    config.json may give one colour per class ([r,g,b]) or several
    ([[r,g,b], ...]): Google draws every line as a fill plus a darker border of
    the same hue, so a class lists both and anti-aliased blends still match."""
    out = []
    for name, v in palette.items():
        arr = np.asarray(v, dtype=np.int32)
        out.append((name, arr.reshape(-1, 3)))
    return out


CASING_T_MIN = 0.4   # a casing-blend pixel counts for its class when the fill covers >= 40% of it


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


def classify_pixels(rgb: np.ndarray, palette: dict, tolerance: float) -> np.ndarray:
    """rgb: (N,3). Returns (N,) int8 class index into palette order, -1 = no match.
    1. A pixel takes the class of its nearest reference colour (euclidean in
       RGB) if that colour is within `tolerance`.
    2. A pixel that misses every reference may still be the edge of a cased
       line, fill blended with white casing (see casing_blend): it takes that
       fill's class when the fill covers at least CASING_T_MIN of it.
    Pure casing, grey and black fail both steps and return -1."""
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
    """Palette-drift detector: of the opaque, clearly coloured pixels
    (saturation > 40), what fraction classifies into some class? Near 100%
    on a healthy capture; a Google restyle shows up here first."""
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
    """Fraction of all pixels that match a traffic palette colour, per class.
    Downsamples large images for speed."""
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
    """Most common opaque saturated colours: used to measure Google's actual
    palette on a real capture instead of assuming it."""
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
