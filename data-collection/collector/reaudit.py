"""Recompute the palette-dependent statistics of an existing capture.

    python collector/reaudit.py --name <capture-name> [--config config.json]

Nothing is fetched or re-encoded: the stored tiles do not depend on the
palette. Rewritten: tiles.csv.gz and the manifest's content fields, palette,
checks and status. Run it after changing the palette in config.json (e.g.
Google restyled the traffic layer) so the stored statistics describe the
stored pixels again.
Exit codes: 0 ok/partial, 2 failed, 3 bad config or no such capture.
"""

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import checks  # noqa: E402
import grid  # noqa: E402
import tiles as tiles_mod  # noqa: E402
from capture import decide_status, load_config, utc_now  # noqa: E402

ROOT = HERE.parent
_TILE_RE = re.compile(r"^z(\d+)_(\d+)_(\d+)\.png$")


def load_tiles(out_dir: Path, zoom: int) -> dict:
    """All z{zoom}_{x}_{y}.png under tiles/ as {(x, y): bytes}."""
    tiles = {}
    for p in (out_dir / "tiles").glob("*.png"):
        m = _TILE_RE.match(p.name)
        if m and int(m.group(1)) == zoom:
            tiles[(int(m.group(2)), int(m.group(3)))] = p.read_bytes()
    return tiles


def reaudit(out_dir: Path, cfg: dict) -> dict:
    mp = out_dir / "manifest.json"
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    zoom, tp = int(manifest["zoom"]), int(manifest.get("tile_px", cfg["tile_px"]))
    expected = set(grid.expected_tiles(manifest["bbox"], zoom))
    tiles = load_tiles(out_dir, zoom)
    print(f"  {len(tiles)} tiles on disk, {len(expected)} expected", flush=True)

    audit = tiles_mod.audit_tiles(tiles, expected, out_dir, cfg)            # rewrites tiles.csv.gz

    ck = manifest.get("checks")
    if not isinstance(ck, dict):                                            # older manifests stored a list
        ck = manifest["checks"] = {}
    ck["traffic_content"] = {"ok": audit["tiles_nonempty"] > 0,
                             "detail": f"{audit['tiles_nonempty']} non-empty tiles, mean traffic frac {audit['mean_traffic_frac']}"}
    ck["palette_match"] = checks.palette_match_check(audit)
    ck["files"] = checks.verify_files(out_dir, tiles, zoom, tp)
    manifest.pop("blocks", None)                                            # captures before 2026-09-15 had mosaics
    for stale in ("block_tiles_consistent",):                               # and checks that described them
        ck.pop(stale, None)
    ck["georef"] = checks.georef_check(set(tiles), zoom, manifest["bbox"], manifest["tile_range"])
    manifest.update({"palette": cfg["palette"], "palette_tolerance": cfg["palette_tolerance"], "tile_px": tp,
                     "tiles_nonempty": audit["tiles_nonempty"], "mean_traffic_frac": audit["mean_traffic_frac"],
                     "audit": audit, "reaudited_utc": utc_now()})
    manifest["warnings"] = [f"{k}: {v['detail']}" for k, v in ck.items() if not v["ok"]]
    manifest["status"] = decide_status(ck)
    mp.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    ck["manifest"] = checks.verify_manifest(mp)
    mp.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    for k, v in ck.items():
        print(f"  [check] {k}: {'ok' if v['ok'] else 'FAIL'} - {v['detail']}", flush=True)
    print(f"  {out_dir.name}: {manifest['status'].upper()}", flush=True)
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="capture folder name under captures/")
    ap.add_argument("--config", type=Path)
    ap.add_argument("--out", type=Path, default=ROOT / "captures")
    args = ap.parse_args()
    cfg = load_config(args.config)
    v = checks.validate_config(cfg)
    if not v["ok"]:
        print(f"config invalid: {v['detail']}")
        return 3
    out_dir = args.out / args.name
    if not (out_dir / "manifest.json").exists():
        print(f"no capture at {out_dir}")
        return 3
    m = reaudit(out_dir, cfg)
    return 0 if m["status"] in ("ok", "partial") else 2


if __name__ == "__main__":
    sys.exit(main())
