"""Give every road a traffic weight, including the ones Google never paints.

    python -m track_algos.traffic.impute --graph output/graph/dhaka.json
        --observed output/traffic/observed.csv --out output/traffic/complete.csv

Google paints about a tenth of Dhaka's road length. For each unobserved edge:
take the k nearest observed edges, average their weights by 1/distance, and if
this road is smaller than those neighbours step one rung down the ladder in
weights.py. That estimate fades with distance toward the mean of the edge's
K-means zone. Observed weights are never changed.

Two checks run alongside and land in the report: `measure_class_step` measures
the demotion assumption on observed roads, and `evaluate` hides a share of the
observed edges and scores the prediction against them.
"""

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from ..decode.decoder import read_csv, read_meta, write_csv, write_geojson
from ..geo import local_xy
from ..graph.road_graph import RoadGraph, class_rank
from ..ml.kmeans import KMeans
from ..ml.knn import KNNRegressor
from .weights import CEILING, FLOOR, demote, summary, to_class

METHOD_NAMES = {0: "observed", 1: "neighbours", 2: "neighbours_demoted", 3: "zone_blended"}


@dataclass
class Config:
    k: int = 5                    # neighbours per unobserved edge
    max_dist_m: float = 500.0     # beyond this a neighbour counts for nothing
    decay_m: float = 250.0        # distance over which the neighbours fade to the zone mean
    zones: int = 24               # K-means zones for the fallback
    demote_smaller: bool = True
    demote_strength: float = 1.0  # 0 copies the neighbours, 1 is a full rung down
    rank_margin: float = 0.5      # how much smaller a road must be to count as smaller
    seed: int = 0


