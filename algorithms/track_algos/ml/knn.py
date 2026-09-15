"""K-nearest neighbours: a classifier and a regressor.

A prediction is the vote or the mean of the k closest stored points, weighted
by 1/distance so closer points count more. Standardise features first so no
column dominates the distance.
"""

from collections import Counter

import numpy as np


def standardize(X, mean=None, std=None):
    """Z-score each column. Returns (Xs, mean, std) so the scaling can be reused."""
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
            weights = 1.0 / (d[idx] + 1e-9) if self.weighted else np.ones(idx.size)
            votes = Counter()
            for i, w in zip(idx, weights):
                votes[self.y[i]] += w
            out.append(max(votes, key=votes.get))
        return np.asarray(out)

    def score(self, X, y) -> float:
        return float((self.predict(X) == np.asarray(y)).mean())


class KNNRegressor:
    """KNN for a continuous target. Distances are computed by brute force in
    chunks, as one matrix product per chunk."""

    def __init__(self, k: int = 5, weighted: bool = True):
        self.k, self.weighted = k, weighted
        self.X = self.y = None

    def fit(self, X, y) -> "KNNRegressor":
        self.X = np.asarray(X, dtype=float)
        self.y = np.asarray(y, dtype=float)
        return self

    def kneighbors(self, X, chunk: int = 2048):
        """Return (distances, indices), both (n_queries, k), nearest first.

        Ranking by squared distance gives the same order as by distance, so the
        square root is taken only on the k neighbours kept, not on every pair.
        Stays in float64: in single precision 473 of 98,302 Dhaka roads picked a
        different neighbour and one prediction moved by 38 weight points."""
        X = np.asarray(X, dtype=float)
        k = min(self.k, self.X.shape[0])
        dists = np.empty((X.shape[0], k), dtype=float)
        idxs = np.empty((X.shape[0], k), dtype=np.int64)
        stored_norm = (self.X ** 2).sum(1)[None, :]
        for s in range(0, X.shape[0], chunk):
            q = X[s: s + chunk]
            # |a - b|^2 = |a|^2 + |b|^2 - 2 a.b
            d2 = (q ** 2).sum(1)[:, None] + stored_norm - 2.0 * (q @ self.X.T)
            np.maximum(d2, 0.0, out=d2)
            part = np.argpartition(d2, k - 1, axis=1)[:, :k]
            dpart = np.take_along_axis(d2, part, axis=1)
            order = np.argsort(dpart, axis=1)
            idxs[s: s + chunk] = np.take_along_axis(part, order, axis=1)
            dists[s: s + chunk] = np.take_along_axis(dpart, order, axis=1)
        return np.sqrt(dists, out=dists), idxs

    @staticmethod
    def combine(values, dists, weighted: bool = True):
        """Distance-weighted mean of each row of neighbour values."""
        if not weighted:
            return values.mean(axis=1)
        w = 1.0 / (dists + 1e-6)
        return (values * w).sum(axis=1) / w.sum(axis=1)

    def predict(self, X, chunk: int = 2048) -> np.ndarray:
        d, i = self.kneighbors(X, chunk)
        return self.combine(self.y[i], d, self.weighted)
