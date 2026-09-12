"""Download the collected dataset (or one day of it) from Hugging Face.

    python tools/download.py --to D:/track-data [--day 2026-09-10] [--repo user/name]

Env: HF_TOKEN (read access to the private repo), HF_REPO (default for --repo).
Re-running only fetches files that are new or changed.
"""

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", type=Path, required=True, help="destination folder")
    ap.add_argument("--day", help="only cycles/YYYY-MM-DD")
    ap.add_argument("--repo", default=os.environ.get("HF_REPO", ""))
    args = ap.parse_args()

    token = os.environ.get("HF_TOKEN", "").strip()
    if not args.repo or not token:
        print("set HF_TOKEN and HF_REPO (or pass --repo)")
        return 3

    from huggingface_hub import snapshot_download

    patterns = [f"cycles/{args.day}/*"] if args.day else None
    path = snapshot_download(
        repo_id=args.repo, repo_type="dataset", token=token,
        local_dir=str(args.to), allow_patterns=patterns,
    )
    n = sum(1 for p in Path(path).rglob("manifest.json"))
    size = sum(p.stat().st_size for p in Path(path).rglob("*") if p.is_file())
    print(f"downloaded to {path}: {n} cycles, {size / 1e9:.2f} GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
