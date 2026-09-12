"""K-means, KNN and logistic regression on small synthetic problems with known
answers: two well-separated blobs must be found, and a linearly separable
labelling must be learned near-perfectly."""

import numpy as np

from track_algos.ml.kmeans import KMeans
from track_algos.ml.knn import KNN, standardize
from track_algos.ml.logistic_regression import LogisticRegression, OneVsRest


def blobs(seed=0):
    rng = np.random.default_rng(seed)
    a = rng.normal([0, 0], 0.3, (60, 2))
    b = rng.normal([5, 5], 0.3, (60, 2))
    c = rng.normal([0, 5], 0.3, (60, 2))
    X = np.vstack([a, b, c])
    y = np.array([0] * 60 + [1] * 60 + [2] * 60)
    return X, y


def test_kmeans_finds_blobs():
    X, y = blobs()
    km = KMeans(k=3, seed=1).fit(X)
    # each true blob should map to exactly one cluster
    for c in range(3):
        assert len(set(km.labels_[y == c].tolist())) == 1
    assert km.inertia_ < 60.0
    assert km.predict(np.array([[5.1, 4.9]]))[0] == km.labels_[y == 1][0]


def test_knn_classifies():
    X, y = blobs()
    Xs, mean, std = standardize(X)
    knn = KNN(k=5).fit(Xs, y)
    q, _, _ = standardize(np.array([[0.1, 0.0], [5.0, 5.2], [0.2, 4.8]]), mean, std)
    assert knn.predict(q).tolist() == [0, 1, 2]
    assert knn.score(Xs, y) > 0.98


def test_logistic_regression_binary():
    rng = np.random.default_rng(0)
    X = rng.uniform(-3, 3, (300, 2))
    y = (X[:, 0] + 0.5 * X[:, 1] > 0.3).astype(int)
    m = LogisticRegression(lr=0.5, epochs=800).fit(X, y)
    assert m.score(X, y) > 0.97
    assert m.loss_history[-1] < m.loss_history[0]
    p = m.predict_proba(np.array([[3, 3], [-3, -3]]))
    assert p[0] > 0.9 and p[1] < 0.1


def test_one_vs_rest_multiclass():
    X, y = blobs()
    ovr = OneVsRest(classes=[0, 1, 2], lr=0.5, epochs=400).fit(X, y)
    assert ovr.score(X, y) > 0.97
    assert ovr.predict_proba(X).shape == (180, 3)
