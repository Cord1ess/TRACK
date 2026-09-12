"""K-means clustering, written from scratch.

Groups observations into k clusters by repeating two steps until the
assignments stop changing:
    assign: each point joins the cluster whose centroid is nearest
    update: each centroid moves to the mean of its points
This minimises within-cluster squared distance. It is the pattern-discovery
step in TRACK: each edge (or time slot) becomes a vector of congestion values
across the week, and the clusters are the recurring traffic scenarios
("weekday rush hour", "weekend afternoon", "off-peak") the simulation runs on.

Initialisation uses k-means++ (spread-out starting centroids), which avoids
the poor local optima plain random starts often produce.
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
