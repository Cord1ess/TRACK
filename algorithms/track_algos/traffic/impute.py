"""Give every road a traffic weight, including the ones Google never paints.

    python -m track_algos.traffic.impute --graph output/graph/dhaka.json
        --observed output/traffic/observed.csv --out output/traffic/complete.csv

Google paints about a tenth of Dhaka's road length. Every road ends up at one
of the four colours Google paints and never between them: an observed road
keeps the colour it was painted, and the rest take the colour of the nearest
observed road along the network: a road touching it gets the same colour, and
every --hops-per-rung junctions after that it is one rung milder (dark red,
red, orange, green). Equally near observed roads of different colours: the
worse colour wins, so the answer never depends on the order roads are listed
in. A road with no path to any observed road is green, because a road with no
evidence against it is one worth sending traffic down. Observed weights are
never changed.

Averaging produced 773 different weights across the city, values like 29.74
that no colour on the map corresponds to. Spreading through open space instead
of along the network made a road a neighbour of one across a river.

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
from ..ml.knn import KNNRegressor
from .weights import CEILING, FLOOR, LADDER, demote, summary, to_class

METHOD_NAMES = {0: "observed", 1: "adjacent", 2: "spread", 3: "assumed_clear"}

# Every weight in the output is one of these four. Nothing lands between them:
# a road is a colour Google could have painted, or it is assumed clear.
_LADDER_ARR = np.asarray(LADDER, dtype=float)


def _rung_index(w) -> np.ndarray:
    """Nearest rung of the ladder, as 0..3."""
    w = np.asarray(w, dtype=float)
    return np.abs(w[:, None] - _LADDER_ARR[None, :]).argmin(axis=1).astype(np.int16)


def _snap(w) -> np.ndarray:
    """Snap weights onto the ladder."""
    return _LADDER_ARR[_rung_index(w)]


@dataclass
class Config:
    k: int = 5                    # neighbours per unobserved edge
    max_dist_m: float = 500.0     # beyond this a neighbour counts for nothing
    # Kept so old commands and saved configs still run. The spread works in
    # junctions now, not metres, so neither of these changes the result.
    decay_m: float = 250.0
    hops_per_rung: float = 1.0    # after the touching roads, junctions per rung milder
    claim: str = "nearest"        # "nearest": the closest observed road decides.
                                  # "worst": the most congested one within reach
                                  # decides, which never hides a jam but
                                  # over-states congestion: 34% of observed roads
                                  # are calmer than their worst neighbour.
    zones: int = 24               # unused here; layers.py still draws K-means zones
    # Off by default: it makes a small street beside a red road orange, where
    # the rule is that touching roads share the colour. On/off only, since the
    # ladder has no half rungs; any strength above 0 is a full step.
    demote_smaller: bool = True
    demote_strength: float = 0.0
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

    u = np.asarray([e.u for e in graph.edges.values()], dtype=np.int64)
    v = np.asarray([e.v for e in graph.edges.values()], dtype=np.int64)
    return {"id": np.asarray(ids, dtype=np.int64), "xy": np.column_stack([x, y]),
            "rank": np.asarray(rank, dtype=float), "highway": hw, "lon": lon, "lat": lat,
            "adj": _adjacency(u, v)}


def _adjacency(u, v) -> tuple[np.ndarray, np.ndarray]:
    """For every edge, the edges sharing a junction with it, as arrays (ptr,
    idx): edge i's neighbours are idx[ptr[i]:ptr[i + 1]].

    Neighbours are the roads a road actually connects to. Distance on a map
    would make a road on the far side of a river a neighbour."""
    u = np.asarray(u, dtype=np.int64)
    v = np.asarray(v, dtype=np.int64)
    n = u.size
    ends = np.concatenate([u, v])
    eid = np.concatenate([np.arange(n), np.arange(n)])
    order = np.lexsort((eid, ends))
    ends, eid = ends[order], eid[order]
    cut = np.flatnonzero(np.r_[True, ends[1:] != ends[:-1], True])
    ii, jj = [], []
    for a, b in zip(cut[:-1], cut[1:]):
        es = eid[a:b]
        if es.size < 2:
            continue
        x, y = np.repeat(es, es.size), np.tile(es, es.size)
        keep = x != y
        ii.append(x[keep])
        jj.append(y[keep])
    ptr = np.zeros(n + 1, dtype=np.int64)
    if not ii:
        return ptr, np.zeros(0, dtype=np.int64)
    key = np.unique(np.concatenate(ii) * n + np.concatenate(jj))  # two roads meeting twice count once
    np.cumsum(np.bincount(key // n, minlength=n), out=ptr[1:])
    return ptr, key % n


def _adjacency_of(tab: dict) -> tuple[np.ndarray, np.ndarray]:
    """The table's adjacency, or one built from a plain list of neighbour lists."""
    if "adj" in tab:
        return tab["adj"]
    nbrs = tab["nbrs"]
    ptr = np.zeros(len(nbrs) + 1, dtype=np.int64)
    np.cumsum([len(x) for x in nbrs], out=ptr[1:])
    idx = np.fromiter((j for x in nbrs for j in x), dtype=np.int64, count=int(ptr[-1]))
    return ptr, idx


