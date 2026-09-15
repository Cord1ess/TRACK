"""K-means clustering with k-means++ initialisation.

Repeats two steps until the centroids stop moving: assign each point to its
nearest centroid, then move each centroid to the mean of its points.
"""

import numpy as np


class KMeans:
    def __init__(self, k: int, max_iter: int = 100, tol: float = 1e-4, seed: int = 0):
        self.k, self.max_iter, self.tol = k, max_iter, tol
        self.rng = np.random.default_rng(seed)
        self.centroids: np.ndarray | None = None
        self.labels_: np.ndarray | None = None
        self.inertia_: float = 0.0

    def _init_pp(self, X: np.ndarray) -> np.ndarray:
        """k-means++: each new centroid is drawn with probability proportional
        to its squared distance from the nearest existing one."""
        n = X.shape[0]
        centroids = [X[self.rng.integers(n)]]
        for _ in range(1, self.k):
            d2 = np.min(((X[:, None, :] - np.asarray(centroids)[None, :, :]) ** 2).sum(axis=2), axis=1)
            probs = d2 / d2.sum() if d2.sum() > 0 else np.full(n, 1.0 / n)
            centroids.append(X[self.rng.choice(n, p=probs)])
        return np.asarray(centroids, dtype=float)

    def fit(self, X) -> "KMeans":
        X = np.asarray(X, dtype=float)
        self.centroids = self._init_pp(X)
        for _ in range(self.max_iter):
            labels = self.predict(X)
            new = np.array([X[labels == j].mean(axis=0) if np.any(labels == j) else self.centroids[j]
                            for j in range(self.k)])
            shift = np.abs(new - self.centroids).max()
            self.centroids = new
            if shift < self.tol:
                break
        self.labels_ = self.predict(X)
        self.inertia_ = float(((X - self.centroids[self.labels_]) ** 2).sum())
        return self

    def predict(self, X) -> np.ndarray:
        X = np.asarray(X, dtype=float)
        d2 = ((X[:, None, :] - self.centroids[None, :, :]) ** 2).sum(axis=2)
        return d2.argmin(axis=1)
