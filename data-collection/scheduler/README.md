# Scheduler

The scheduled capture is the GitHub Actions workflow
`.github/workflows/collect.yml`: every 10 minutes it captures, runs the
pipeline, builds the site and deploys it. Runs never overlap. The run history
under Actions is the first place to look when something is wrong.

`daily_check.py` reports on one day of the Hugging Face archive: captures
found, their status and coverage, the longest gap between them, whether the
collector is still uploading, and the dataset size. `.github/workflows/health.yml`
runs it every morning and fails, which GitHub reports by email, when the day
was below the thresholds at the top of the script. It needs `HF_TOKEN` and
`HF_REPO`; without them both do nothing.

The thresholds assume a run takes about 8 minutes, so a full day is roughly
144 captures. They are deliberately loose: they are meant to catch collection
having stopped or broken, not a few slow runs. Change them at the top of
`daily_check.py` if the cadence changes.

At about 3 MB a capture and 144 captures a day, the archive grows roughly
0.5 GB a day, so the free 100 GB private tier lasts about six months. Watch
the dataset size line in the daily report.