def _bfs(ptr: np.ndarray, idx: np.ndarray, seeds) -> np.ndarray:
    """Junctions crossed from the nearest seed to every edge; -1 with no path."""
    n = ptr.size - 1
    dist = np.full(n, -1, dtype=np.int32)
    front = np.unique(np.asarray(seeds, dtype=np.int64))
    if front.size == 0:
        return dist
    dist[front] = 0
    d = 0
    while front.size:
        d += 1
        start = ptr[front]
        cnt = ptr[front + 1] - start
        total = int(cnt.sum())
        if total == 0:
            break
        pos = np.repeat(start - np.concatenate(([0], np.cumsum(cnt)[:-1])), cnt) + np.arange(total)
        nb = idx[pos]
        nb = np.unique(nb[dist[nb] < 0])
        dist[nb] = d
        front = nb
    return dist


def _rungs_lost(d, per_rung: int) -> np.ndarray:
    """How many rungs milder than its source a road is, d junctions away: none
    for the road itself or one touching it, then one every per_rung junctions."""
    d = np.asarray(d, dtype=np.int64)
    return np.where(d <= 1, 0, (d - 1 + per_rung - 1) // per_rung)


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
            model: str = "spread", details: dict | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Predict every edge where observed is False.

    model "spread" is the model; "global" is a baseline that gives the whole
    city one rung. The older names "nearest", "knn" and "knn_demote" still work
    and mean "spread", the last taking the smaller-road step when cfg has it on.

    Returns (weights, method per edge: 0 observed, 1 adjacent, 2 spread, 3
    assumed clear). A `details` dict also gets `hops`: junctions to the
    observed road the colour came from, 0 when observed, -1 when none.
    """
    n = weight.size
    out = weight.astype(float).copy()
    method = np.zeros(n, dtype=np.int8)
    hops = np.zeros(n, dtype=np.int32)
    oi = np.flatnonzero(observed)
    ui = np.flatnonzero(~observed)

    def done():
        if details is not None:
            details["hops"] = hops
        return np.clip(out, FLOOR, CEILING), method

    if ui.size == 0:
        return done()
    if oi.size == 0:
        # Nothing observed at all (a blank capture). Every road is assumed
        # clear; returning the input left every weight at 0, off the ladder,
        # and labelled every road "observed".
        out[ui], method[ui], hops[ui] = FLOOR, 3, -1
        return done()
    if model == "global":
        out[ui] = _snap(np.full(ui.size, float(weight[oi].mean())))
        method[ui], hops[ui] = 2, -1
        return done()

    ptr, idx = _adjacency_of(tab)
    rung = _rung_index(weight)
    per_rung = max(1, int(round(cfg.hops_per_rung)))
    big = np.iinfo(np.int32).max

    # Junctions from the nearest observed road of each colour: row r is rung r.
    # Four exact searches replace one walk that let whichever road came first in
    # the list claim a tie, and in which a green road claimed nothing at all, so
    # a jam four junctions away could colour a road touching a clear one.
    D = np.stack([_bfs(ptr, idx, oi[rung[oi] == r]) for r in range(4)])
    reach = D >= 0
    unreached = ~reach.any(axis=0)
    Dm = np.where(reach, D, big)
    rungs = np.arange(4, dtype=np.int64)[:, None]

    if str(cfg.claim).lower() == "worst":
        cand = np.where(reach, np.maximum(rungs - _rungs_lost(D, per_rung), 0), -1)
        level = cand.max(axis=0)
        dist = np.where(cand == level[None, :], Dm, big).min(axis=0)
    else:
        dist = Dm.min(axis=0)
        # equally near observed roads of different colours: the worse one wins
        best = np.where(Dm == dist[None, :], rungs, -1).max(axis=0)
        level = best - _rungs_lost(dist, per_rung)
    level = np.clip(level, 0, 3)

    demote_on = (model not in ("nearest", "knn") and cfg.demote_smaller
                 and cfg.demote_strength > 0)
    if demote_on:
        # a road smaller than the roads it meets is one rung milder
        cnt = np.diff(ptr)
        tot = np.bincount(np.repeat(np.arange(n), cnt), weights=tab["rank"][idx], minlength=n)
        r_nb = np.where(cnt > 0, tot / np.maximum(cnt, 1), tab["rank"])
        smaller = tab["rank"] > r_nb + cfg.rank_margin
        level = np.where(smaller, np.maximum(level - 1, 0), level)

    pred = np.where(unreached, FLOOR, _LADDER_ARR[level])
    out[ui] = pred[ui]
    hops = np.where(unreached, -1, dist).astype(np.int32)
    hops[oi] = 0
    method[ui] = np.where(hops[ui] == 1, 1, 2)
    method[ui[unreached[ui]]] = 3
    return done()


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
    base = asdict(cfg)
    variants = {
        "global": ("global", cfg),
        "spread": ("spread", Config(**{**base, "demote_strength": 0.0})),
        "spread_demote": ("spread", Config(**{**base, "demote_smaller": True, "demote_strength": 1.0})),
        "spread_worst": ("spread", Config(**{**base, "demote_strength": 0.0, "claim": "worst"})),
    }
    models = {}
    for name, (m, c) in variants.items():
        pairs = run(m, c)
        models[name] = {
            "mae": mean([np.abs(p - t).mean() for p, t in pairs]),
            "rmse": mean([np.sqrt(((p - t) ** 2).mean()) for p, t in pairs]),
            "rung_accuracy": round(float(np.mean(
                [np.mean(_rung_index(p) == _rung_index(t)) for p, t in pairs])), 4),
            # predicted milder than Google painted it: the error that sends
            # traffic into a jam
            "too_green": round(float(np.mean(
                [np.mean(_rung_index(p) < _rung_index(t)) for p, t in pairs])), 4),
        }
    return {"holdout_frac": holdout, "repeats": repeats, "held_out_edges": int(holdout * oi.size),
            "models": models}


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

    det = {}
    pred, method = predict(tab, weight, observed, cfg, model="spread", details=det)
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
            "hops": int(det["hops"][n]),
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
    ap.add_argument("--hops-per-rung", type=float, default=1.0,
                    help="after the roads touching an observed road, junctions per rung milder")
    ap.add_argument("--claim", choices=("nearest", "worst"), default="nearest",
                    help="nearest: the closest observed road wins (more accurate); "
                         "worst: the most congested within reach wins (never hides a jam)")
    ap.add_argument("--zones", type=int, default=24)
    ap.add_argument("--no-demote", action="store_true")
    ap.add_argument("--demote-strength", type=float, default=0.0,
                    help="above 0: a road smaller than the roads it meets is one rung milder")
    ap.add_argument("--no-eval", action="store_true",
                    help="skip the hold-out evaluation and class-step measurement (fast)")
    args = ap.parse_args()

    cfg = Config(k=args.k, max_dist_m=args.max_dist_m, decay_m=args.decay_m, zones=args.zones,
                 hops_per_rung=args.hops_per_rung, claim=args.claim,
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
        print(f"    {'model':14s} {'MAE':>7s} {'RMSE':>7s} {'rung acc':>9s} {'too green':>10s}")
        for m, v in report["evaluation"]["models"].items():
            print(f"    {m:14s} {v['mae']:7.2f} {v['rmse']:7.2f} {v['rung_accuracy']:8.1%} "
                  f"{v['too_green']:9.1%}")
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
