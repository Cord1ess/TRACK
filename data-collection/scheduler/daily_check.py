"""Daily health check against the Hugging Face repo. Fails (which makes the
workflow email the owner) when the previous UTC day is below threshold.

    python collector/daily_check.py [--day YYYY-MM-DD]

Checks:
  usable cycles       >= daily_min_cycles
  partial + failed    <= daily_max_partial
  longest run of consecutive missing slots <= daily_max_gap_slots
  mean traffic pixel fraction >= daily_min_traffic_frac (layer still rendering)
  collector alive     latest.json newer than 3 slots
  dataset size        < dataset_max_gb, with a 7-day projection

Env: HF_TOKEN, HF_REPO. Writes a summary to $GITHUB_STEP_SUMMARY when present.
Exit codes: 0 healthy, 1 problems found, 3 missing env.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


def longest_gap(missing: list[str], cadence: int) -> int:
    """Longest run of consecutive missing HHMM slots."""
    slots = [f"{h:02d}{m:02d}" for h in range(24) for m in range(0, 60, cadence)]
    miss = set(missing)
    best = cur = 0
    for s in slots:
        cur = cur + 1 if s in miss else 0
        best = max(best, cur)
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", help="UTC day, default yesterday")
    args = ap.parse_args()

    cfg = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
    token = os.environ.get("HF_TOKEN", "").strip()
    repo = os.environ.get("HF_REPO", "").strip()
    if not token or not repo:
        print("[check] HF_TOKEN and HF_REPO must be set")
        return 3
    now = datetime.now(timezone.utc)
    day = args.day or (now - timedelta(days=1)).strftime("%Y-%m-%d")
    cadence = cfg["cadence_min"]
    expected_slots = 24 * 60 // cadence

    from huggingface_hub import HfApi, hf_hub_download

    api = HfApi(token=token)
    prefix = f"cycles/{day}"
    try:
        entries = list(api.list_repo_tree(repo, path_in_repo=prefix, repo_type="dataset"))
    except Exception as e:
        print(f"[check] could not list {prefix}: {type(e).__name__}: {str(e)[:200]}")
        entries = []
    slots = sorted(e.path.rsplit("/", 1)[-1] for e in entries if "." not in e.path.rsplit("/", 1)[-1])

    rows, counts, total_bytes = [], {"ok": 0, "partial": 0, "failed": 0, "unreadable": 0}, 0
    cov_sum, traffic_sum, warn_total, n_content = 0.0, 0.0, 0, 0
    for hhmm in slots:
        try:
            p = hf_hub_download(repo, f"{prefix}/{hhmm}/manifest.json", repo_type="dataset",
                                token=token)
            m = json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception:
            rows.append((hhmm, "unreadable", 0, 0, 0, ""))
            counts["unreadable"] += 1
            continue
        st = m.get("status", "failed")
        counts[st if st in counts else "failed"] += 1
        total_bytes += m.get("bytes", 0)
        cov_sum += m.get("coverage_pct", 0)
        tf = (m.get("audit") or {}).get("mean_traffic_frac")
        if tf is None and m.get("chunks"):
            ok_chunks = [c for c in m["chunks"] if c.get("file")]
            tf = sum(c.get("traffic_frac", 0) for c in ok_chunks) / max(1, len(ok_chunks))
        if tf is not None:
            traffic_sum += tf
            n_content += 1
        warns = m.get("warnings", [])
        warn_total += len(warns)
        rows.append((hhmm, st, m.get("coverage_pct", 0), m.get("bytes", 0) // 1024,
                     m.get("duration_s", 0), "; ".join(w.split(":")[0] for w in warns)))

    usable = counts["ok"] + counts["partial"]
    have = set(slots)
    missing = [f"{h:02d}{mn:02d}" for h in range(24) for mn in range(0, 60, cadence)
               if f"{h:02d}{mn:02d}" not in have]
    gap = longest_gap(missing, cadence)
    mean_cov = round(cov_sum / max(1, len(slots)), 2)
    mean_tf = round(traffic_sum / max(1, n_content), 5)

    # ---- liveness: latest.json age
    latest_age_min, latest_txt = None, "no latest.json"
    try:
        p = hf_hub_download(repo, "latest.json", repo_type="dataset", token=token)
        latest = json.loads(Path(p).read_text(encoding="utf-8"))
        up = datetime.strptime(latest["uploaded_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        latest_age_min = int((now - up).total_seconds() // 60)
        latest_txt = f"{latest['slot_utc']} {latest['status']} uploaded {latest_age_min} min ago"
    except Exception as e:
        latest_txt = f"latest.json unreadable ({type(e).__name__})"

    # ---- dataset size
    size_gb, size_txt = None, "size unknown"
    try:
        info = api.repo_info(repo, repo_type="dataset", files_metadata=True)
        size_gb = sum((s.size or 0) for s in info.siblings) / 1e9
        days_so_far = len({s.rfilename.split("/")[1] for s in info.siblings
                           if s.rfilename.startswith("cycles/")}) or 1
        size_txt = f"{size_gb:.2f} GB over {days_so_far} day(s); 7-day projection {size_gb / days_so_far * 7:.1f} GB"
    except Exception as e:
        size_txt = f"size unavailable ({type(e).__name__})"

    problems = []
    if usable < cfg["daily_min_cycles"]:
        problems.append(f"only {usable} usable cycles (need {cfg['daily_min_cycles']})")
    bad = counts["partial"] + counts["failed"] + counts["unreadable"]
    if bad > cfg["daily_max_partial"]:
        problems.append(f"{bad} partial/failed cycles (max {cfg['daily_max_partial']})")
    if gap > cfg["daily_max_gap_slots"]:
        problems.append(f"{gap} consecutive slots missing (max {cfg['daily_max_gap_slots']})")
    if n_content and mean_tf < cfg["daily_min_traffic_frac"]:
        problems.append(f"mean traffic fraction {mean_tf} below {cfg['daily_min_traffic_frac']}: "
                        f"is the traffic layer rendering?")
    if latest_age_min is not None and latest_age_min > 3 * cadence:
        problems.append(f"collector stale: last upload {latest_age_min} min ago")
    if size_gb is not None and size_gb > cfg["dataset_max_gb"]:
        problems.append(f"dataset {size_gb:.1f} GB exceeds {cfg['dataset_max_gb']} GB budget")

    lines = [
        f"## TRACK daily check for {day} (UTC)", "",
        f"**{'HEALTHY' if not problems else 'DEGRADED: ' + '; '.join(problems)}**", "",
        "| Metric | Value |", "|---|---|",
        f"| Slots expected | {expected_slots} |",
        f"| Cycles found | {len(slots)} |",
        f"| ok / partial / failed / unreadable | {counts['ok']} / {counts['partial']} / {counts['failed']} / {counts['unreadable']} |",
        f"| Mean coverage | {mean_cov} % |",
        f"| Mean traffic pixel fraction | {mean_tf} |",
        f"| Warnings across cycles | {warn_total} |",
        f"| Longest gap | {gap} slots |",
        f"| Missing slots | {len(missing)}: {' '.join(missing[:24])}{' ...' if len(missing) > 24 else ''} |",
        f"| Data this day | {total_bytes / 1e6:.1f} MB |",
        f"| Dataset | {size_txt} |",
        f"| Collector | {latest_txt} |",
        "",
    ]
    flagged = [r for r in rows if r[1] != "ok" or r[5]]
    if flagged:
        lines += ["| Slot | Status | Coverage % | KB | Seconds | Warnings |", "|---|---|---|---|---|---|"]
        lines += [f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} |" for r in flagged]
    text = "\n".join(lines)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