def edge_table(graph: RoadGraph) -> dict:
    """Arrays for every edge: id, midpoint in local metres, class rank."""
    ids, lon, lat, rank, hw = [], [], [], [], []
    for e in graph.edges.values():
        g = e.geometry
        m = g[len(g) // 2] if len(g) > 2 else [(g[0][0] + g[-1][0]) / 2, (g[0][1] + g[-1][1]) / 2]
        ids.append(e.id); lon.append(m[0]); lat.append(m[1])
        rank.append(class_rank(e.highway)); hw.append(e.highway)
    lon = np.asarray(lon); lat = np.asarray(lat)
    x, y = local_xy(lon, lat, float(lon.mean()), float(lat.mean()))
    return {"id": np.asarray(ids, dtype=np.int64), "xy": np.column_stack([x, y]),
            "rank": np.asarray(rank, dtype=float), "highway": hw, "lon": lon, "lat": lat}


def _step_stats(v: np.ndarray) -> dict:
    return {"pairs": int(v.size), "mean_weight_change": round(float(v.mean()), 2),
            "median": round(float(np.median(v)), 2),
            "share_lower": round(float((v < -1).mean()), 3),
            "share_equal": round(float((np.abs(v) <= 1).mean()), 3),
            "share_higher": round(float((v > 1).mean()), 3)}


def measure_class_step(tab: dict, weight: np.ndarray, observed: np.ndarray,
                       max_dist_m: float = 300.0, k: int = 12,
                       busy_threshold: float = 55.0) -> dict:
    """On observed roads only: how much lower is a smaller road's weight than a
    nearby bigger road's? `beside_busy` keeps the pairs where the bigger road
    is at least busy_threshold, which is the case the ladder is about."""
    oi = np.flatnonzero(observed)
    if oi.size < 50:
        return {"pairs": 0, "note": "not enough observed edges"}
    knn = KNNRegressor(k=min(k, oi.size)).fit(tab["xy"][oi], weight[oi])
    d, nb = knn.kneighbors(tab["xy"][oi])
    buckets, busy = defaultdict(list), []
    for a in range(oi.size):
        for col in range(1, d.shape[1]):            # column 0 is the edge itself
            if d[a, col] > max_dist_m:
                break
            b = nb[a, col]
            dr = int(round(tab["rank"][oi[b]] - tab["rank"][oi[a]]))
            if dr > 0:                              # b is the smaller road
                diff = float(weight[oi[b]] - weight[oi[a]])
                buckets[min(dr, 3)].append(diff)
                if weight[oi[a]] >= busy_threshold:
                    busy.append(diff)
    out = {"pairs": int(sum(len(v) for v in buckets.values())), "max_dist_m": max_dist_m,
           "busy_threshold": busy_threshold, "by_rank_delta": {}}
    for dr in sorted(buckets):
        out["by_rank_delta"][str(dr)] = _step_stats(np.asarray(buckets[dr]))
    allv = np.concatenate([np.asarray(v) for v in buckets.values()]) if buckets else np.zeros(0)
    if allv.size:
        out["all_pairs"] = _step_stats(allv)
    out["beside_busy"] = _step_stats(np.asarray(busy)) if busy else {"pairs": 0}
    return out


def predict(tab: dict, weight: np.ndarray, observed: np.ndarray, cfg: Config,
            model: str = "knn_demote") -> tuple[np.ndarray, np.ndarray]:
    """Predict every edge where observed is False.

    model: "global" (mean of observed), "nearest", "knn" (distance-weighted
    mean) or "knn_demote" (the same plus the ladder). Returns (weights, method
    code per edge: 0 observed, 1 neighbours, 2 neighbours demoted, 3 zone).
    """
    n = weight.size
    out = weight.astype(float).copy()
    method = np.zeros(n, dtype=np.int8)
    oi = np.flatnonzero(observed)
    ui = np.flatnonzero(~observed)
    if oi.size == 0 or ui.size == 0:
        return out, method

    obs_w = weight[oi]
    if model == "global":
        out[ui] = float(obs_w.mean())
        method[ui] = 1
        return np.clip(out, FLOOR, CEILING), method

    k = 1 if model == "nearest" else min(cfg.k, oi.size)
    knn = KNNRegressor(k=k).fit(tab["xy"][oi], obs_w)
    d, nb = knn.kneighbors(tab["xy"][ui])

    near = d <= cfg.max_dist_m
    wt = np.where(near, 1.0 / (d + 1e-6), 0.0)
    tot = wt.sum(axis=1)
    safe = np.where(tot > 0, tot, 1.0)
    w_nb = (obs_w[nb] * wt).sum(axis=1) / safe
    r_nb = (tab["rank"][oi][nb] * wt).sum(axis=1) / safe
    smaller = tab["rank"][ui] > r_nb + cfg.rank_margin
    demoting = model == "knn_demote" and cfg.demote_smaller and cfg.demote_strength > 0

    def step_down(w, apply_to):
        if not demoting:
            return w
        return np.where(apply_to, w + cfg.demote_strength * (demote(w) - w), w)

    w_nb = step_down(w_nb, smaller)

    # zone fallback: the mean of the observed roads in this edge's K-means zone
    km = KMeans(k=min(cfg.zones, max(1, oi.size // 20)), seed=cfg.seed).fit(tab["xy"][oi])
    nz = km.centroids.shape[0]
    zone_mean = np.asarray([obs_w[km.labels_ == z].mean() if (km.labels_ == z).any() else obs_w.mean()
                            for z in range(nz)])
    zone_rank = np.asarray([tab["rank"][oi][km.labels_ == z].mean() if (km.labels_ == z).any()
                            else tab["rank"][oi].mean() for z in range(nz)])
    zi = np.argmin(((tab["xy"][ui][:, None, :] - km.centroids[None, :, :]) ** 2).sum(axis=2), axis=1)
    w_zone = step_down(zone_mean[zi], tab["rank"][ui] > zone_rank[zi] + cfg.rank_margin)

    # fade from the neighbours to the zone with distance to the nearest observed road
    d0 = d[:, 0]
    alpha = np.where(d0 <= cfg.max_dist_m, np.exp(-d0 / max(cfg.decay_m, 1.0)), 0.0)
    pred = alpha * w_nb + (1.0 - alpha) * w_zone

    out[ui] = np.clip(pred, FLOOR, CEILING)
    method[ui] = np.where(alpha < 0.5, 3, np.where(smaller & demoting, 2, 1)).astype(np.int8)
    return out, method


def _splits(oi: np.ndarray, holdout: float, repeats: int, seed: int):
    """The held-out edge sets, the same sequence for every model."""
    rng = np.random.default_rng(seed)
    for _ in range(repeats):
        yield rng.permutation(oi)[: max(1, int(holdout * oi.size))]


def evaluate(tab: dict, weight: np.ndarray, observed: np.ndarray, cfg: Config,
             holdout: float = 0.2, repeats: int = 3) -> dict:
    """Hide a share of the observed edges, predict them from the rest, score."""
    oi = np.flatnonzero(observed)

    def run(model: str, c: Config) -> list[tuple[np.ndarray, np.ndarray]]:
        pairs = []
        for hide in _splits(oi, holdout, repeats, cfg.seed):
            mask = observed.copy()
            mask[hide] = False
            pred, _ = predict(tab, np.where(mask, weight, 0.0), mask, c, model=model)
            pairs.append((pred[hide], weight[hide]))
        return pairs

    mean = lambda vals: round(float(np.mean(vals)), 2)
    models = {}
    for m in ("global", "nearest", "knn", "knn_demote"):
        pairs = run(m, cfg)
        models[m] = {
            "mae": mean([np.abs(p - t).mean() for p, t in pairs]),
            "rmse": mean([np.sqrt(((p - t) ** 2).mean()) for p, t in pairs]),
            "rung_accuracy": round(float(np.mean(
                [np.mean([to_class(a) == to_class(b) for a, b in zip(p, t)]) for p, t in pairs])), 4),
        }
    strengths = {}
    for st in (0.0, 0.25, 0.5, 0.75, 1.0):
        c = Config(**{**asdict(cfg), "demote_strength": st})
        strengths[str(st)] = mean([np.abs(p - t).mean() for p, t in run("knn_demote", c)])
    return {"holdout_frac": holdout, "repeats": repeats, "held_out_edges": int(holdout * oi.size),
            "models": models, "mae_by_demote_strength": strengths}


def build_complete(graph: RoadGraph, rows: list[dict], cfg: Config,
                   log=print, full_report: bool = True) -> tuple[list[dict], dict]:
    """Observed rows plus a predicted row for every other edge, and the report.
    full_report=False skips the hold-out evaluation and the class-step
    measurement, which take minutes and only change when the model does."""
    tab = edge_table(graph)
    index = {int(i): n for n, i in enumerate(tab["id"])}
    weight = np.zeros(tab["id"].size)
    observed = np.zeros(tab["id"].size, dtype=bool)
    by_id = {r["edge_id"]: r for r in rows}
    for r in rows:
        n = index.get(r["edge_id"])
        if n is not None and r["source"] == "observed":
            weight[n] = r["weight"]
            observed[n] = True
    log(f"  {int(observed.sum())} observed, {int((~observed).sum())} to predict")

    report = {"config": asdict(cfg), "observed_edges": int(observed.sum()),
              "total_edges": int(tab["id"].size)}
    if full_report:
        log("  measuring the demotion assumption on observed roads...")
        report["class_step"] = measure_class_step(tab, weight, observed)
        log("  hold-out evaluation...")
        report["evaluation"] = evaluate(tab, weight, observed, cfg)

    pred, method = predict(tab, weight, observed, cfg, model="knn_demote")
    drift = float(np.abs(pred[observed] - weight[observed]).max()) if observed.any() else 0.0
    assert drift == 0.0, f"imputation altered observed weights (max {drift})"
    report["observed_weights_unchanged"] = True

    out = []
    for n, eid in enumerate(tab["id"]):
        eid = int(eid)
        src = by_id.get(eid, {})
        w = float(pred[n])
        out.append({
            "slot_utc": src.get("slot_utc", ""), "edge_id": eid, "cls": to_class(w),
            "weight": round(w, 2),
            "f_green": src.get("f_green", 0.0), "f_orange": src.get("f_orange", 0.0),
            "f_red": src.get("f_red", 0.0), "f_darkred": src.get("f_darkred", 0.0),
            "coverage": src.get("coverage", 0.0), "vc_ratio": round(w / 100.0, 4),
            "n_samples": src.get("n_samples", 0),
            "source": "observed" if observed[n] else "predicted",
            "method": METHOD_NAMES[int(method[n])],
        })
    report["method_counts"] = {v: int((method == k).sum()) for k, v in METHOD_NAMES.items()}
    report["weights"] = {"observed": summary(pred[observed]), "predicted": summary(pred[~observed]),
                         "all": summary(pred)}
    return out, report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graph", type=Path, default=Path("output/graph/dhaka.json"))
    ap.add_argument("--observed", type=Path, default=Path("output/traffic/observed.csv"))
    ap.add_argument("--out", type=Path, default=Path("output/traffic/complete.csv"))
    ap.add_argument("--report", type=Path, default=Path("output/traffic/impute-report.json"))
    ap.add_argument("--geojson", action="store_true", help="also write predicted/observed/complete layers")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--max-dist-m", type=float, default=500.0)
    ap.add_argument("--decay-m", type=float, default=250.0)
    ap.add_argument("--zones", type=int, default=24)
    ap.add_argument("--no-demote", action="store_true")
    ap.add_argument("--demote-strength", type=float, default=1.0)
    ap.add_argument("--no-eval", action="store_true",
                    help="skip the hold-out evaluation and class-step measurement (fast)")
    args = ap.parse_args()

    cfg = Config(k=args.k, max_dist_m=args.max_dist_m, decay_m=args.decay_m, zones=args.zones,
                 demote_smaller=not args.no_demote, demote_strength=args.demote_strength)
    graph = RoadGraph.load(args.graph)
    gid = graph.graph_id()
    rows = read_csv(args.observed)
    src_meta = read_meta(args.observed)
    print(f"graph {len(graph.edges)} edges, id {gid}; observed file {len(rows)} rows")
    if src_meta.get("graph_id") and src_meta["graph_id"] != gid:
        print(f"  ERROR: {args.observed} was decoded against graph {src_meta['graph_id']}, "
              f"not {gid}. Re-run the decoder.")
        return 2
    out, report = build_complete(graph, rows, cfg, full_report=not args.no_eval)
    report["graph_id"] = gid
    write_csv(out, args.out, {"graph_id": gid, **{k: v for k, v in src_meta.items() if k != "rows"}})
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"  -> {args.out}\n  -> {args.report}")

    if "evaluation" in report:
        print("\n  hold-out (predicting hidden observed edges from the rest):")
        print(f"    {'model':14s} {'MAE':>7s} {'RMSE':>7s} {'rung acc':>9s}")
        for m, v in report["evaluation"]["models"].items():
            print(f"    {m:14s} {v['mae']:7.2f} {v['rmse']:7.2f} {v['rung_accuracy']:8.1%}")
        print("    MAE by demotion strength:",
              "  ".join(f"{k}={v}" for k, v in report["evaluation"]["mae_by_demote_strength"].items()))
    cs = report.get("class_step", {})
    if cs.get("pairs"):
        b = cs.get("beside_busy", {})
        print(f"\n  demotion assumption on {cs['pairs']} nearby observed pairs: "
              f"all pairs {cs['all_pairs']['mean_weight_change']:+.1f}, "
              f"beside a busy road {b.get('mean_weight_change', 0):+.1f} "
              f"({b.get('share_lower', 0):.0%} lower, {b.get('pairs', 0)} pairs)")
    print("  weights:", json.dumps(report["weights"]))

    if args.geojson:
        d = args.out.parent
        for src, name in (("predicted", "predicted.geojson"), ("observed", "observed-final.geojson")):
            n = write_geojson(out, graph, d / name, only_source=src)
            print(f"  {n} {src} edges -> {d / name}")
        n = write_geojson(out, graph, d / "complete.geojson")
        print(f"  {n} edges -> {d / 'complete.geojson'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
