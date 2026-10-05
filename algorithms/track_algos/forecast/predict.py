"""Forecast every road's traffic level for a given day and time.

Four forecasters, all used the same way:

    model.fit(history, rows)       learn from these captures (row numbers)
    model.predict(when)            -> one level per road, 0 green .. 3 dark red

    AlwaysClear          every road green. The floor: any real model must beat it.

    SameTimeLastWeek     the capture taken on the same weekday at the closest
                         time of day. The honest baseline: "it will be like
                         last Tuesday at 5 pm".

    KNNForecast          find the k past captures whose time is most like the
                         target (time of day, weekday or weekend), and give
                         each road the level most of them agree on.

    LogisticForecast     logistic regression learns how a road's level depends
                         on how busy that road usually is overall, how busy it
                         usually is at that hour, and the time of day.

`when` is a datetime in Dhaka time.
"""

from datetime import datetime

import numpy as np

from ..ml.knn import KNNRegressor, standardize
from ..ml.logistic_regression import OneVsRest
from .history import History, WEEKEND_DAYS

LEVELS = 4


def time_features(minute_of_day, weekend) -> np.ndarray:
    """Time of day on a circle (so 11:50 pm sits next to 12:10 am), plus a
    weekend flag scaled so a weekday is never 'closer' than another weekday
    at a different hour would be: the largest circle distance is 2, the
    weekend gap is 3."""
    angle = 2 * np.pi * np.asarray(minute_of_day, dtype=float) / 1440.0
    return np.column_stack([np.sin(angle), np.cos(angle), 3.0 * np.asarray(weekend, dtype=float)])


def majority_level(neighbour_levels: np.ndarray) -> np.ndarray:
    """(k, roads) levels -> the level most neighbours agree on, per road.
    A tie goes to the worse level, the same rule the spread uses."""
    votes = np.stack([(neighbour_levels == level).sum(axis=0) for level in range(LEVELS)])
    return (LEVELS - 1 - votes[::-1].argmax(axis=0)).astype(np.uint8)


class AlwaysClear:
    name = "always clear"

    def fit(self, history: History, rows: np.ndarray) -> "AlwaysClear":
        self.roads = history.roads
        return self

    def predict(self, when: datetime) -> np.ndarray:
        return np.zeros(self.roads, dtype=np.uint8)


class SameTimeLastWeek:
    name = "same time last week"

    def fit(self, history: History, rows: np.ndarray) -> "SameTimeLastWeek":
        self.history = history
        self.rows = np.asarray(rows)
        local = history.local
        self.weekday = np.array([local[r].weekday() for r in self.rows])
        self.minute = history.minute_of_day[self.rows]
        self.weekend = history.weekend[self.rows]
        return self

    def predict(self, when: datetime) -> np.ndarray:
        minute = when.hour * 60 + when.minute
        gap = np.abs(self.minute - minute)
        gap = np.minimum(gap, 1440 - gap)                       # minutes apart on the clock
        same_day = self.weekday == when.weekday()
        if not same_day.any():                                  # no capture on that weekday:
            same_day = self.weekend == (when.weekday() in WEEKEND_DAYS)   # use the same kind of day
        gap = np.where(same_day, gap, 10_000)
        # closest time of day; among equals, the most recent capture
        best = np.lexsort((-self.rows, gap))[0]
        return self.history.levels[self.rows[best]]


class KNNForecast:
    name = "KNN"

    def __init__(self, k: int = 6):
        self.k = k

    def fit(self, history: History, rows: np.ndarray) -> "KNNForecast":
        self.history = history
        self.rows = np.asarray(rows)
        features = time_features(history.minute_of_day[self.rows], history.weekend[self.rows])
        self.search = KNNRegressor(k=self.k).fit(features, np.zeros(len(self.rows)))
        return self

    def neighbours(self, when: datetime) -> np.ndarray:
        """Row numbers of the k past captures most like `when`."""
        target = time_features([when.hour * 60 + when.minute], [when.weekday() in WEEKEND_DAYS])
        _, nearest = self.search.kneighbors(target)
        return self.rows[nearest[0]]

    def predict(self, when: datetime) -> np.ndarray:
        return majority_level(self.history.levels[self.neighbours(when)])


class LogisticForecast:
    name = "logistic regression"

    def __init__(self, samples: int = 200_000, epochs: int = 300, seed: int = 0):
        self.samples, self.epochs, self.seed = samples, epochs, seed

    def fit(self, history: History, rows: np.ndarray) -> "LogisticForecast":
        rows = np.asarray(rows)
        hours = history.minute_of_day[rows] // 60
        weekend = history.weekend[rows].astype(int)

        # How busy each road usually is: overall, and at each hour of a weekday
        # or weekend. Added up capture by capture rather than copying the whole
        # table as floats, which for two weeks would be over a gigabyte. An
        # hour with no captures falls back to the overall value.
        sums = np.zeros((2, 24, history.roads), dtype=np.float32)
        counts = np.zeros((2, 24), dtype=np.int64)
        for row, hour, day_type in zip(rows, hours, weekend):
            sums[day_type, hour] += history.levels[row]
            counts[day_type, hour] += 1
        self.overall = sums.sum(axis=(0, 1)) / max(1, counts.sum())
        self.profile = np.tile(self.overall, (2, 24, 1))
        seen = counts > 0
        self.profile[seen] = sums[seen] / counts[seen][:, None]

        # Train on a random sample of (capture, road) pairs.
        rng = np.random.default_rng(self.seed)
        pick_capture = rng.integers(0, len(rows), self.samples)
        pick_road = rng.integers(0, history.roads, self.samples)
        X = self._features(pick_road, hours[pick_capture], weekend[pick_capture],
                           history.minute_of_day[rows][pick_capture])
        y = history.levels[rows[pick_capture], pick_road]
        X, self.mean, self.std = standardize(X)
        self.model = OneVsRest(classes=range(LEVELS), lr=0.5, epochs=self.epochs).fit(X, y)
        return self

    def _features(self, road, hour, weekend, minute) -> np.ndarray:
        clock = time_features(minute, weekend)
        return np.column_stack([self.overall[road], self.profile[weekend, hour, road], clock])

    def predict(self, when: datetime) -> np.ndarray:
        roads = np.arange(self.overall.size)
        hour = np.full(roads.size, when.hour)
        weekend = np.full(roads.size, int(when.weekday() in WEEKEND_DAYS))
        minute = np.full(roads.size, when.hour * 60 + when.minute)
        X, _, _ = standardize(self._features(roads, hour, weekend, minute), self.mean, self.std)
        return self.model.predict(X).astype(np.uint8)


def all_forecasters() -> list:
    return [AlwaysClear(), SameTimeLastWeek(), KNNForecast(), LogisticForecast()]
