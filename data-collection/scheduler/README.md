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
