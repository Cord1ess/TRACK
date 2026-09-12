"""Tile bookkeeping for the TRACK collector: coverage against the expected
tile set, a per-tile audit file, and block mosaics for compact archiving.
"""

import csv
import gzip
import hashlib
import io
from pathlib import Path

import numpy as np
from PIL import Image

import analyze
from grid import tile_bounds


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
    """Write tiles.csv.gz (one row per expected tile: bytes, hash, content) and
    return content totals. The per-download proof that every tile in the
    dataset is listed with its hash."""
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


def block_origin(x: int, y: int, n: int) -> tuple[int, int]:
    return (x // n) * n, (y // n) * n


def block_canvas(tiles: dict, members, bx: int, by: int, tile_px: int, n: int) -> tuple:
    """Mosaic the member tiles of one n x n block whose origin tile is (bx, by).
    Returns (canvas, tiles present, [x, y] of tiles missing or unreadable)."""
    canvas = Image.new("RGBA", (n * tile_px, n * tile_px), (0, 0, 0, 0))
    present, missing = 0, []
    for (x, y) in sorted(members):
        data = tiles.get((x, y))
        if data is None:
            missing.append([x, y])
            continue
        try:
            tile = analyze.load_image(data).convert("RGBA")
        except Exception:
            missing.append([x, y])
            continue
        canvas.paste(tile, ((x - bx) * tile_px, (y - by) * tile_px))
        present += 1
    return canvas, present, missing


def build_blocks(tiles: dict, z: int, expected: set, out_dir: Path, cfg: dict,
                 tile_px: int) -> list[dict]:
    """Mosaic tiles into n x n tile blocks aligned to multiples of n. Returns
    manifest entries with bounds, content statistics and hashes."""
    n = cfg["block_tiles"]
    fmt = cfg.get("block_format", "rgba")
    blocks_dir = out_dir / "blocks"
    blocks_dir.mkdir(parents=True, exist_ok=True)

    groups: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for (x, y) in expected:
        groups.setdefault(block_origin(x, y, n), []).append((x, y))

    entries = []
    for (bx, by), members in sorted(groups.items()):
        canvas, present, missing = block_canvas(tiles, members, bx, by, tile_px, n)
        content = analyze.traffic_fraction(canvas, cfg["palette"], cfg["palette_tolerance"], sample=1024)
        img = analyze.quantize(canvas, cfg["palette"], cfg["palette_tolerance"]) if fmt == "palette" else canvas
        raw = analyze.png_bytes(img)
        name = f"z{z}_x{bx}_y{by}.png"
        (blocks_dir / name).write_bytes(raw)
        nb, sb = tile_bounds(bx, by, z), tile_bounds(bx + n - 1, by + n - 1, z)
        entries.append({
            "file": f"blocks/{name}", "z": z, "x0": bx, "y0": by, "n": n,
            "tile_px": tile_px, "px": n * tile_px,
            "bounds": {"north": nb["north"], "south": sb["south"], "west": nb["west"], "east": sb["east"]},
            "tiles_expected": len(members), "tiles_present": present, "tiles_missing": missing,
            "traffic_frac": content["traffic_frac"], "classes": content["classes"],
            "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
        })
    return entries
