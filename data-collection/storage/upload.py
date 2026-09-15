"""Sends one capture to the private archive and checks it arrived.

    python storage/upload.py captures/<name> [--dry-run] [--allow-public]

A capture is stored as two files. The manifest goes up on its own, so the
details of a capture can be read without downloading it. Everything else, the
five thousand tiles and the audit file and the log, goes into a single
compressed archive of about 3 MB. Sending one archive instead of five thousand
separate files is the difference between seconds and many minutes.

Anything that can be rebuilt from the tiles is left out.

Safety: the access token is checked before anything is sent, and the upload is
refused if the dataset is not private. After sending, the file sizes on the
server are compared with what was sent, and it retries up to three times if
they do not match.

Needs HF_TOKEN and HF_REPO in the environment.
Exit codes: 0 sent and verified, 1 failed, 3 bad setup or public dataset.
"""

import argparse
import io
import json
import os
import sys
import tarfile
import time
from pathlib import Path

# Anything that can be rebuilt from the tiles is left out of the archive.
# blocks/ and preview_on_white.png were dropped from the collector in
# September 2026; they stay listed so re-uploading an older capture folder
# still skips them.
DERIVED = {"_pyramid", "_pyramid_clean", "_clean",      # the visualizer's tile caches
           "blocks", "preview_on_white.png"}            # older captures only
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
        "series": manifest.get("series", "test"),
        "coverage_pct": manifest.get("coverage_pct"), "tiles_nonempty": manifest.get("tiles_nonempty"),
        "expected_tiles": manifest.get("expected_tiles"), "bytes_tiles": manifest.get("bytes_tiles"),
        "fetch_seconds": manifest.get("fetch_seconds"),
        "total_seconds": manifest.get("total_seconds"), "zoom": manifest.get("zoom"),
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
