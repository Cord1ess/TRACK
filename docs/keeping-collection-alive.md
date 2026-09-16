# Keeping collection alive

The goal is an unbroken sequence: traffic data for every window, with no
period missing. This is what keeps it running, and what you have to set up by
hand.

## What runs on its own

Four things keep the chain alive, listed by how often each one matters.

| # | Mechanism | Covers | Delay after a fault |
|---|---|---|---|
| 1 | The capture retries inside the run, 3 attempts | a failed download | about 75 seconds |
| 2 | Every run starts the next one, whatever happened | any failed step | up to 5 minutes |
| 3 | GitHub's cron ticks every 5 minutes | a runner killed mid-run | up to 5 minutes |
| 4 | An external trigger posts to GitHub | GitHub's cron not firing | up to 5 minutes |

Only 4 needs setting up. The rest is in the workflow.

A run takes about 7 minutes and starts its successor as it finishes, so
captures arrive roughly every 7 minutes. The chain sets the pace, not the
clock.

## What you have to set up

### The external trigger

GitHub's own schedule is unreliable: it fired twice in ten runs during
testing, and GitHub documents that scheduled runs can be dropped under load.
The chain normally makes this irrelevant, but if a runner is killed the chain
stops and something outside GitHub has to restart it.

1. Make a GitHub token with `repo` scope at
   <https://github.com/settings/tokens>.
2. Sign up at <https://cron-job.org> (free, one-minute granularity).
3. Create a job that runs every 5 minutes:

   - URL: `https://api.github.com/repos/Cord1ess/TRACK/dispatches`
   - Method: POST
   - Header: `Authorization: Bearer YOUR_TOKEN`
   - Header: `Accept: application/vnd.github+json`
   - Body: `{"event_type":"collect"}`

The workflow accepts `repository_dispatch` with type `collect`, and the
concurrency group means a trigger arriving while a run is going simply becomes
the pending run rather than starting a second one.

### The heartbeat

This is how you find out the chain has stopped, in minutes rather than hours.
Collection was dead for five hours on 16 September before anyone noticed.

1. Sign up at <https://healthchecks.io> (free tier covers 20 checks).
2. Create a check with period 10 minutes and grace 15 minutes.
3. Copy its ping URL into a repository secret named `HEARTBEAT_URL`.

The workflow pings it after every successful capture. If the pings stop, the
service emails you. Without the secret the step is skipped.

## Checking it worked

`scheduler/daily_check.py` runs every morning and reports the previous day:
how many captures arrived, the longest gap, and every gap over 20 minutes.
A day with no gap over 30 minutes is clean.

Read it under the health workflow's run summary. It exits non-zero when the
day was not clean, which turns the run red.

## What none of this fixes

GitHub Actions being unavailable. No retry logic inside GitHub survives
GitHub itself being down. If a trial week shows gaps from that cause, the
answer is a second capturer somewhere else, writing to the same archive.
Duplicate captures are harmless: they carry their own timestamps and the
site keeps the newest.
