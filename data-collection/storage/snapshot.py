"""Copy a stretch of the archive to local disk for analysis.

    python storage/snapshot.py --to ../week1 --from 2026-09-16 --until 2026-09-24

Read-only against the archive: it never deletes or writes there, so it is safe
to run while collection is going. Resumable, because a capture already on disk
is skipped, so an interrupted run continues where it stopped.

Writes <to>/captures/<name>/ exactly as the collector wrote them, plus an
index.csv of every capture with its time, status, coverage and painted-tile
count for quick filtering before you unpack anything.
"""

import argparse
import csv
import json
import os
import shutil
import sys
import tarfile
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--to", type=Path, required=True, help="folder to write into")
    ap.add_argument("--from", dest="start", default="", help="YYYY-MM-DD, inclusive")
    ap.add_argument("--until", dest="end", default="", help="YYYY-MM-DD, inclusive")
    ap.add_argument("--repo", default=os.environ.get("HF_REPO", ""))
    ap.add_argument("--index-only", action="store_true",
                    help="write index.csv and stop, without downloading tiles")
    args = ap.parse_args()

    token = os.environ.get("HF_TOKEN", "").strip()
    if not args.repo or not token:
        print("[snapshot] set HF_TOKEN and HF_REPO")
        return 3

    from huggingface_hub import HfApi, hf_hub_download
    api = HfApi(token=token)

    names = sorted(e.path.split("/")[-1] for e in
                   api.list_repo_tree(args.repo, path_in_repo="captures", repo_type="dataset")
                   if "." not in e.path.split("/")[-1])
    # the name carries the date, so the window is filtered without any downloads
    day = lambda n: n.split("dhaka-")[-1][:10]
    want = [n for n in names
            if (not args.start or day(n) >= args.start)
            and (not args.end or day(n) <= args.end)]
    print(f"[snapshot] {len(want)} captures in range, of {len(names)} in the archive")

    out = args.to.resolve()
    (out / "captures").mkdir(parents=True, exist_ok=True)

    # Clear anything a previous interrupted run left behind: staging folders,
    # and capture folders with no manifest, which means the extract never
    # finished. Both are re-fetched below rather than left to accumulate.
    stale = 0
    for d in (out / "captures").iterdir():
        if not d.is_dir():
            continue
        if d.name.endswith(".partial") or not (d / "manifest.json").exists():
            shutil.rmtree(d, ignore_errors=True)
            stale += 1
    if stale:
        print(f"[snapshot] cleared {stale} unfinished folder(s) from an earlier run")
    rows, done, skipped = [], 0, 0

    for i, n in enumerate(want, 1):
        dst = out / "captures" / n
        try:
            mp = hf_hub_download(args.repo, f"captures/{n}/manifest.json",
                                 repo_type="dataset", token=token)
            m = json.loads(Path(mp).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  {n}: manifest unreadable ({type(e).__name__}), skipped")
            continue
        rows.append({
            "name": n, "captured_utc": m.get("captured_utc", ""),
            "series": m.get("series", ""), "status": m.get("status", ""),
            "coverage_pct": m.get("coverage_pct", ""),
            "tiles_nonempty": m.get("tiles_nonempty", ""),
            "expected_tiles": m.get("expected_tiles", ""),
            "mean_traffic_frac": m.get("mean_traffic_frac", ""),
            "fetch_seconds": m.get("fetch_seconds", ""),
        })
        if args.index_only:
            continue
        if (dst / "manifest.json").exists():          # resumable
            skipped += 1
            continue
        try:
            tp = hf_hub_download(args.repo, f"captures/{n}/capture.tar.gz",
                                 repo_type="dataset", token=token)
        except Exception as e:
            print(f"  {n}: tar unreadable ({type(e).__name__}), skipped")
            continue
        # Extract into a fresh temporary folder, then move it into place.
        #
        # Extracting straight over a half-finished capture raises WinError 4390
        # on Windows: tarfile's "data" filter resolves each destination path,
        # and hits an existing directory where it expects to create one. So a
        # resumed download crashed instead of repairing what it found.
        #
        # This way an interrupted run leaves a stray .partial folder and
        # nothing else, the real capture folder is only ever created complete,
        # and re-running repairs rather than crashes.
        staging = dst.with_name(dst.name + ".partial")
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(tp, "r:*") as tar:
                tar.extractall(staging, filter="data")
            (staging / "manifest.json").write_bytes(Path(mp).read_bytes())
            shutil.rmtree(dst, ignore_errors=True)      # any earlier partial
            staging.replace(dst)
        except Exception as e:
            shutil.rmtree(staging, ignore_errors=True)
            print(f"  {n}: extract failed ({type(e).__name__}), left for the next run")
            continue
        done += 1
        if done % 25 == 0 or i == len(want):
            print(f"  {i}/{len(want)}  ({done} fetched, {skipped} already here)", flush=True)

    if rows:
        with open(out / "index.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"[snapshot] index.csv: {len(rows)} captures")
    print(f"[snapshot] {done} fetched, {skipped} already present -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
