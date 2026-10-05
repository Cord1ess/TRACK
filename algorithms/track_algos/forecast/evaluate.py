"""Test the forecasters on captures they have never seen.

The captures are split by time: everything before `split` is training, everything
after is the test. Each forecaster learns from the training captures only, then
forecasts every test capture's time, and the forecast is compared with what was
actually captured.

Scores are counted on the roads Google painted at that moment, because those
are real readings; the other roads were themselves predicted by the spread.

    right_level   share of painted roads given exactly the right level
    too_green     share given a milder level than the truth: the error that
                  would send traffic into a jam
    by_hour       right_level for each hour of the day (Dhaka time)

With two weeks of data the default split is "the last 7 days are the test",
which is a true week-ahead forecast. With less, the last 30% is the test.
"""

from datetime import datetime

import numpy as np

from .history import DHAKA, History

DAY_S = 86400


def default_split(history: History) -> float:
    first, last = float(history.times[0]), float(history.times[-1])
    if last - first >= 13 * DAY_S:
        return last - 7 * DAY_S
    return first + 0.7 * (last - first)


def evaluate(history: History, forecasters: list, split: float | None = None,
             every: int = 3, progress=None) -> dict:
    split = default_split(history) if split is None else split
    train = np.flatnonzero(history.times < split)
    test = np.flatnonzero(history.times >= split)[::every]
    if train.size == 0 or test.size == 0:
        raise SystemExit("the split leaves no training or no test captures")

    local = history.local
    hours = history.minute_of_day // 60
    report = {"split_utc": datetime.fromtimestamp(split, DHAKA).isoformat(),
              "train": describe(history, train), "test": describe(history, test),
              "models": {}}

    for model in forecasters:
        if progress:
            progress.step(model.name, f"learning from {train.size:,} captures")
        model.fit(history, train)
        right = too_green = counted = 0
        right_by_hour = np.zeros(24)
        counted_by_hour = np.zeros(24)
        for n, row in enumerate(test):
            if progress and n % 10 == 0:
                progress.step(model.name, f"forecasting test capture {n + 1:,} of {test.size:,}",
                              n + 1, test.size)
            forecast = model.predict(local[row])
            painted = history.painted[row]
            truth = history.levels[row][painted]
            guess = forecast[painted]
            hits = int((guess == truth).sum())
            right += hits
            too_green += int((guess < truth).sum())
            counted += truth.size
            right_by_hour[hours[row]] += hits
            counted_by_hour[hours[row]] += truth.size
        report["models"][model.name] = {
            "right_level": round(right / max(1, counted), 4),
            "too_green": round(too_green / max(1, counted), 4),
            "roads_scored": int(counted),
            "by_hour": [round(r / c, 4) if c else None
                        for r, c in zip(right_by_hour, counted_by_hour)],
        }
    return report


def describe(history: History, rows: np.ndarray) -> dict:
    local = history.local
    return {"captures": int(rows.size),
            "from": local[rows[0]].strftime("%a %d %b %I:%M %p"),
            "to": local[rows[-1]].strftime("%a %d %b %I:%M %p")}
