"""K-nearest-neighbours classifier, written from scratch.

No training beyond remembering the data. To classify a new point, find the k
stored points closest to it (Euclidean) and take a majority vote of their
labels, optionally weighted by 1/distance so closer neighbours count more.

In TRACK a point is (road segment features, time-of-day, day-of-week, cluster
scenario) and the label is the congestion class 1..4. KNN's strength here is
that it makes no assumption about the shape of the relationship; its cost is
that every prediction scans the training set, which is fine at our scale.
Features should be standardised first (see `standardize`) so time and
distance-like features do not dominate each other.
"""

from collections import Counter

import numpy as np


def standardize(X, mean=None, std=None):
    """Z-score columns; returns (Xs, mean, std) so the same scaling can be reused."""
    X = np.asarray(X, dtype=float)
    mean = X.mean(axis=0) if mean is None else mean
    std = X.std(axis=0) if std is None else std
    std = np.where(std == 0, 1.0, std)
    return (X - mean) / std, mean, std


class KNN:
    def __init__(self, k: int = 5, weighted: bool = True):
        self.k, self.weighted = k, weighted
        self.X = self.y = None

    def fit(self, X, y) -> "KNN":
        self.X = np.asarray(X, dtype=float)
        self.y = np.asarray(y)
        return self

    def predict(self, X) -> np.ndarray:
        X = np.asarray(X, dtype=float)
        out = []
        for x in X:
            d = np.sqrt(((self.X - x) ** 2).sum(axis=1))
            idx = np.argsort(d)[: self.k]
            if self.weighted:
                votes = Counter()
                for i in idx:
                    votes[self.y[i]] += 1.0 / (d[i] + 1e-9)
                out.append(max(votes, key=votes.get))
            else:
                out.append(Counter(self.y[idx].tolist()).most_common(1)[0][0])
        return np.asarray(out)

    def score(self, X, y) -> float:
        return float((self.predict(X) == np.asarray(y)).mean())


class KNNRegressor:
    """KNN for a continuous target instead of a label.

    Same idea as the classifier: find the k nearest stored points and combine
    their values, weighted by 1/distance so a road 40 m away counts for more
    than one 400 m away. TRACK uses it to give a traffic weight to the ~89 % of
    Dhaka's roads Google never paints, from the roads around them that it does.

    `kneighbors` is exposed separately because the caller needs more than the
    average: it also looks at what KIND of roads the neighbours are before
    deciding how much better than them the quiet side street probably is.

    Distances are computed by brute force in chunks. With a few thousand
    observed roads and tens of thousands of queries that is one large matrix
    multiply per chunk, which numpy does far faster than any tree we could
    write, and it keeps the code honest: there is no index to get stale.
    """

    def __init__(self, k: int = 5, weighted: bool = True):
        self.k, self.weighted = k, weighted
        self.X = self.y = None

    def fit(self, X, y) -> "KNNRegressor":
        self.X = np.asarray(X, dtype=float)
        self.y = np.asarray(y, dtype=float)
        return self

    def kneighbors(self, X, chunk: int = 2048):
        """Returns (distances, indices), both (n_queries, k), nearest first."""
        X = np.asarray(X, dtype=float)
        k = min(self.k, self.X.shape[0])
        dists = np.empty((X.shape[0], k), dtype=float)
        idxs = np.empty((X.shape[0], k), dtype=np.int64)
        for s in range(0, X.shape[0], chunk):
            q = X[s: s + chunk]
            # |a-b|^2 = |a|^2 + |b|^2 - 2a.b: one matrix product instead of a
            # (chunk, n_stored, n_features) temporary, which matters at city scale
            d2 = ((q ** 2).sum(1)[:, None] + (self.X ** 2).sum(1)[None, :]
                  - 2.0 * (q @ self.X.T))
            d = np.sqrt(np.maximum(d2, 0.0))
            part = np.argpartition(d, k - 1, axis=1)[:, :k]
            dpart = np.take_along_axis(d, part, axis=1)
            order = np.argsort(dpart, axis=1)
            idxs[s: s + chunk] = np.take_along_axis(part, order, axis=1)
            dists[s: s + chunk] = np.take_along_axis(dpart, order, axis=1)
        return dists, idxs

    @staticmethod
    def combine(values, dists, weighted: bool = True):
        """Distance-weighted mean of neighbour values, row by row."""
        if not weighted:
            return values.mean(axis=1)
        w = 1.0 / (dists + 1e-6)
        return (values * w).sum(axis=1) / w.sum(axis=1)

    def predict(self, X, chunk: int = 2048) -> np.ndarray:
        d, i = self.kneighbors(X, chunk)
        return self.combine(self.y[i], d, self.weighted)
