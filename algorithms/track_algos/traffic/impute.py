"""Give every road in Dhaka a traffic weight, including the ones Google never paints.

    python -m track_algos.traffic.impute --graph output/graph/dhaka.json
        --observed output/traffic/observed.csv --out output/traffic/complete.csv
        [--geojson] [--report output/traffic/impute-report.json]

The problem
-----------
Google paints traffic on about 11 % of Dhaka's road length: nearly all of the
primary and trunk network, almost none of the residential streets. A router
that only knows those roads cannot do the one thing this project exists to do,
which is push traffic off a jammed arterial and onto the side streets beside
it. So the missing 89 % has to be filled in, and filled in honestly: every
edge records whether its weight was observed or predicted.

The model
---------
For an unobserved edge, take the k nearest observed edges, average their
weights with 1/distance weighting (ml/knn.py), and then ask whether this road
is smaller than the ones it is copying from. If it is, apply one step of the
demotion ladder in weights.py: beside a dark red arterial a quiet lane is red,
beside a red one it is yellow, beside a yellow one it is green. If the road is
the same size or larger than its neighbours, copy their level unchanged; there
is no reason to assume an unobserved primary is quieter than the primary next
to it.

How far a neighbour's word carries
----------------------------------
A jam does not stop at a fixed radius and it does not reach unchanged across a
neighbourhood either. The neighbour estimate is faded toward a regional
baseline (the mean of the edge's K-means zone, ml/kmeans.py) by
exp(-distance / `--decay-m`): right beside an observed road the neighbours
decide, a few hundred metres away the zone does, with a smooth gradient in
between. `--max-dist-m` is the hard stop where neighbours count for nothing.

Without the fade, one jammed arterial repainted every street within 500 m as
jammed, which is neither physically true nor useful to a router looking for a
way around it.

Checking it instead of asserting it
-----------------------------------
Two tests run alongside the prediction and land in the report:

`measure_class_step` uses only observed roads. Wherever a smaller observed road
sits near a bigger observed road, it measures the real difference in weight.
That is the demotion ladder's assumption, measured rather than assumed.

`evaluate` hides a random fraction of the observed edges, predicts them from
the rest, and compares against the truth, for several models at once: the
global mean, nearest neighbour, plain KNN, and KNN with demotion. It reports
mean absolute error in weight points and how often the predicted rung of the
ladder is the right one. Caveat worth remembering when reading it: the held-out
edges are mostly major roads, because those are the ones with data, so this
measures how well neighbours predict a major road. It cannot fully validate the
demotion step for residential streets, which never have data to hold out. That
is what `measure_class_step` is for.
"""

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass  # noqa: F401
from pathlib import Path

import numpy as np

from ..decode.decoder import read_csv, read_meta, write_csv, write_geojson
from ..geo import local_xy
from ..graph.road_graph import RoadGraph, class_rank
from ..ml.kmeans import KMeans
from ..ml.knn import KNNRegressor
from .weights import CEILING, FLOOR, demote, summary, to_class


@dataclass
class Config:
    k: int = 5                    # neighbours consulted per unobserved edge
    max_dist_m: float = 500.0     # beyond this, a neighbour says nothing useful
    decay_m: float = 250.0        # confidence in a neighbour halves roughly every decay_m
    zones: int = 24               # K-means zones for the far-from-everything fallback
    demote_smaller: bool = True   # apply the ladder when the road is smaller than its neighbours
    demote_strength: float = 1.0  # 0 = copy the neighbours, 1 = a full step down the ladder
    rank_margin: float = 0.5      # how much smaller it must be before demoting
    seed: int = 0


