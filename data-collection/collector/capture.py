"""One keyless capture of Google's traffic layer over the configured area.

    python collector/capture.py --name <capture-name> [--bbox N,S,E,W] [--zoom 17]
                                [--rate 96] [--workers 32] [--fill-gaps] [--incidents]

Writes captures/<name>/:
    manifest.json     zoom, bbox, tile range, coverage, validation stats, checks,
                      status, seconds per stage
    tiles/            every validated transparent traffic tile, z{z}_{x}_{y}.png
    tiles.csv.gz      one row per expected tile: bytes, sha256, content fractions
    log.txt

Safety net, in order:
    preflight   config valid, disk space, grid math self-check, name unused
    sweep       every tile fetched through fetch.fetch (strict PNG validation, retries)
    retry pass  every tile that failed the sweep is fetched again
    gap-fill    (optional) empty tiles ringed by traffic are re-fetched and merged
    post        coverage vs threshold, traffic present, palette still matches,
                georef round-trip, every tile re-read + compared, manifest complete
Status: ok (complete, clean) | partial (usable, incomplete) | failed (unusable)
Exit codes: 0 ok/partial, 2 failed, 3 preflight error.
"""

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import analyze
import checks
import grid
import tiles as tiles_mod
from fetch import Limiter, fetch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
VERSION = "3.0.0"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Stage:
    """Times each part of a run into `seconds`, so a slow capture says where."""

    def __init__(self):
        self.seconds = {}

    @contextmanager
    def __call__(self, name: str):
        t = time.time()
        try:
            yield
        finally:
            self.seconds[name] = round(self.seconds.get(name, 0) + time.time() - t, 1)


class Log:
    def __init__(self):
        self.lines = []

    def __call__(self, msg: str) -> None:
        line = f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {msg}"
        self.lines.append(line)
        print(line, flush=True)


def load_config(path: Path | None = None) -> dict:
    return json.loads((path or ROOT / "config.json").read_text(encoding="utf-8"))


def parse_bbox(s: str) -> dict:
    n, so, e, w = (float(v) for v in s.split(","))
    return {"north": n, "south": so, "east": e, "west": w}


CRITICAL = ("coverage", "traffic_content", "georef", "files")


def decide_status(ck: dict) -> str:
    """ok = every check passed; partial = only coverage_full failed (a few
    tiles unrecoverable); failed = a critical check failed. Non-critical
    checks (http_clean, palette_match) only add warnings."""
    if any(not ck[k]["ok"] for k in CRITICAL if k in ck):
        return "failed"
    if not ck.get("coverage_full", {"ok": True})["ok"]:
        return "partial"
    return "ok"


def has_traffic(data: bytes, cfg: dict) -> bool:
    return analyze.traffic_fraction(analyze.load_image(data).convert("RGBA"),
                                    cfg["palette"], cfg["palette_tolerance"])["traffic_frac"] > 0


def sweep(targets, cfg, limiter, stats, log, label):
    got, done = {}, 0
    with ThreadPoolExecutor(max_workers=cfg["workers"]) as ex:
        futs = [ex.submit(fetch, cfg["zoom"], x, y, limiter, stats, cfg["tile_px"],
                          cfg["incidents"], cfg["retries"]) for (x, y) in targets]
        for f in futs:
            (x, y), data, _ = f.result()
            done += 1
            if data:
                got[(x, y)] = data
            if done % 500 == 0:
                log(f"[{label}] {done}/{len(targets)} ({len(got)} valid)")
    return got


