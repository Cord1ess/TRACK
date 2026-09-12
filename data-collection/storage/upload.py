"""Upload one capture folder to the private Hugging Face dataset repo, then
verify it landed.

    python storage/upload.py captures/<name> [--dry-run] [--allow-public]

CYCLE_DIR defaults to the path in out/last_cycle.txt. The folder lands at
cycles/YYYY-MM-DD/HHMM/ in one atomic commit that also refreshes latest.json
at the repo root (slot, status, coverage, bytes) for at-a-glance health.

Failsafes:
  * token is validated (whoami) before anything is sent
  * the repo must be private (refuse otherwise, unless --allow-public)
  * one commit per cycle: either every file lands or none does
  * after the commit the remote listing is compared with the local files
    (names and sizes); a mismatch re-uploads once, then fails
  * 3 attempts with backoff around transient network errors

Env: HF_TOKEN (write token), HF_REPO (e.g. "someone/track-dhaka-traffic").
Exit codes: 0 uploaded and verified, 1 upload failed, 3 missing env/folder/repo not private.
"""

import argparse
import io
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def local_files(cycle_dir: Path) -> dict[str, int]:
    return {p.relative_to(cycle_dir).as_posix(): p.stat().st_size
            for p in cycle_dir.rglob("*") if p.is_file()}


def remote_files(api, repo: str, path_in_repo: str) -> dict[str, int]:
    out = {}
    for e in api.list_repo_tree(repo, path_in_repo=path_in_repo, repo_type="dataset",
                                recursive=True):
        size = getattr(e, "size", None)
        if size is None:
            lfs = getattr(e, "lfs", None)
            size = getattr(lfs, "size", None) if lfs else None
        if size is not None:
            out[e.path[len(path_in_repo) + 1:]] = size
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cycle_dir", type=Path, help="captures/<name> folder to upload")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-public", action="store_true")
    args = ap.parse_args()

    cycle_dir = args.cycle_dir.resolve()
    manifest_path = cycle_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"[upload] {cycle_dir} has no manifest.json")
        return 3
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    path_in_repo = f"captures/{cycle_dir.name}"
    files = local_files(cycle_dir)
    total = sum(files.values())
    print(f"[upload] {cycle_dir.parent.name}/{cycle_dir.name}: {len(files)} files, "
          f"{total // 1024} KB -> {path_in_repo} (status {manifest.get('status')})")

    latest = {
        "slot_utc": manifest.get("slot_utc"), "status": manifest.get("status"),
        "coverage_pct": manifest.get("coverage_pct"), "bytes": manifest.get("bytes"),
        "duration_s": manifest.get("duration_s"), "mode": manifest.get("mode"),
        "zoom": manifest.get("zoom"), "warnings": len(manifest.get("warnings", [])),
        "run_id": manifest.get("run_id"), "path": path_in_repo,
        "uploaded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    token = os.environ.get("HF_TOKEN", "").strip()
    repo = os.environ.get("HF_REPO", "").strip()
    if args.dry_run:
        for name in sorted(files):
            print(f"    {name}  {files[name]}")
        print(f"    latest.json  {json.dumps(latest)}")
        print(f"[upload] dry run, repo={repo or '(unset)'} token={'set' if token else 'unset'}")
        return 0
    if not token or not repo:
        print("[upload] HF_TOKEN and HF_REPO must be set")
        return 3

    from huggingface_hub import CommitOperationAdd, HfApi

    api = HfApi(token=token)

    # ---- token + repo checks
    try:
        who = api.whoami()
        print(f"[upload] token ok for {who.get('name', '?')}")
    except Exception as e:
        print(f"[upload] token rejected: {type(e).__name__}: {str(e)[:200]}")
        return 3
    try:
        api.create_repo(repo, repo_type="dataset", private=True, exist_ok=True)
        info = api.repo_info(repo, repo_type="dataset")
        if not info.private and not args.allow_public:
            print(f"[upload] REFUSING: {repo} is public. Make it private or pass --allow-public.")
            return 3
    except Exception as e:
        print(f"[upload] repo check failed: {type(e).__name__}: {str(e)[:200]}")
        return 1

    ops = [CommitOperationAdd(path_in_repo=f"{path_in_repo}/{name}",
                              path_or_fileobj=str(cycle_dir / name)) for name in sorted(files)]
    ops.append(CommitOperationAdd(path_in_repo="latest.json",
                                  path_or_fileobj=io.BytesIO(json.dumps(latest, indent=1).encode())))
    msg = (f"cycle {manifest.get('slot_utc')} {manifest.get('status')} "
           f"cov {manifest.get('coverage_pct', 0)}%")

    for attempt in range(1, 4):
        try:
            api.create_commit(repo_id=repo, repo_type="dataset", operations=ops, commit_message=msg)
            remote = remote_files(api, repo, path_in_repo)
            missing = [n for n in files if n not in remote]
            wrong = [n for n in files if n in remote and remote[n] != files[n]]
            if not missing and not wrong:
                print(f"[upload] verified {len(files)} files at {path_in_repo}")
                return 0
            print(f"[upload] attempt {attempt}: verification failed, "
                  f"missing {missing[:3]} size-mismatch {wrong[:3]}")
        except Exception as e:
            print(f"[upload] attempt {attempt} failed: {type(e).__name__}: {str(e)[:300]}")
        if attempt < 3:
            time.sleep(15 * attempt)
    return 1


if __name__ == "__main__":
    sys.exit(main())
