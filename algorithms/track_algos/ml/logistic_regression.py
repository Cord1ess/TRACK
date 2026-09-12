"""Logistic regression, written from scratch (binary and one-vs-rest multiclass).

Models the probability that a segment is congested as
    p = sigmoid(w . x + b)
and fits w, b by gradient descent on the log-loss (cross-entropy) with L2
regularisation. Compared with KNN it is a parametric, linear model: fast to
predict, its weights are interpretable ("evening rush raises the odds of red
on primary roads by ..."), and it generalises smoothly, but it can only draw
a linear boundary in feature space. Comparing the two on the same edge table
is one of TRACK's evaluation results.

`LogisticRegression` handles binary labels {0,1}. `OneVsRest` wraps it for the
four congestion classes by training one binary model per class and picking
the highest probability.
"""

import numpy as np


def sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


class LogisticRegression:
    def __init__(self, lr: float = 0.1, epochs: int = 500, l2: float = 1e-3, seed: int = 0):
        self.lr, self.epochs, self.l2 = lr, epochs, l2
        self.rng = np.random.default_rng(seed)
        self.w = None
        self.b = 0.0
        self.loss_history: list[float] = []

    def fit(self, X, y) -> "LogisticRegression":
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)
        n, d = X.shape
        self.w = np.zeros(d)
        self.b = 0.0
        for _ in range(self.epochs):
            p = sigmoid(X @ self.w + self.b)
            grad_w = X.T @ (p - y) / n + self.l2 * self.w
            grad_b = float((p - y).mean())
            self.w -= self.lr * grad_w
            self.b -= self.lr * grad_b
            eps = 1e-9
            self.loss_history.append(float(-(y * np.log(p + eps) + (1 - y) * np.log(1 - p + eps)).mean()))
        return self

    def predict_proba(self, X) -> np.ndarray:
        return sigmoid(np.asarray(X, dtype=float) @ self.w + self.b)

    def predict(self, X, threshold: float = 0.5) -> np.ndarray:
        return (self.predict_proba(X) >= threshold).astype(int)

    def score(self, X, y) -> float:
        return float((self.predict(X) == np.asarray(y)).mean())


class OneVsRest:
    """Multiclass by one binary logistic model per class."""

    def __init__(self, classes, **kw):
        self.classes = list(classes)
        self.models = {c: LogisticRegression(**kw) for c in self.classes}

    def fit(self, X, y) -> "OneVsRest":
        y = np.asarray(y)
        for c, m in self.models.items():
            m.fit(X, (y == c).astype(float))
        return self

    def predict_proba(self, X) -> np.ndarray:
        P = np.column_stack([self.models[c].predict_proba(X) for c in self.classes])
        return P / np.clip(P.sum(axis=1, keepdims=True), 1e-9, None)

    def predict(self, X) -> np.ndarray:
        return np.asarray(self.classes)[self.predict_proba(X).argmax(axis=1)]

    def score(self, X, y) -> float:
        return float((self.predict(X) == np.asarray(y)).mean())
