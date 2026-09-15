"""Move every local capture into the archive, proving each one before deleting it.

    python storage/migrate.py --captures captures [--dry-run] [--keep-newest]

For each capture on disk, in order:

    1. upload it, the same way the collection workflow does
    2. download it again into a scratch folder
    3. compare every file byte for byte against the original
    4. only then delete the local copy

A capture is never removed on the strength of an upload returning success.
It is removed because a fresh download of it was read back and matched. If any
step fails, that capture is left alone and the run moves to the next one.

Needs HF_TOKEN and HF_REPO. Exit codes: 0 all captures archived and verified,
1 at least one could not be, 3 bad setup.
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint(folder: Path, skip: set[str]) -> dict[str, str]:
    """Every file under `folder` as relative path -> sha256, minus derived ones."""
    out = {}
    for p in sorted(folder.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(folder)
        if skip.intersection(rel.parts):
            continue
        out[rel.as_posix()] = digest(p)
    return out


def upload(capture: Path) -> bool:
    r = subprocess.run([sys.executable, str(HERE / "upload.py"), str(capture)],
                       cwd=ROOT, text=True)
    return r.returncode == 0


def download_into(name: str, dest: Path, repo: str, token: str) -> bool:
    """Fetch one capture from the archive and unpack it exactly as the
    collection workflow would, so the comparison tests the real path."""
    from huggingface_hub import hf_hub_download

    try:
        tar_path = hf_hub_download(repo, f"captures/{name}/capture.tar.gz",
                                   repo_type="dataset", token=token,
                                   force_download=True)
        man_path = hf_hub_download(repo, f"captures/{name}/manifest.json",
                                   repo_type="dataset", token=token,
                                   force_download=True)
    except Exception as e:
        print(f"    could not fetch it back: {type(e).__name__}: {str(e)[:160]}")
        return False
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r:*") as tar:
        tar.extractall(dest, filter="data")
    (dest / "manifest.json").write_bytes(Path(man_path).read_bytes())
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--captures", type=Path, default=ROOT / "captures")
    ap.add_argument("--dry-run", action="store_true",
                    help="upload and verify, but keep the local copies")
    ap.add_argument("--keep-newest", action="store_true",
                    help="leave the most recent capture on disk for local work")
    args = ap.parse_args()

    token = os.environ.get("HF_TOKEN", "").strip()
    repo = os.environ.get("HF_REPO", "").strip()
    if not token or not repo:
        print("[migrate] HF_TOKEN and HF_REPO must be set")
        return 3

    root = args.captures.resolve()
    captures = sorted(d for d in root.iterdir() if d.is_dir() and (d / "manifest.json").exists())
    if not captures:
        print(f"[migrate] no captures under {root}")
        return 0

    # upload.py leaves these out of the archive, so they are not part of the
    # comparison either: they are rebuilt from the tiles on demand
    from upload import DERIVED

    if args.keep_newest:
        import json
        newest = max(captures, key=lambda d: json.loads(
            (d / "manifest.json").read_text(encoding="utf-8")).get("captured_utc", ""))
        captures = [c for c in captures if c != newest]
        print(f"[migrate] keeping {newest.name} on disk")

    print(f"[migrate] {len(captures)} capture(s) to archive, repo {repo}")
    failed = []
    for cap in captures:
        print(f"\n[migrate] {cap.name}")
        before = fingerprint(cap, DERIVED)
        print(f"    {len(before)} files on disk")

        if not upload(cap):
            print("    upload failed; leaving it alone")
            failed.append(cap.name)
            continue

        with tempfile.TemporaryDirectory() as td:
            back = Path(td) / cap.name
            if not download_into(cap.name, back, repo, token):
                failed.append(cap.name)
                continue
            after = fingerprint(back, DERIVED)

            missing = sorted(set(before) - set(after))
            extra = sorted(set(after) - set(before))
            changed = sorted(f for f in set(before) & set(after) if before[f] != after[f])
            if missing or changed:
                print(f"    MISMATCH: {len(missing)} missing, {len(changed)} different, {len(extra)} unexpected")
                for f in (missing + changed)[:5]:
                    print(f"      {f}")
                print("    leaving the local copy alone")
                failed.append(cap.name)
                continue
            print(f"    verified: {len(after)} files match byte for byte"
                  + (f", {len(extra)} extra in the archive" if extra else ""))

        if args.dry_run:
            print("    dry run, keeping the local copy")
            continue
        shutil.rmtree(cap)
        print("    local copy removed")

    print()
    if failed:
        print(f"[migrate] {len(failed)} capture(s) NOT archived: {', '.join(failed)}")
        return 1
    print(f"[migrate] all {len(captures)} capture(s) archived and verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