def edge_table(graph: RoadGraph) -> dict:
    """Flat arrays describing every edge: id, midpoint in local metres, class rank."""
    ids, lon, lat, rank, hw = [], [], [], [], []
    for e in graph.edges.values():
        g = e.geometry
        m = g[len(g) // 2] if len(g) > 2 else [(g[0][0] + g[-1][0]) / 2, (g[0][1] + g[-1][1]) / 2]
        ids.append(e.id); lon.append(m[0]); lat.append(m[1])
        rank.append(class_rank(e.highway)); hw.append(e.highway)
    lon = np.asarray(lon); lat = np.asarray(lat)
    x, y = local_xy(lon, lat, float(lon.mean()), float(lat.mean()))
    return {"id": np.asarray(ids, dtype=np.int64), "xy": np.column_stack([x, y]),
            "rank": np.asarray(rank, dtype=float), "highway": hw,
            "lon": lon, "lat": lat}


def _step_stats(v: np.ndarray) -> dict:
    return {"pairs": int(v.size), "mean_weight_change": round(float(v.mean()), 2),
            "median": round(float(np.median(v)), 2),
            "share_lower": round(float((v < -1).mean()), 3),
            "share_equal": round(float((np.abs(v) <= 1).mean()), 3),
            "share_higher": round(float((v > 1).mean()), 3)}


def measure_class_step(tab: dict, weight: np.ndarray, observed: np.ndarray,
                       max_dist_m: float = 300.0, k: int = 12,
                       busy_threshold: float = 55.0) -> dict:
    """Using observed roads only: how much lower is a smaller road's weight than
    a nearby bigger road's? The demotion ladder claims roughly 20-30 points.

    Two measurements, because the blunt one is nearly meaningless. Across all
    pairs most comparisons are green beside green, which says nothing about
    escaping a jam. The `beside_busy` measurement keeps only pairs where the
    BIGGER road is at least `busy_threshold` (yellow or worse), which is exactly
    the situation the ladder describes: a side street beside a congested road.

    Both are biased, and the report says so. Google paints a residential street
    only where it has enough probe traffic, so the small roads in this sample
    are the busy ones; the quiet lanes the ladder is really about are invisible
    here by construction."""
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
    """Predict a weight for every edge where `observed` is False.

    model: "global" mean of observed | "nearest" single nearest | "knn"
    distance-weighted mean | "knn_demote" the same plus the demotion ladder.
    Returns (predicted weight for every edge, method code per edge:
    0 observed, 1 neighbours, 2 neighbours+demoted, 3 zone fallback)."""
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
        """One rung of the ladder, scaled by demote_strength, where apply_to."""
        if not demoting:
            return w
        return np.where(apply_to, w + cfg.demote_strength * (demote(w) - w), w)

    w_nb = step_down(w_nb, smaller)

    # Regional baseline: the mean of the observed roads in this edge's K-means
    # zone. It is what we believe about a part of the city when no individual
    # road nearby has been measured.
    km = KMeans(k=min(cfg.zones, max(1, oi.size // 20)), seed=cfg.seed).fit(tab["xy"][oi])
    nz = km.centroids.shape[0]
    zone_mean = np.asarray([obs_w[km.labels_ == z].mean() if (km.labels_ == z).any() else obs_w.mean()
                            for z in range(nz)])
    zone_rank = np.asarray([tab["rank"][oi][km.labels_ == z].mean() if (km.labels_ == z).any()
                            else tab["rank"][oi].mean() for z in range(nz)])
    zi = np.argmin(((tab["xy"][ui][:, None, :] - km.centroids[None, :, :]) ** 2).sum(axis=2), axis=1)
    w_zone = step_down(zone_mean[zi], tab["rank"][ui] > zone_rank[zi] + cfg.rank_margin)

    # Fade from the neighbours to the zone with distance to the nearest measured road.
    d0 = d[:, 0]
    alpha = np.where(d0 <= cfg.max_dist_m, np.exp(-d0 / max(cfg.decay_m, 1.0)), 0.0)
    pred = alpha * w_nb + (1.0 - alpha) * w_zone

    meth = np.where(alpha < 0.5, 3, np.where(smaller & demoting, 2, 1)).astype(np.int8)
    out[ui] = np.clip(pred, FLOOR, CEILING)
    method[ui] = meth
    return out, method


def evaluate(tab: dict, weight: np.ndarray, observed: np.ndarray, cfg: Config,
             holdout: float = 0.2, repeats: int = 3) -> dict:
    """Hide a slice of the observed edges, predict them from the rest, score."""
    rng = np.random.default_rng(cfg.seed)
    oi = np.flatnonzero(observed)
    models = ["global", "nearest", "knn", "knn_demote"]
    acc = {m: {"mae": [], "rmse": [], "rung": []} for m in models}
    for _ in range(repeats):
        hide = rng.permutation(oi)[: max(1, int(holdout * oi.size))]
        mask = observed.copy()
        mask[hide] = False
        truth = weight[hide]
        for m in models:
            pred, _ = predict(tab, np.where(mask, weight, 0.0), mask, cfg, model=m)
            p = pred[hide]
            acc[m]["mae"].append(float(np.abs(p - truth).mean()))
            acc[m]["rmse"].append(float(np.sqrt(((p - truth) ** 2).mean())))
            acc[m]["rung"].append(float(np.mean([to_class(a) == to_class(b) for a, b in zip(p, truth)])))
    # what the demotion prior costs, as a dial rather than a switch
    strengths = {}
    for st in (0.0, 0.25, 0.5, 0.75, 1.0):
        c = Config(**{**asdict(cfg), "demote_strength": st})
        maes = []
        rng2 = np.random.default_rng(cfg.seed)
        for _ in range(repeats):
            hide = rng2.permutation(oi)[: max(1, int(holdout * oi.size))]
            mask = observed.copy()
            mask[hide] = False
            p, _ = predict(tab, np.where(mask, weight, 0.0), mask, c, model="knn_demote")
            maes.append(float(np.abs(p[hide] - weight[hide]).mean()))
        strengths[str(st)] = round(float(np.mean(maes)), 2)
    return {"holdout_frac": holdout, "repeats": repeats, "held_out_edges": int(holdout * oi.size),
            "models": {m: {"mae": round(float(np.mean(v["mae"])), 2),
                           "rmse": round(float(np.mean(v["rmse"])), 2),
                           "rung_accuracy": round(float(np.mean(v["rung"])), 4)} for m, v in acc.items()},
            "mae_by_demote_strength": strengths}


def build_complete(graph: RoadGraph, rows: list[dict], cfg: Config,
                   log=print) -> tuple[list[dict], dict]:
    """Observed rows + predicted rows for every remaining edge, plus a report."""
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
    log("  measuring the demotion assumption on observed roads...")
    report["class_step"] = measure_class_step(tab, weight, observed)
    log("  hold-out evaluation...")
    report["evaluation"] = evaluate(tab, weight, observed, cfg)

    pred, method = predict(tab, weight, observed, cfg, model="knn_demote")
    # The prediction fills gaps; it must never restate what Google measured.
    drift = float(np.abs(pred[observed] - weight[observed]).max()) if observed.any() else 0.0
    assert drift == 0.0, f"imputation altered {int(observed.sum())} observed weights (max {drift})"
    report["observed_weights_unchanged"] = True
    names = {0: "observed", 1: "neighbours", 2: "neighbours_demoted", 3: "zone_blended"}
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
            "method": names[int(method[n])],
        })
    report["method_counts"] = {v: int((method == k).sum()) for k, v in names.items()}
    report["weights"] = {"observed": summary(pred[observed]), "predicted": summary(pred[~observed]),
                         "all": summary(pred)}
    return out, report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graph", type=Path, default=Path("output/graph/dhaka.json"))
    ap.add_argument("--observed", type=Path, default=Path("output/traffic/observed.csv"))
    ap.add_argument("--out", type=Path, default=Path("output/traffic/complete.csv"))
    ap.add_argument("--report", type=Path, default=Path("output/traffic/impute-report.json"))
    ap.add_argument("--geojson", action="store_true", help="write predicted/observed layers for the visualizer")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--max-dist-m", type=float, default=500.0)
    ap.add_argument("--decay-m", type=float, default=250.0,
                    help="how fast a neighbour's influence fades with distance")
    ap.add_argument("--zones", type=int, default=24)
    ap.add_argument("--no-demote", action="store_true")
    ap.add_argument("--demote-strength", type=float, default=1.0,
                    help="0 copies the neighbours, 1 is a full step down the ladder")
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
    out, report = build_complete(graph, rows, cfg)
    report["graph_id"] = gid
    write_csv(out, args.out, {"graph_id": gid, **{k: v for k, v in src_meta.items() if k != "rows"}})
    print(f"  -> {args.out}")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"  -> {args.report}")

    ev = report["evaluation"]["models"]
    print("\n  hold-out (predicting held-out OBSERVED edges from the rest):")
    print(f"    {'model':14s} {'MAE':>7s} {'RMSE':>7s} {'rung acc':>9s}")
    for m, v in ev.items():
        print(f"    {m:14s} {v['mae']:7.2f} {v['rmse']:7.2f} {v['rung_accuracy']:8.1%}")
    print("    MAE by demotion strength:",
          "  ".join(f"{k}={v}" for k, v in report["evaluation"]["mae_by_demote_strength"].items()))
    cs = report["class_step"]
    if cs.get("pairs"):
        print(f"\n  demotion assumption, measured on {cs['pairs']} nearby observed road pairs:")
        print(f"    {'':>20s} {'pairs':>7s} {'mean':>7s} {'lower':>7s} {'equal':>7s} {'higher':>7s}")
        for dr, v in cs["by_rank_delta"].items():
            lbl = f"{dr} class(es) smaller"
            print(f"    {lbl:>20s} {v['pairs']:7d} {v['mean_weight_change']:+7.1f} "
                  f"{v['share_lower']:7.0%} {v['share_equal']:7.0%} {v['share_higher']:7.0%}")
        b = cs.get("beside_busy", {})
        if b.get("pairs"):
            print(f"    {'beside a BUSY road':>20s} {b['pairs']:7d} {b['mean_weight_change']:+7.1f} "
                  f"{b['share_lower']:7.0%} {b['share_equal']:7.0%} {b['share_higher']:7.0%}"
                  "  <- the case the ladder is about")
    print("\n  weights:", json.dumps(report["weights"], indent=1))

    if args.geojson:
        d = args.out.parent
        for src, name in (("predicted", "predicted.geojson"), ("observed", "observed-final.geojson")):
            n = write_geojson(out, graph, d / name, only_source=src)
            print(f"  {n} {src} edges -> {d / name}")
        n = write_geojson(out, graph, d / "complete.geojson")
        print(f"  {n} edges (all) -> {d / 'complete.geojson'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
