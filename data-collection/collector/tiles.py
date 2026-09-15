"""Keeps score of what a capture actually got.

    coverage     how many of the tiles we wanted did we get
    audit_tiles  writes one row per tile: its size, a fingerprint, and how
                 much traffic is painted on it

The audit file is the record that every tile in a capture is listed and
accounted for, so a missing or altered tile can be spotted later.
"""

import csv
import gzip
import hashlib
import io
from pathlib import Path

import numpy as np

import analyze


def coverage(expected: set, received: set) -> dict:
    missing = sorted(expected - received)
    pct = 100.0 if not expected else round(100.0 * (len(expected) - len(missing)) / len(expected), 2)
    return {"expected": len(expected), "received": len(expected) - len(missing),
            "missing": missing, "coverage_pct": pct}


def _tile_stats(data: bytes, palette: dict, tolerance: float) -> dict:
    img = analyze.load_image(data).convert("RGBA")
    a = np.asarray(img)
    alpha = a[:, :, 3]
    opaque = alpha > 128
    n = alpha.size
    if not opaque.any():
        return {"opaque_frac": 0.0, "traffic_frac": 0.0, "coloured": 0, "matched": 0}
    rgb = a[:, :, :3][opaque].astype(np.int32)
    cls = analyze.classify_pixels(rgb, palette, tolerance)
    coloured = (rgb.max(axis=1) - rgb.min(axis=1)) > 40        # clearly a colour, not casing or grey
    return {"opaque_frac": round(float(opaque.sum() / n), 5),
            "traffic_frac": round(float((cls >= 0).sum() / n), 5),
            "coloured": int(coloured.sum()), "matched": int((cls[coloured] >= 0).sum())}


def audit_tiles(tiles: dict, expected: set, out_dir: Path, cfg: dict) -> dict:
    """Write one row per tile to tiles.csv.gz and add up the totals.

    Each row holds the tile's position, its size, a fingerprint of its
    contents and how much of it is painted. A tile we never got is listed as
    missing rather than left out, so the file always covers the whole area."""
    rows, sum_traffic, sum_opaque, nonempty, coloured, matched = [], 0.0, 0.0, 0, 0, 0
    for (x, y) in sorted(expected):
        data = tiles.get((x, y))
        if data is None:
            rows.append([x, y, 0, "", 0, 0, "missing"])
            continue
        st = _tile_stats(data, cfg["palette"], cfg["palette_tolerance"])
        sum_traffic += st["traffic_frac"]
        sum_opaque += st["opaque_frac"]
        nonempty += st["opaque_frac"] > 0
        coloured += st["coloured"]
        matched += st["matched"]
        rows.append([x, y, len(data), hashlib.sha256(data).hexdigest(),
                     st["opaque_frac"], st["traffic_frac"], "ok"])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["x", "y", "bytes", "sha256", "opaque_frac", "traffic_frac", "status"])
    w.writerows(rows)
    raw = gzip.compress(buf.getvalue().encode("utf-8"))
    (out_dir / "tiles.csv.gz").write_bytes(raw)
    n = len(expected) or 1
    return {"file": "tiles.csv.gz", "bytes": len(raw), "rows": len(rows),
            "tiles_ok": sum(1 for r in rows if r[-1] == "ok"), "tiles_nonempty": nonempty,
            "mean_opaque_frac": round(sum_opaque / n, 5),
            "mean_traffic_frac": round(sum_traffic / n, 5),
            "coloured_px": coloured, "matched_px": matched,
            "palette_match_frac": round(matched / coloured, 4) if coloured else 1.0}
