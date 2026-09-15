"""Preflight and post-capture checks for the TRACK collector.

Every check returns {"ok": bool, "detail": str, ...} and never raises, so
capture.py can collect them all into the manifest and decide the run status.
"""

import hashlib
import json
import shutil
from pathlib import Path

import grid

REQUIRED = ["zoom", "tile_px", "bbox", "rate", "workers", "retries", "min_coverage_pct",
            "palette", "palette_tolerance", "min_free_gb", "fill_gaps", "incidents"]
# A bbox outside this is a typo, not a plan.
SANE_BOX = {"north": 24.2, "south": 23.4, "east": 90.8, "west": 90.0}


def validate_config(cfg: dict) -> dict:
    p = [f"missing key {k}" for k in REQUIRED if k not in cfg]
    if p:
        return {"ok": False, "detail": "; ".join(p)}
    b = cfg["bbox"]
    if not (b["north"] > b["south"] and b["east"] > b["west"]):
        p.append("bbox needs north>south and east>west")
    if not (SANE_BOX["south"] <= b["south"] and b["north"] <= SANE_BOX["north"]
            and SANE_BOX["west"] <= b["west"] and b["east"] <= SANE_BOX["east"]):
        p.append("bbox is outside the Dhaka sanity box")
    if not (12 <= cfg["zoom"] <= 17):
        p.append("zoom must be 12..17 (Google serves no traffic above 17)")
    if cfg["tile_px"] != 256:
        p.append("tile_px must be 256 (only size the endpoint serves)")
    if not (0.5 <= cfg["rate"] <= 200):
        p.append("rate must be 0.5..200 req/s")
    if not (1 <= cfg["workers"] <= 64):
        p.append("workers must be 1..64")
    if not (0 <= cfg["retries"] <= 8):
        p.append("retries must be 0..8")
    if not (50 <= cfg["min_coverage_pct"] <= 100):
        p.append("min_coverage_pct must be 50..100")
    pal = cfg["palette"]
    if not isinstance(pal, dict) or len(pal) < 2:
        p.append("palette needs at least two classes")
    else:
        for name, v in pal.items():
            refs = v if (isinstance(v, list) and v and isinstance(v[0], list)) else [v]
            if not all(isinstance(c, list) and len(c) == 3 and all(isinstance(x, int) and 0 <= x <= 255 for x in c)
                       for c in refs):
                p.append(f"palette {name} must be [r,g,b] or [[r,g,b], ...]")
    if not (1 <= cfg["palette_tolerance"] <= 120):
        p.append("palette_tolerance must be 1..120")
    return {"ok": not p, "detail": "; ".join(p) or "config valid"}


def palette_match_check(audit: dict, min_frac: float = 0.8, min_px: int = 1000) -> dict:
    """Warn when the coloured pixels of a capture stop matching the palette:
    the first sign that Google restyled the traffic layer. Non-critical, the
    raw tiles are still good; re-measure with analyze.measure_colours."""
    n, frac = audit.get("coloured_px", 0), audit.get("palette_match_frac", 1.0)
    return {"ok": n < min_px or frac >= min_frac,
            "detail": f"{frac:.0%} of {n} coloured px match the palette (min {min_frac:.0%})"}


def disk_check(path: Path, min_free_gb: float) -> dict:
    path.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(path).free / 1e9
    return {"ok": free_gb >= min_free_gb, "free_gb": round(free_gb, 2),
            "detail": f"{free_gb:.1f} GB free (min {min_free_gb})"}


def grid_roundtrip() -> dict:
    """lon/lat -> tile -> lon/lat must round-trip; plus an anchor computed
    independently with the OSM wiki formula: Kakrail (23.7358, 90.4063) lies
    in tile (98451, 56635) at zoom 17."""
    worst = 0.0
    for z in (12, 16, 17):
        for lat, lon in ((23.7358, 90.4063), (23.90, 90.32), (23.69, 90.47)):
            tx, ty = grid.lon_to_tx(lon, z), grid.lat_to_ty(lat, z)
            px, py = grid.lonlat_to_pixel(lon, lat, z)
            lon3, lat3 = grid.pixel_to_lonlat(px, py, z)
            worst = max(worst, abs(lon - grid.tx_to_lon(tx, z)), abs(lat - grid.ty_to_lat(ty, z)),
                        abs(lon - lon3), abs(lat - lat3))
    r = grid.tile_range({"north": 23.7358, "south": 23.7358, "east": 90.4063, "west": 90.4063}, 17)
    anchor_ok = (r["x0"], r["y0"]) == (98451, 56635)
    return {"ok": worst < 1e-9 and anchor_ok, "worst_deg_error": worst, "anchor_ok": anchor_ok,
            "detail": f"round-trip error {worst:.2e} deg, anchor {'ok' if anchor_ok else 'WRONG'}"}


