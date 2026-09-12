# Scheduler (Phase E, next)

The daily capture system. Not built yet; the design is in `docs/project-plan.md` §5.

Planned `run_daily.py`:
- loop on a fixed cadence (15 min), call `collector/capture.py --name <YYYY-MM-DD>/<HHMM>`
- never overlap runs, skip a slot whose manifest already exists
- free-disk guard, per-day index `captures/index.jsonl`, 07:00 Dhaka summary
- optional push of each finished day through `storage/upload.py`

`daily_check.py` is the health report from the earlier design (counts a day's
captures on Hugging Face). It will be adapted to read local `captures/` first.
