"""Run the whole TRACK data pipeline for one capture, end to end.

    python pipeline.py --capture ../data-collection/captures/test-capture-2026-09-12

    graph     OpenStreetMap (Overpass) -> output/graph/dhaka.json
    decode    capture tiles + graph    -> output/traffic/observed.csv
    impute    observed + KNN/K-means   -> output/traffic/complete.csv (+ report)

Each stage is skipped when its output is newer than its inputs, so re-running
after a tweak only redoes what changed. `--force` redoes everything, and
`--only graph,decode` runs a subset.

The result is the dataset the router and the visualizer both read: one traffic
weight for every road in Dhaka, each marked observed or predicted.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
STAGES = ("graph", "decode", "impute")


def newer(out: Path, *inputs: Path) -> bool:
    """True when `out` exists and is at least as new as every input that exists."""
    if not out.exists():
        return False
    t = out.stat().st_mtime
    return all(t >= p.stat().st_mtime for p in inputs if p.exists())


def run(label: str, args: list[str]) -> None:
    print(f"\n=== {label}\n    {' '.join(args[2:])}", flush=True)
    t0 = time.time()
    r = subprocess.run([sys.executable, "-m", *args[1:]] if args[0] == "-m" else args,
                       cwd=HERE)
    if r.returncode != 0:
        raise SystemExit(f"{label} failed with exit code {r.returncode}")
    print(f"    {label} done in {time.time() - t0:.0f}s", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capture", type=Path,
                    default=Path("../data-collection/captures/test-capture-2026-09-12"))
    ap.add_argument("--out", type=Path, default=Path("output"))
    ap.add_argument("--max-edge-m", type=float, default=150.0)
    ap.add_argument("--offset-px", type=float, default=4.0)
    ap.add_argument("--trim-m", type=float, default=10.0)
    ap.add_argument("--min-coverage", type=float, default=0.25)
    ap.add_argument("--decay-m", type=float, default=250.0)
    ap.add_argument("--demote-strength", type=float, default=1.0)
    ap.add_argument("--only", help="comma-separated subset of: " + ",".join(STAGES))
    ap.add_argument("--force", action="store_true", help="redo stages even if up to date")
    args = ap.parse_args()

    want = set(args.only.split(",")) if args.only else set(STAGES)
    bad = want - set(STAGES)
    if bad:
        print(f"unknown stage(s): {', '.join(sorted(bad))}; choose from {', '.join(STAGES)}")
        return 2

    graph = args.out / "graph" / "dhaka.json"
    osm_cache = args.out / "graph" / "dhaka-osm.json"
    observed = args.out / "traffic" / "observed.csv"
    complete = args.out / "traffic" / "complete.csv"
    capture = (HERE / args.capture).resolve()
    manifest = capture / "manifest.json"

    if not manifest.exists():
        print(f"no capture at {capture} (expected manifest.json)")
        return 2

    if "graph" in want and (args.force or not newer(graph, osm_cache)):
        osm_cache.parent.mkdir(parents=True, exist_ok=True)
        run("graph", ["-m", "track_algos.graph.build_graph", "--out", str(graph),
                      "--max-edge-m", str(args.max_edge_m), "--osm-cache", str(osm_cache)])
    elif "graph" in want:
        print(f"\n=== graph: up to date ({graph})", flush=True)

    if "decode" in want and (args.force or not newer(observed, graph, manifest)):
        run("decode", ["-m", "track_algos.decode.decoder", "--capture", str(capture),
                       "--graph", str(graph), "--out", str(observed), "--geojson",
                       "--offset-px", str(args.offset_px), "--trim-m", str(args.trim_m),
                       "--min-coverage", str(args.min_coverage)])
    elif "decode" in want:
        print(f"\n=== decode: up to date ({observed})", flush=True)

    if "impute" in want and (args.force or not newer(complete, observed, graph)):
        run("impute", ["-m", "track_algos.traffic.impute", "--graph", str(graph),
                       "--observed", str(observed), "--out", str(complete),
                       "--decay-m", str(args.decay_m),
                       "--demote-strength", str(args.demote_strength)])
    elif "impute" in want:
        print(f"\n=== impute: up to date ({complete})", flush=True)

    print("\npipeline complete. Start the visualizer and turn on 'show our data':")
    print("    cd ../visualizer && python server.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
