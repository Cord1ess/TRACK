"""Run the pipeline over many captures and collect the road weights.

    python batch.py --captures ../week1/captures --day 2026-09-17 --workers 6
    python batch.py --captures ../week1/captures --all --workers 6

For each capture it runs decode and impute, then copies the resulting
complete.csv out as weights/<capture>.csv.gz, one file per moment in time.
That is the dataset worth having: 110,382 roads by however many captures,
about 0.75 MB each against 41 MB of tiles.

Why each worker gets its own output folder: every stage writes to fixed paths
under output/ by default, so running several at once in one folder means they
overwrite each other's observed.csv and complete.csv silently. Each worker is
given --out of its own, and the graph is shared read-only.

Already-done captures are skipped, so this can be stopped with Ctrl-C and
restarted without losing work.
"""

import argparse
import gzip
import json
import shutil
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent


def one(args) -> tuple[str, bool, str]:
    """Process a single capture in its own output folder."""
    capture, out_root, worker = args
    work = HERE / f"_batch/w{worker}"
    work.mkdir(parents=True, exist_ok=True)

    # The graph is 37 MB and identical for every capture. The pipeline looks
    # for it under its own --out, so each worker needs one there: link to the
    # built copy, or to the committed .gz for the pipeline to unpack itself.
    # Hardlink so eighteen workers do not put 18 copies on disk.
    graph_dir = work / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    for fname in ("dhaka.json", "dhaka.json.gz"):
        src, dst = HERE / "output" / "graph" / fname, graph_dir / fname
        if src.exists() and not dst.exists():
            try:
                dst.hardlink_to(src)                  # no second copy on disk
            except Exception:
                shutil.copyfile(src, dst)
    if not (graph_dir / "dhaka.json").exists() and not (graph_dir / "dhaka.json.gz").exists():
        return capture.name, False, "no graph: run the pipeline once first"

    # a worker reuses its folder for capture after capture, and the pipeline
    # skips a stage whose output is newer than its inputs. Capture files come
    # out of a tar with their original mtimes, so without --force the second
    # capture in a worker would be skipped and the FIRST one's weights written
    # out under the second one's name. Silent wrong data; --force prevents it.
    produced = work / "traffic" / "complete.csv"
    # Clear BOTH stage outputs, not just the last one. impute takes its
    # timestamp from observed.csv, so a leftover observed.csv from the
    # previous capture can be imputed and written out under this capture's
    # name: a real file, right shape, wrong moment in time.
    # A locked file here must not kill the worker: on Windows the previous
    # pipeline's handle can still be closing, and an exception raised in a
    # worker propagates out of as_completed and ends the whole run. Retry
    # briefly, then give up and let the timestamp check below catch any
    # leftover data rather than losing the remaining captures.
    for stale in (produced, work / "traffic" / "observed.csv"):
        for attempt in range(10):
            try:
                stale.unlink(missing_ok=True)
                break
            except PermissionError:
                time.sleep(0.3)
    try:
        r = subprocess.run(
            [sys.executable, "pipeline.py", "--capture", str(capture),
             "--out", str(work), "--only", "decode,impute", "--quick", "--force"],
            cwd=HERE, capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        return capture.name, False, "timed out"
    # The pipeline can report success a moment before the file is visible to
    # this process on Windows, so a bare exists() here called a good capture
    # failed. Wait briefly for it rather than dropping the capture.
    for _ in range(20):
        if produced.exists() and produced.stat().st_size > 0:
            break
        time.sleep(0.5)
    if r.returncode != 0 or not produced.exists():
        tail = (r.stderr or r.stdout or "")[-160:].replace("\n", " ")
        return capture.name, False, f"rc={r.returncode} {tail}"

    # Refuse to write a file whose contents belong to a different capture.
    # Every row carries the capture's own captured_utc, so comparing the first
    # row against the manifest catches any mix-up rather than trusting that
    # the stages ran on what they were given.
    try:
        want = json.loads((capture / "manifest.json").read_text(encoding="utf-8"))["captured_utc"]
        with open(produced, "r", encoding="utf-8") as f:
            f.readline()                                   # header
            got = f.readline().split(",")[0]
    except Exception as e:
        return capture.name, False, f"could not verify: {type(e).__name__}"
    if got != want:
        return capture.name, False, f"wrong capture in output: {got} != {want}"

    out = out_root / f"{capture.name}.csv.gz"
    with open(produced, "rb") as f, gzip.open(out, "wb", compresslevel=6) as g:
        shutil.copyfileobj(f, g)
    return capture.name, True, f"{out.stat().st_size / 1e6:.2f} MB"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--captures", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=HERE.parent / "week1" / "weights")
    ap.add_argument("--day", help="YYYY-MM-DD, one day only")
    ap.add_argument("--all", action="store_true", help="every capture present")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, help="stop after this many, for a quick look")
    args = ap.parse_args()

    caps = sorted(d for d in args.captures.iterdir()
                  if d.is_dir() and (d / "manifest.json").exists())
    if args.day:
        caps = [c for c in caps if args.day in c.name]
    elif not args.all:
        print("give --day YYYY-MM-DD or --all")
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    todo = [c for c in caps if not (args.out / f"{c.name}.csv.gz").exists()]
    skipped = len(caps) - len(todo)
    if args.limit:
        todo = todo[:args.limit]

    print(f"[batch] {len(todo)} to process, {skipped} already done, {args.workers} workers")
    if not todo:
        return 0

    t0 = time.time()
    done, failed = 0, []
    jobs = [(c, args.out, i % args.workers) for i, c in enumerate(todo)]
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(one, j): j[0] for j in jobs}
        for f in as_completed(futs):
            # One capture must never end the run: an exception raised inside a
            # worker surfaces here, and letting it propagate abandoned 47 of
            # 181 captures once already.
            try:
                name, ok, detail = f.result()
            except Exception as e:
                name, ok, detail = futs[f].name, False, f"{type(e).__name__}: {str(e)[:120]}"
            done += 1
            if not ok:
                failed.append((name, detail))
                print(f"  FAILED {name}: {detail}", flush=True)
            if done % 10 == 0 or done == len(todo):
                rate = (time.time() - t0) / done
                left = (len(todo) - done) * rate / 60
                print(f"  {done}/{len(todo)}  {rate:.0f}s each, ~{left:.0f} min left", flush=True)

    shutil.rmtree(HERE / "_batch", ignore_errors=True)
    print(f"\n[batch] {done - len(failed)} written to {args.out}, {len(failed)} failed")
    for n, d in failed[:10]:
        print(f"  {n}: {d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