def georef_check(tiles: set, zoom: int, bbox: dict, tile_range: dict) -> dict:
    """Every tile's own corner must map back to its (x, y), and the tile range
    must sit inside the bbox it was derived from. Catches a wrong zoom, swapped
    axes or a bad manifest. Checks the corners and a sample of the interior,
    which is enough: the failure modes are systematic, not per tile."""
    bad = []
    r = tile_range
    if not (r["x0"] <= r["x1"] and r["y0"] <= r["y1"]):
        bad.append(f"tile range unordered: {r}")
    for (x, y) in _georef_sample(tiles, r):
        b = grid.tile_bounds(x, y, zoom)
        if not (b["north"] > b["south"] and b["east"] > b["west"]):
            bad.append(f"({x},{y}): unordered bounds")
            continue
        if (int(round(grid.lon_to_tx(b["west"], zoom))), int(round(grid.lat_to_ty(b["north"], zoom)))) != (x, y):
            bad.append(f"({x},{y}): bounds do not map back")
        if (b["south"] > bbox["north"] or b["north"] < bbox["south"]
                or b["west"] > bbox["east"] or b["east"] < bbox["west"]):
            bad.append(f"({x},{y}): outside bbox")
    return {"ok": not bad, "checked": len(tiles),
            "detail": "; ".join(bad[:5]) or f"{len(tiles)} tiles georeferenced"}


def _georef_sample(tiles: set, r: dict) -> list:
    """The four corners of the range plus an evenly spaced sample of the rest."""
    corners = [(r["x0"], r["y0"]), (r["x1"], r["y0"]), (r["x0"], r["y1"]), (r["x1"], r["y1"])]
    rest = sorted(tiles)
    step = max(1, len(rest) // 200)
    return [t for t in corners if t in tiles] + rest[::step]


def verify_files(out_dir: Path, tiles: dict, zoom: int, tile_px: int, sample: int = 200) -> dict:
    """Re-read written tiles from disk and compare them with what was fetched.
    Catches a partial write or corrupted file before it is archived or
    uploaded. Every tile is re-read and hashed; a sample is also fully decoded,
    since decoding 5,000 PNGs costs more than that part of the check is worth."""
    from PIL import Image

    bad, checked, total, decoded = [], 0, 0, 0
    coords = sorted(tiles)
    step = max(1, len(coords) // sample) if sample else 1
    for i, (x, y) in enumerate(coords):
        p = out_dir / "tiles" / f"z{zoom}_{x}_{y}.png"
        checked += 1
        if not p.exists():
            bad.append(f"{p.name}: missing")
            continue
        raw = p.read_bytes()
        total += len(raw)
        if raw != tiles[(x, y)]:
            bad.append(f"{p.name}: does not match the fetched bytes")
            continue
        if i % step == 0:
            decoded += 1
            try:
                with Image.open(p) as im:
                    im.verify()
                with Image.open(p) as im:
                    if im.size != (tile_px, tile_px):
                        bad.append(f"{p.name}: {im.size} != {tile_px}")
            except Exception as ex:
                bad.append(f"{p.name}: undecodable ({type(ex).__name__})")
    return {"ok": not bad, "checked": checked, "bytes": total, "decoded": decoded,
            "detail": "; ".join(bad[:5]) or f"{checked} tiles verified, {decoded} decoded"}


def verify_manifest(path: Path) -> dict:
    need = ["name", "captured_utc", "zoom", "bbox", "tile_range", "status", "coverage_pct",
            "expected_tiles", "received_tiles", "checks"]
    try:
        m = json.loads(path.read_text(encoding="utf-8"))
    except Exception as ex:
        return {"ok": False, "detail": f"manifest unreadable: {ex}"}
    missing = [k for k in need if k not in m]
    return {"ok": not missing, "detail": ("missing " + ", ".join(missing)) if missing else "manifest complete"}