def run(cfg: dict, name: str, out_root: Path, log: Log) -> tuple[Path | None, dict]:
    started = utc_now()
    t0 = time.time()
    stage = Stage()
    out_dir = out_root / name
    manifest = {"collector_version": VERSION, "name": name, "source": "google-consumer-traffic-tiles-keyless",
                "captured_utc": started, "zoom": cfg["zoom"], "tile_px": cfg["tile_px"],
                "bbox": cfg["bbox"], "incidents": cfg["incidents"], "palette": cfg["palette"],
                "palette_tolerance": cfg["palette_tolerance"], "status": "failed",
                "note": "", "checks": {}, "warnings": []}

    # ---- preflight
    with stage("preflight"):
        pre = {"config": checks.validate_config(cfg), "grid_math": checks.grid_roundtrip(),
               "disk": checks.disk_check(out_root, cfg["min_free_gb"]),
               "name_unused": {"ok": not (out_dir / "manifest.json").exists(),
                               "detail": f"{out_dir} {'already has a manifest' if (out_dir / 'manifest.json').exists() else 'is free'}"}}
    manifest["preflight"] = pre
    for k, v in pre.items():
        log(f"[preflight] {k}: {'ok' if v['ok'] else 'FAIL'} - {v['detail']}")
    if not all(v["ok"] for v in pre.values()):
        manifest["note"] = "preflight failed: " + ", ".join(k for k, v in pre.items() if not v["ok"])
        return None, manifest

    (out_dir / "tiles").mkdir(parents=True, exist_ok=True)
    expected = sorted(grid.expected_tiles(cfg["bbox"], cfg["zoom"]))
    log(f"[capture] {name}: {len(expected)} tiles at z{cfg['zoom']}, ~{cfg['rate']}/s, "
        f"incidents={cfg['incidents']}, fill_gaps={cfg['fill_gaps']}")

    limiter, stats = Limiter(cfg["rate"]), Counter()
    t_fetch = time.time()
    with stage("sweep"):
        tiles = sweep(expected, cfg, limiter, stats, log, "sweep")
    missing = [t for t in expected if t not in tiles]
    if missing:
        log(f"[retry] {len(missing)} tiles failed validation; fetching again")
        with stage("retry"):
            tiles.update(sweep(missing, cfg, limiter, stats, log, "retry"))
    unrecoverable = [t for t in expected if t not in tiles]

    gaps_filled = 0
    if cfg["fill_gaps"]:
        with stage("gapfill scan"):
            haz = {t for t, d in tiles.items() if has_traffic(d, cfg)}
            suspects = [(x, y) for (x, y) in tiles if (x, y) not in haz and
                        sum(((x + dx, y + dy) in haz) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))) >= 3]
        if suspects:
            log(f"[gapfill] re-fetching {len(suspects)} empty tiles ringed by traffic")
            with stage("gapfill fetch"):
                for t, d in sweep(suspects, cfg, limiter, stats, log, "gapfill").items():
                    if has_traffic(d, cfg):
                        tiles[t] = d
                        gaps_filled += 1
    fetch_s = round(time.time() - t_fetch, 1)
    if limiter.slowed:
        log(f"[capture] the server asked for less {stats['slow_downs']} times; rate is now {limiter.rate}/s")

    with stage("write tiles"):
        for (x, y), data in tiles.items():
            (out_dir / "tiles" / f"z{cfg['zoom']}_{x}_{y}.png").write_bytes(data)
    cov = tiles_mod.coverage(set(expected), set(tiles))
    with stage("audit"):
        audit = tiles_mod.audit_tiles(tiles, set(expected), out_dir, cfg)

    # ---- post checks
    tile_range = grid.tile_range(cfg["bbox"], cfg["zoom"])
    ck = manifest["checks"]
    with stage("checks"):
        ck["coverage"] = {"ok": cov["coverage_pct"] >= cfg["min_coverage_pct"],
                          "detail": f"{cov['coverage_pct']}% (min {cfg['min_coverage_pct']}%)"}
        ck["coverage_full"] = {"ok": not unrecoverable, "detail": f"{len(unrecoverable)} tiles unrecoverable"}
        ck["traffic_content"] = {"ok": audit["tiles_nonempty"] > 0,
                                 "detail": f"{audit['tiles_nonempty']} non-empty tiles, mean traffic frac {audit['mean_traffic_frac']}"}
        ck["palette_match"] = checks.palette_match_check(audit)
        ck["georef"] = checks.georef_check(set(tiles), cfg["zoom"], cfg["bbox"], tile_range)
        ck["http_clean"] = {"ok": not any(k.startswith("http_") and k != "http_200" for k in stats),
                            "detail": f"http {dict((k, v) for k, v in stats.items() if k.startswith('http_'))}"}
        ck["files"] = checks.verify_files(out_dir, tiles, cfg["zoom"], cfg["tile_px"])

    manifest.update({
        "tile_range": tile_range,
        "expected_tiles": cov["expected"], "received_tiles": cov["received"], "coverage_pct": cov["coverage_pct"],
        "tiles_missing": cov["missing"], "unrecoverable_tiles": unrecoverable, "gaps_filled": gaps_filled,
        "recovered_after_retry": stats.get("recovered_after_retry", 0),
        "validation_rejects": {k[len("invalid_"):]: v for k, v in stats.items() if k.startswith("invalid_")},
        "http_status": {k: v for k, v in stats.items() if k.startswith("http_")},
        "fetch_seconds": fetch_s, "rate": cfg["rate"], "rate_final": limiter.rate,
        "slow_downs": stats.get("slow_downs", 0), "tiles_nonempty": audit["tiles_nonempty"],
        "mean_traffic_frac": audit["mean_traffic_frac"], "bytes_tiles": sum(len(d) for d in tiles.values()),
        "audit": audit,
    })
    manifest["warnings"] = [f"{k}: {v['detail']}" for k, v in ck.items() if not v["ok"]]
    manifest["status"] = decide_status(ck)

    manifest["finished_utc"] = utc_now()
    manifest["stage_seconds"] = dict(stage.seconds)
    manifest["total_seconds"] = round(time.time() - t0, 1)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    ck["manifest"] = checks.verify_manifest(out_dir / "manifest.json")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    for k, v in ck.items():
        log(f"[check] {k}: {'ok' if v['ok'] else 'FAIL'} - {v['detail']}")
    log("[time] " + ", ".join(f"{k} {v}s" for k, v in stage.seconds.items())
        + f", total {manifest['total_seconds']}s")
    log(f"[capture] {name}: {manifest['status'].upper()} coverage {cov['coverage_pct']}% "
        f"({cov['received']}/{cov['expected']}), recovered {manifest['recovered_after_retry']}, "
        f"gaps filled {gaps_filled}, {manifest['bytes_tiles'] // 1024} KB in {fetch_s}s")
    (out_dir / "log.txt").write_text("\n".join(log.lines), encoding="utf-8")
    return out_dir, manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="capture folder name under captures/")
    ap.add_argument("--config", type=Path)
    ap.add_argument("--bbox", help="north,south,east,west (default: config)")
    ap.add_argument("--zoom", type=int)
    ap.add_argument("--rate", type=float)
    ap.add_argument("--workers", type=int)
    ap.add_argument("--fill-gaps", action="store_true")
    ap.add_argument("--incidents", action="store_true")
    ap.add_argument("--out", type=Path, default=ROOT / "captures")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.bbox:
        cfg["bbox"] = parse_bbox(args.bbox)
    for k in ("zoom", "rate", "workers"):
        if getattr(args, k) is not None:
            cfg[k] = getattr(args, k)
    cfg["fill_gaps"] = cfg.get("fill_gaps", False) or args.fill_gaps
    cfg["incidents"] = cfg.get("incidents", False) or args.incidents

    log = Log()
    out_dir, manifest = run(cfg, args.name, args.out, log)
    if out_dir is None:
        log(f"[capture] {manifest['note']}")
        return 3
    return 0 if manifest["status"] in ("ok", "partial") else 2


if __name__ == "__main__":
    sys.exit(main())
