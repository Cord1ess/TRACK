"""Asks once a day whether collection is actually working.

    python scheduler/daily_check.py [--day YYYY-MM-DD]     (default: yesterday)

Looks at everything that reached the archive on one day and answers four
questions. How many captures arrived and were they good. Was there a long gap
where nothing was collected. Is the collector still running now. Is the
archive filling up.

The answer is a table, printed and also attached to the workflow run so it can
be read without opening logs. Exits 1 if anything looks wrong, which turns the
scheduled run red.

Needs HF_TOKEN and HF_REPO.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The workflow triggers every 10 minutes and a whole run takes about 8, so a
# perfect day is roughly 144 captures 10 minutes apart. The thresholds below
# are deliberately loose: they catch "collection has stopped or is badly
# broken", not a few slow runs.
MIN_CAPTURES = 72          # half a perfect day; fewer usable captures is a problem
MAX_BAD = 18               # partial, failed or unreadable captures allowed
MAX_GAP_MIN = 60           # longest allowed gap between captures
STALE_AFTER_MIN = 30       # latest.json older than this means the collector stopped
MAX_DATASET_GB = 90.0      # the free private tier is 100 GB

WHEN = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2})(\d{2})")


def when_of(name: str, manifest: dict | None = None) -> tuple[str | None, str | None]:
    """(day, HH:MM) of a capture, from its manifest or else from its name."""
    if manifest and manifest.get("captured_utc"):
        t = datetime.strptime(manifest["captured_utc"][:16], "%Y-%m-%dT%H:%M")
        return t.strftime("%Y-%m-%d"), t.strftime("%H:%M")
    m = WHEN.search(name)
    return (m.group(1), f"{m.group(2)}:{m.group(3)}") if m else (None, None)


def longest_gap_min(times: list[str]) -> int:
    """Longest gap in minutes between consecutive HH:MM times."""
    mins = sorted(int(t[:2]) * 60 + int(t[3:]) for t in times)
    return max((b - a for a, b in zip(mins, mins[1:])), default=0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", help="UTC day, default yesterday")
    args = ap.parse_args()
    token = os.environ.get("HF_TOKEN", "").strip()
    repo = os.environ.get("HF_REPO", "").strip()
    if not token or not repo:
        print("[check] HF_TOKEN and HF_REPO must be set")
        return 3
    now = datetime.now(timezone.utc)
    day = args.day or (now - timedelta(days=1)).strftime("%Y-%m-%d")

    from huggingface_hub import HfApi, hf_hub_download

    api = HfApi(token=token)
    try:
        entries = list(api.list_repo_tree(repo, path_in_repo="captures", repo_type="dataset"))
    except Exception as e:
        print(f"[check] could not list captures/: {type(e).__name__}: {str(e)[:200]}")
        entries = []
    names = sorted(e.path.split("/")[-1] for e in entries if "." not in e.path.split("/")[-1])

    rows, counts, total_bytes, cov_sum = [], {"ok": 0, "partial": 0, "failed": 0, "unreadable": 0}, 0, 0.0
    for name in names:
        d, _ = when_of(name)
        if d and d != day:                       # dated names decide without a download
            continue
        try:
            p = hf_hub_download(repo, f"captures/{name}/manifest.json", repo_type="dataset", token=token)
            m = json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception:
            rows.append((name, "??:??", "unreadable", 0, 0, 0, ""))
            counts["unreadable"] += 1
            continue
        d, hhmm = when_of(name, m)
        if d != day:
            continue
        st = m.get("status", "failed")
        counts[st if st in counts else "failed"] += 1
        total_bytes += m.get("bytes_tiles", 0)
        cov_sum += m.get("coverage_pct", 0)
        warns = m.get("warnings", [])
        rows.append((name, hhmm, st, m.get("coverage_pct", 0), m.get("bytes_tiles", 0) // 1024,
                     round(m.get("fetch_seconds", 0)), "; ".join(str(w).split(":")[0] for w in warns)))

    usable = counts["ok"] + counts["partial"]
    bad = counts["partial"] + counts["failed"] + counts["unreadable"]
    gap = longest_gap_min([r[1] for r in rows if r[1] != "??:??"])
    mean_cov = round(cov_sum / max(1, len(rows) - counts["unreadable"]), 2)

    latest_age_min, latest_txt = None, "no latest.json"
    try:
        p = hf_hub_download(repo, "latest.json", repo_type="dataset", token=token)
        latest = json.loads(Path(p).read_text(encoding="utf-8"))
        up = datetime.strptime(latest["uploaded_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        latest_age_min = int((now - up).total_seconds() // 60)
        latest_txt = f"{latest.get('captured_utc')} {latest.get('status')}, uploaded {latest_age_min} min ago"
    except Exception as e:
        latest_txt = f"latest.json unreadable ({type(e).__name__})"

    size_gb, size_txt = None, "size unknown"
    try:
        info = api.repo_info(repo, repo_type="dataset", files_metadata=True)
        size_gb = sum((s.size or 0) for s in info.siblings) / 1e9
        days = len({when_of(s.rfilename.split("/")[1])[0] for s in info.siblings
                    if s.rfilename.startswith("captures/")} - {None}) or 1
        size_txt = f"{size_gb:.2f} GB over {days} day(s); 7-day projection {size_gb / days * 7:.1f} GB"
    except Exception as e:
        size_txt = f"size unavailable ({type(e).__name__})"

    problems = []
    if usable < MIN_CAPTURES:
        problems.append(f"only {usable} usable captures (need {MIN_CAPTURES})")
    if bad > MAX_BAD:
        problems.append(f"{bad} partial, failed or unreadable captures (max {MAX_BAD})")
    if gap > MAX_GAP_MIN:
        problems.append(f"{gap} minutes between captures at worst (max {MAX_GAP_MIN})")
    if latest_age_min is not None and latest_age_min > STALE_AFTER_MIN:
        problems.append(f"collector stale: last upload {latest_age_min} min ago")
    if size_gb is not None and size_gb > MAX_DATASET_GB:
        problems.append(f"dataset {size_gb:.1f} GB exceeds {MAX_DATASET_GB} GB")

    lines = [
        f"## TRACK health for {day} (UTC)", "",
        f"**{'HEALTHY' if not problems else 'DEGRADED: ' + '; '.join(problems)}**", "",
        "| Metric | Value |", "|---|---|",
        f"| Captures | {len(rows)} |",
        f"| ok / partial / failed / unreadable | {counts['ok']} / {counts['partial']} / {counts['failed']} / {counts['unreadable']} |",
        f"| Mean coverage | {mean_cov} % |",
        f"| Longest gap | {gap} min |",
        f"| Tiles this day | {total_bytes / 1e6:.1f} MB |",
        f"| Dataset | {size_txt} |",
        f"| Collector | {latest_txt} |",
        "",
    ]
    flagged = [r for r in rows if r[2] != "ok" or r[6]]
    if flagged:
        lines += ["| Capture | Time | Status | Coverage % | KB | Seconds | Warnings |", "|---|---|---|---|---|---|---|"]
        lines += [f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} | {r[6]} |" for r in flagged]
    text = "\n".join(lines)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
