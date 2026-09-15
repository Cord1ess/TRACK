"""Brings captures back from the archive.

    python storage/download.py --to captures [--latest 3] [--name <capture>]

Fetches the newest few captures, or one by name, and unpacks each into its own
folder exactly as the collector wrote it. A capture already on disk is left
alone, so this is safe to run repeatedly.

This is how a fresh deployment gets data without waiting for a capture: the
site can be built from the archive immediately.

Needs HF_TOKEN and HF_REPO. Exit codes: 0 done, 3 bad setup. A missing or
empty archive is not an error, it just fetches nothing and says so.
"""

import argparse
import json
import os
import sys
import tarfile
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", type=Path, required=True, help="captures folder")
    ap.add_argument("--latest", type=int, default=3, help="how many of the newest captures")
    ap.add_argument("--name", help="one capture by name instead")
    ap.add_argument("--repo", default=os.environ.get("HF_REPO", ""))
    args = ap.parse_args()

    token = os.environ.get("HF_TOKEN", "").strip()
    if not args.repo or not token:
        print("[download] set HF_TOKEN and HF_REPO (or pass --repo)")
        return 3

    from huggingface_hub import HfApi, hf_hub_download

    api = HfApi(token=token)
    try:
        entries = list(api.list_repo_tree(args.repo, path_in_repo="captures", repo_type="dataset"))
    except Exception as e:
        print(f"[download] could not list the archive: {type(e).__name__}: {str(e)[:200]}")
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::warning title=archive::could not list {args.repo}: {type(e).__name__}. "
                  "Check the HF_TOKEN and HF_REPO secrets.")
        return 0
    names = [e.path.split("/")[-1] for e in entries if "." not in e.path.split("/")[-1]]

    def manifest_of(name: str) -> dict | None:
        try:
            p = hf_hub_download(args.repo, f"captures/{name}/manifest.json", repo_type="dataset", token=token)
            return json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception:
            return None

    if args.name:
        want = [args.name] if args.name in names else []
    else:
        dated = []
        for n in names:                                   # newest by capture time
            m = manifest_of(n)
            if m and m.get("status") in ("ok", "partial"):
                dated.append((m.get("captured_utc", ""), n))
        want = [n for _, n in sorted(dated, reverse=True)[: args.latest]]

    want = [n for n in want if not (args.to / n / "manifest.json").exists()]
    if not want:
        print(f"[download] nothing to fetch ({len(names)} captures in the archive)")
        if os.environ.get("GITHUB_ACTIONS") and not names:
            print(f"::warning title=archive::{args.repo} holds no captures yet")
        return 0

    for n in want:
        try:
            tar_path = hf_hub_download(args.repo, f"captures/{n}/capture.tar.gz", repo_type="dataset", token=token)
            man_path = hf_hub_download(args.repo, f"captures/{n}/manifest.json", repo_type="dataset", token=token)
        except Exception as e:
            print(f"[download] {n}: no capture.tar.gz in the archive ({type(e).__name__}); skipped")
            if os.environ.get("GITHUB_ACTIONS"):
                print(f"::warning title=archive::{n} could not be fetched: {type(e).__name__}")
            continue
        dst = args.to / n
        dst.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tar_path, "r:*") as tar:
            tar.extractall(dst, filter="data")
        (dst / "manifest.json").write_bytes(Path(man_path).read_bytes())
        size = sum(p.stat().st_size for p in dst.rglob("*") if p.is_file())
        print(f"[download] {n}: {size // 1024} KB, {sum(1 for p in dst.rglob('*') if p.is_file())} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
