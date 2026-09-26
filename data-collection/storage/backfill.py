"""Turn archived captures into road weights, one shard of the work at a time.

Run by .github/workflows/backfill.yml. Reads SHARD, SHARDS, START and END from
the environment, takes every SHARDS-th capture in the range, and for each one:

    fetch the capture -> run decode, impute -> upload complete.csv.gz -> delete

The delete matters. A capture is 41 MB unpacked and a runner has about 14 GB,
so keeping them would fill the disk after a few hundred. Only one capture is
on disk at a time.

A capture whose weights are already in the archive is skipped, so the workflow
can be re-run after a failure and will only do what is left.

Writes to weights/<capture>.csv.gz in the same dataset. About 2 MB each against
20 MB for the tiles, which is what makes a week of them downloadable.
"""

import gzip
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO_ROOT = ROOT.parent


def main() -> int:
    token = os.environ.get("HF_TOKEN", "").strip()
    repo = os.environ.get("HF_REPO", "").strip()
    if not token or not repo:
        print("[backfill] HF_TOKEN and HF_REPO must be set")
        return 3

    shard = int(os.environ.get("SHARD", "0"))
    shards = max(1, int(os.environ.get("SHARDS", "1")))
    start, end = os.environ.get("START", ""), os.environ.get("END", "")

    from huggingface_hub import CommitOperationAdd, HfApi, hf_hub_download
    api = HfApi(token=token)

    names = sorted(e.path.split("/")[-1] for e in
                   api.list_repo_tree(repo, path_in_repo="captures", repo_type="dataset")
                   if "." not in e.path.split("/")[-1])
    day = lambda n: n.split("dhaka-")[-1][:10]
    want = [n for n in names if (not start or day(n) >= start) and (not end or day(n) <= end)]

    # already done, so a re-run picks up where the last one stopped
    try:
        have = {e.path.split("/")[-1].replace(".csv.gz", "") for e in
                api.list_repo_tree(repo, path_in_repo="weights", repo_type="dataset")
                if e.path.endswith(".csv.gz")}
    except Exception:
        have = set()

    mine = [n for i, n in enumerate(want) if i % shards == shard and n not in have]
    print(f"[backfill] shard {shard}/{shards}: {len(mine)} captures "
          f"({len(want)} in range, {len(have)} already done)", flush=True)

    work = REPO_ROOT / "_backfill"
    work.mkdir(exist_ok=True)
    done, failed, t0 = 0, [], time.time()

    for i, name in enumerate(mine, 1):
        cap = work / name
        shutil.rmtree(cap, ignore_errors=True)
        try:
            tp = hf_hub_download(repo, f"captures/{name}/capture.tar.gz",
                                 repo_type="dataset", token=token)
            mp = hf_hub_download(repo, f"captures/{name}/manifest.json",
                                 repo_type="dataset", token=token)
            cap.mkdir(parents=True, exist_ok=True)
            with tarfile.open(tp, "r:*") as tar:
                tar.extractall(cap, filter="data")
            (cap / "manifest.json").write_bytes(Path(mp).read_bytes())

            # decode and impute only: the algorithm layers are for the map, and
            # the weights are what an analysis over time needs.
            #
            # One output folder serves capture after capture, so clear both
            # stage outputs first and force every stage: a leftover
            # observed.csv from the previous capture is exactly how two local
            # files came to hold a neighbouring capture's traffic.
            traffic = REPO_ROOT / "algorithms" / "output" / "traffic"
            out = traffic / "complete.csv"
            for stale in (out, traffic / "observed.csv"):
                stale.unlink(missing_ok=True)
            r = subprocess.run(
                [sys.executable, "pipeline.py", "--capture", str(cap),
                 "--only", "decode,impute", "--quick", "--force"],
                cwd=REPO_ROOT / "algorithms", capture_output=True, text=True, timeout=1200)
            if r.returncode != 0 or not out.exists():
                print(f"  {name}: pipeline failed rc={r.returncode} {r.stderr[-200:]}")
                failed.append(name)
                continue
            # refuse to upload another capture's traffic under this name
            want = json.loads((cap / "manifest.json").read_text(encoding="utf-8"))["captured_utc"]
            with out.open(encoding="utf-8") as f:
                f.readline()
                got = f.readline().split(",")[0]
            if got != want:
                print(f"  {name}: output is from {got}, not {want}; not uploaded")
                failed.append(name)
                continue

            buf = io.BytesIO()
            with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as gz:
                gz.write(out.read_bytes())
            api.create_commit(
                repo_id=repo, repo_type="dataset",
                operations=[CommitOperationAdd(path_in_repo=f"weights/{name}.csv.gz",
                                               path_or_fileobj=io.BytesIO(buf.getvalue()))],
                commit_message=f"weights {name}")
            done += 1
            if done % 10 == 0 or i == len(mine):
                rate = (time.time() - t0) / max(done, 1)
                left = (len(mine) - i) * rate / 60
                print(f"  {i}/{len(mine)}  {done} done, {len(failed)} failed, "
                      f"~{left:.0f} min left", flush=True)
        except Exception as e:
            print(f"  {name}: {type(e).__name__}: {str(e)[:160]}")
            failed.append(name)
        finally:
            # one capture on disk at a time, or the runner fills up
            shutil.rmtree(cap, ignore_errors=True)

    print(f"[backfill] shard {shard}: {done} done, {len(failed)} failed")
    if failed:
        print("  failed:", ", ".join(failed[:20]))
    # a shard that did some work is a success; re-running picks up the rest
    return 0 if done or not mine else 1


if __name__ == "__main__":
    sys.exit(main())
