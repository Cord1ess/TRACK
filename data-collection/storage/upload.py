"""Upload one capture to the private Hugging Face dataset, then verify it landed.

    python storage/upload.py captures/<name> [--dry-run] [--allow-public]

A capture becomes two files in one commit: captures/<name>/manifest.json,
readable without downloading anything else, and captures/<name>/capture.tar.gz
with the native data: the tiles, tiles.csv.gz and the log. One archive per
capture keeps the dataset at two files per run instead of five thousand, and
about 2.5 MB. Derived files are left out: the block mosaics and the preview
(rebuilt from the tiles) and the visualizer's tile caches. latest.json at the
root is refreshed in the same commit, and loose files from an earlier upload
of the same capture are removed.

Failsafes: the token is checked first; the dataset must be private (or pass
--allow-public); one commit per capture; the remote listing is compared with
what was sent, and a mismatch re-uploads once; three attempts with backoff.

Env: HF_TOKEN (write token), HF_REPO (e.g. "someone/track-dhaka-traffic").
Exit codes: 0 uploaded and verified, 1 upload failed, 3 missing env or folder,
or the repo is public.
"""

import argparse
import io
import json
import os
import sys
import tarfile
import time
from pathlib import Path

DERIVED = {"_pyramid", "_pyramid_clean", "_clean",      # the visualizer's tile caches
           "blocks", "preview_on_white.png"}             # mosaics and preview, made from the tiles
KEEP = ("manifest.json", "capture.tar.gz")


def capture_files(cycle_dir: Path) -> list[str]:
    """Relative paths of everything that goes into the tar."""
    return sorted(p.relative_to(cycle_dir).as_posix() for p in cycle_dir.rglob("*")
                  if p.is_file() and p.name != "manifest.json"
                  and not DERIVED.intersection(p.relative_to(cycle_dir).parts))


def make_tar(cycle_dir: Path, files: list[str]) -> bytes:
    buf = io.BytesIO()
    # gzip mostly removes the tar headers and padding: thousands of tiny PNGs
    # would otherwise double in size
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=1) as tar:
        for rel in files:
            tar.add(cycle_dir / rel, arcname=rel)
    return buf.getvalue()


def remote_files(api, repo: str, path_in_repo: str) -> dict[str, int]:
    """name -> size of the files under one capture path on the remote."""
    out = {}
    try:
        for e in api.list_repo_tree(repo, path_in_repo=path_in_repo, repo_type="dataset", recursive=True):
            size = getattr(e, "size", None)
            if size is not None:
                out[e.path[len(path_in_repo) + 1:]] = size
    except Exception:
        pass
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
    files = capture_files(cycle_dir)
    tar_bytes = make_tar(cycle_dir, files)
    print(f"[upload] {cycle_dir.name}: {len(files)} files in one archive, {len(tar_bytes) // 1024} KB "
          f"-> {path_in_repo} (status {manifest.get('status')})")

    captured = manifest.get("captured_utc") or manifest.get("slot_utc")
    latest = {
        "captured_utc": captured, "slot_utc": captured, "status": manifest.get("status"),
        "coverage_pct": manifest.get("coverage_pct"), "tiles_nonempty": manifest.get("tiles_nonempty"),
        "expected_tiles": manifest.get("expected_tiles"), "bytes_tiles": manifest.get("bytes_tiles"),
        "fetch_seconds": manifest.get("fetch_seconds"), "zoom": manifest.get("zoom"),
        "warnings": len(manifest.get("warnings", [])), "path": path_in_repo,
        "uploaded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    token = os.environ.get("HF_TOKEN", "").strip()
    repo = os.environ.get("HF_REPO", "").strip()
    if args.dry_run:
        print(f"    manifest.json  {manifest_path.stat().st_size}")
        print(f"    capture.tar.gz {len(tar_bytes)}  ({files[0]} ... {files[-1]})")
        print(f"    latest.json    {json.dumps(latest)}")
        print(f"[upload] dry run, repo={repo or '(unset)'} token={'set' if token else 'unset'}")
        return 0
    if not token or not repo:
        print("[upload] HF_TOKEN and HF_REPO must be set")
        return 3

    from huggingface_hub import CommitOperationAdd, CommitOperationDelete, HfApi

    api = HfApi(token=token)
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

    stale = [n for n in remote_files(api, repo, path_in_repo) if n not in KEEP]
    ops = [CommitOperationAdd(path_in_repo=f"{path_in_repo}/manifest.json", path_or_fileobj=str(manifest_path)),
           CommitOperationAdd(path_in_repo=f"{path_in_repo}/capture.tar.gz", path_or_fileobj=io.BytesIO(tar_bytes)),
           CommitOperationAdd(path_in_repo="latest.json",
                              path_or_fileobj=io.BytesIO(json.dumps(latest, indent=1).encode()))]
    ops += [CommitOperationDelete(path_in_repo=f"{path_in_repo}/{n}") for n in stale]
    if stale:
        print(f"[upload] removing {len(stale)} loose files from an earlier upload of this capture")
    msg = f"capture {captured} {manifest.get('status')} cov {manifest.get('coverage_pct', 0)}%"

    want = {"manifest.json": manifest_path.stat().st_size, "capture.tar.gz": len(tar_bytes)}
    for attempt in range(1, 4):
        try:
            api.create_commit(repo_id=repo, repo_type="dataset", operations=ops, commit_message=msg)
            remote = remote_files(api, repo, path_in_repo)
            wrong = {n: (want[n], remote.get(n)) for n in want if remote.get(n) != want[n]}
            if not wrong:
                print(f"[upload] verified {path_in_repo}: manifest.json + capture.tar.gz ({len(tar_bytes) // 1024} KB)")
                return 0
            print(f"[upload] attempt {attempt}: verification failed {wrong}")
            ops = [op for op in ops if isinstance(op, CommitOperationAdd)]   # deletions are done
        except Exception as e:
            print(f"[upload] attempt {attempt} failed: {type(e).__name__}: {str(e)[:300]}")
        if attempt < 3:
            time.sleep(15 * attempt)
    return 1


if __name__ == "__main__":
    sys.exit(main())
