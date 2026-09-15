"""Run every algorithm on the current data and write one map layer for each.

    python -m track_algos.layers --graph output/graph/dhaka.json
        --weights output/traffic/complete.csv --out output/layers

Each layer is a GeoJSON file and an entry in index.json: a name, one line on
what it shows, a legend and the numbers behind it. The visualizer lists them.
"""

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

from .decode.decoder import read_csv, read_meta
from .geo import EARTH_R, haversine_m
from .graph.build_graph import DHAKA
from .graph.road_graph import RoadGraph
from .ml.kmeans import KMeans
from .ml.knn import KNN, KNNRegressor, standardize
from .ml.logistic_regression import OneVsRest
from .search.astar import astar, haversine_heuristic
from .search.dijkstra import dijkstra
from .traffic.assignment import Assignment
from .traffic.demand import gravity_matrix, sample_od_pairs, zones_from_grid
from .traffic.impute import Config, edge_table, predict
from .traffic.weights import to_class, to_hex, travel_time_s

RAMP = {1: "#16e098", 2: "#ffcf43", 3: "#d1352b", 4: "#a92727"}
CATEGORICAL = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b",
               "#e377c2", "#7f7f7f", "#bcbd22", "#17becf", "#393b79", "#ad494a"]
PLACES = {   # lon, lat
    "Uttara": (90.3995, 23.8759), "Motijheel": (90.4177, 23.7330), "Mirpur 10": (90.3685, 23.8069),
    "Gulshan 1": (90.4167, 23.7806), "Dhanmondi 27": (90.3745, 23.7563), "Airport": (90.3978, 23.8433),
    "Mohammadpur": (90.3593, 23.7660), "Bashundhara": (90.4300, 23.8150), "Sayedabad": (90.4260, 23.7182),
    "Shahbagh": (90.3963, 23.7389),
}
ROUTES = [("Uttara", "Motijheel"), ("Mirpur 10", "Gulshan 1"), ("Dhanmondi 27", "Airport"),
          ("Mohammadpur", "Bashundhara"), ("Sayedabad", "Mirpur 10"), ("Gulshan 1", "Dhanmondi 27")]


# ---------------------------------------------------------------- helpers

def feature(kind: str, coords, **props) -> dict:
    return {"type": "Feature", "properties": props, "geometry": {"type": kind, "coordinates": coords}}


def pairs(graph: RoadGraph):
    """One edge per two-way pair, so a street is drawn once."""
    seen = set()
    for e in graph.edges.values():
        key = (min(e.u, e.v), max(e.u, e.v), len(e.geometry))
        if key not in seen:
            seen.add(key)
            yield e


def band(value: float, bounds: list, colours: list) -> str:
    """Colour for the first band whose upper bound is above value."""
    for b, c in zip(bounds, colours):
        if value <= b:
            return c
    return colours[-1]


def write(out: Path, name: str, feats: list, **extra) -> int:
    out.mkdir(parents=True, exist_ok=True)
    gj = {"type": "FeatureCollection", "features": feats, **extra}
    (out / f"{name}.geojson").write_text(json.dumps(gj), encoding="utf-8")
    return len(feats)


# ---------------------------------------------------------------- the layers

def decoder_coverage(graph, rows):
    """Observed edges by how much of each one Google painted."""
    bounds, colours = [0.5, 0.75, 1.0], ["#bfdbfe", "#3b82f6", "#1e3a8a"]
    feats = [feature("LineString", graph.edges[r["edge_id"]].geometry,
                     color=band(r["coverage"], bounds, colours), coverage=r["coverage"],
                     weight=r["weight"], width=2.5)
             for r in rows if r["source"] == "observed" and r["edge_id"] in graph.edges]
    cov = [r["coverage"] for r in rows if r["source"] == "observed"]
    return feats, {
        "name": "Decoder: coverage", "algorithm": "decoder",
        "description": "Roads where the decoder found Google's traffic line, shaded by how much "
                       "of the road it covered. Below 25 % a road counts as not observed.",
        "legend": [{"color": colours[0], "label": "25-50 %"}, {"color": colours[1], "label": "50-75 %"},
                   {"color": colours[2], "label": "75-100 %"}],
        "summary": f"{len(feats):,} roads observed, mean coverage {100 * np.mean(cov):.0f} %",
        "stats": {"observed": len(feats), "mean_coverage": round(float(np.mean(cov)), 3)}}


def impute_method(graph, by_id):
    """Every road by how its weight was decided."""
    colours = {"observed": "#374151", "neighbours": "#2563eb",
               "neighbours_demoted": "#7c3aed", "zone_blended": "#cbd5e1"}
    labels = {"observed": "read from the tiles", "neighbours": "copied from nearby roads",
              "neighbours_demoted": "nearby roads, one level quieter", "zone_blended": "zone average"}
    counts = {k: 0 for k in colours}
    feats = []
    for e in pairs(graph):
        m = by_id.get(e.id, {}).get("method", "zone_blended")
        counts[m] = counts.get(m, 0) + 1
        feats.append(feature("LineString", e.geometry, color=colours.get(m, "#cbd5e1"),
                             width=3 if m == "observed" else 1.5, method=m))
    return feats, {
        "name": "Imputation: method", "algorithm": "impute",
        "description": "How each road got its weight. Google paints about a tenth of the network; "
                       "the rest is predicted from the nearest observed roads, one level quieter when "
                       "the road is smaller, fading to a zone average far from any data.",
        "legend": [{"color": colours[k], "label": labels[k]} for k in colours],
        "summary": ", ".join(f"{counts[k]:,} {labels[k]}" for k in colours),
        "stats": counts}


def kmeans_zones(graph, tab, weight, observed):
    """The K-means zones the imputer falls back to."""
    oi = np.flatnonzero(observed)
    km = KMeans(k=min(24, max(1, oi.size // 20)), seed=0).fit(tab["xy"][oi])
    lon0, lat0 = float(tab["lon"].mean()), float(tab["lat"].mean())
    kx = math.cos(math.radians(lat0)) * (math.pi / 180.0) * EARTH_R
    ky = (math.pi / 180.0) * EARTH_R
    feats = [feature("LineString", graph.edges[int(tab["id"][i])].geometry,
                     color=CATEGORICAL[int(z) % len(CATEGORICAL)], zone=int(z), width=2.5)
             for i, z in zip(oi, km.labels_)]
    zones = []
    for z, (x, y) in enumerate(km.centroids):
        members = km.labels_ == z
        mean_w = float(weight[oi][members].mean()) if members.any() else 0.0
        zones.append({"zone": z, "roads": int(members.sum()), "mean_weight": round(mean_w, 1)})
        feats.append(feature("Point", [lon0 + x / kx, lat0 + y / ky],
                             color=CATEGORICAL[z % len(CATEGORICAL)], zone=z,
                             roads=int(members.sum()), mean_weight=round(mean_w, 1)))
    busiest = max(zones, key=lambda q: q["mean_weight"])
    return feats, {
        "name": "K-means: zones", "algorithm": "kmeans",
        "description": "Observed roads grouped into zones by position with K-means. A road far from "
                       "any observed road takes its zone's average weight. Dots are zone centres.",
        "legend": [{"color": "#1f77b4", "label": "one colour per zone"}, {"color": "#fff", "label": "dot: zone centre"}],
        "summary": f"{len(zones)} zones; busiest zone averages weight {busiest['mean_weight']} "
                   f"over {busiest['roads']} roads",
        "stats": {"zones": zones}}


def knn_holdout(graph, tab, weight, observed, cfg):
    """Hidden observed roads, coloured by how far the prediction was from the truth."""
    rng = np.random.default_rng(0)
    oi = np.flatnonzero(observed)
    hide = rng.permutation(oi)[: max(1, int(0.2 * oi.size))]
    mask = observed.copy()
    mask[hide] = False
    pred, _ = predict(tab, np.where(mask, weight, 0.0), mask, cfg, model="knn_demote")
    err = np.abs(pred[hide] - weight[hide])
    bounds, colours = [5.0, 15.0, 1e9], ["#93c5fd", "#3b82f6", "#1e3a8a"]
    feats = [feature("LineString", graph.edges[int(tab["id"][i])].geometry,
                     color=band(float(e), bounds, colours), error=round(float(e), 1),
                     truth=round(float(weight[i]), 1), predicted=round(float(pred[i]), 1), width=3)
             for i, e in zip(hide, err)]
    rung = float(np.mean([to_class(a) == to_class(b) for a, b in zip(pred[hide], weight[hide])]))
    return feats, {
        "name": "KNN: hold-out test", "algorithm": "knn",
        "description": "A fifth of the observed roads were hidden and predicted from the rest with "
                       "K nearest neighbours. Colour is the size of the error in weight points "
                       "(green to dark red spans 80).",
        "legend": [{"color": colours[0], "label": "within 5"}, {"color": colours[1], "label": "5 to 15"},
                   {"color": colours[2], "label": "over 15"}],
        "summary": f"{hide.size:,} roads hidden: mean error {err.mean():.1f} points, "
                   f"right traffic level {100 * rung:.0f} % of the time",
        "stats": {"hidden": int(hide.size), "mae": round(float(err.mean()), 2),
                  "rung_accuracy": round(rung, 4)}}


def lr_classes(graph, tab, weight, observed):
    """Logistic regression predicts the traffic level of every road; compared with KNN."""
    oi = np.flatnonzero(observed)
    reg = KNNRegressor(k=6).fit(tab["xy"][oi], weight[oi])
    d, nb = reg.kneighbors(tab["xy"])
    # a road's own reading must not leak into its features: drop a zero-distance neighbour
    first = (d[:, 0] == 0).astype(int)[:, None]
    cols = first + np.arange(5)[None, :]
    nb_mean = np.take_along_axis(weight[oi][nb], cols, axis=1).mean(axis=1)
    X = np.column_stack([tab["xy"], tab["rank"], nb_mean])
    y = np.asarray([to_class(w) for w in weight])

    rng = np.random.default_rng(0)
    perm = rng.permutation(oi)
    test, train = perm[: max(1, oi.size // 5)], perm[max(1, oi.size // 5):]
    Xs, mean, std = standardize(X[train])
    Xt = (X[test] - mean) / std
    lr = OneVsRest(classes=[1, 2, 3, 4], lr=0.5, epochs=400).fit(Xs, y[train])
    knn = KNN(k=7).fit(Xs, y[train])
    lr_acc, knn_acc = lr.score(Xt, y[test]), knn.score(Xt, y[test])

    Xs, mean, std = standardize(X[oi])
    lr = OneVsRest(classes=[1, 2, 3, 4], lr=0.5, epochs=400).fit(Xs, y[oi])
    cls = lr.predict((X - mean) / std)
    index = {int(i): n for n, i in enumerate(tab["id"])}
    feats = [feature("LineString", e.geometry, cls=int(cls[index[e.id]]), width=2)
             for e in pairs(graph) if e.id in index]
    return feats, {
        "name": "Logistic regression: traffic level", "algorithm": "logistic_regression",
        "description": "A logistic regression (one model per level) predicts each road's traffic "
                       "level from its position, class and the weight of the roads around it. "
                       "Trained on the observed roads, drawn for every road.",
        "legend": [{"color": RAMP[1], "label": "green"}, {"color": RAMP[2], "label": "yellow"},
                   {"color": RAMP[3], "label": "red"}, {"color": RAMP[4], "label": "dark red"}],
        "summary": f"hold-out accuracy {100 * lr_acc:.0f} % (KNN on the same features: {100 * knn_acc:.0f} %)",
        "stats": {"lr_accuracy": round(lr_acc, 4), "knn_accuracy": round(knn_acc, 4),
                  "test_roads": int(test.size)}}


def dijkstra_reach(graph, weight_of, hub="Shahbagh"):
    """Travel time from one point to every road on today's traffic."""
    start = graph.nearest_node(*PLACES[hub])
    cost = lambda e: travel_time_s(e.free_flow_s, weight_of.get(e.id, 25.0))
    dist, _ = dijkstra(graph, start, cost)
    bounds = [10, 20, 30, 45, 1e9]
    colours = ["#a7f3d0", "#34d399", "#0ea5e9", "#6366f1", "#4c1d95"]
    feats, mins = [], []
    for e in pairs(graph):
        if e.v not in dist:
            continue
        m = dist[e.v] / 60.0
        mins.append(m)
        feats.append(feature("LineString", e.geometry, color=band(m, bounds, colours),
                             minutes=round(m, 1), width=1.8))
    feats.append(feature("Point", list(PLACES[hub]), color="#111827", name=hub))
    mins = np.asarray(mins)
    return feats, {
        "name": f"Dijkstra: minutes from {hub}", "algorithm": "dijkstra",
        "description": f"Least travel time from {hub} to every road, using today's congested "
                       "travel times. One run of Dijkstra reaches the whole city.",
        "legend": [{"color": colours[0], "label": "under 10 min"}, {"color": colours[1], "label": "10-20"},
                   {"color": colours[2], "label": "20-30"}, {"color": colours[3], "label": "30-45"},
                   {"color": colours[4], "label": "over 45"}],
        "summary": f"{len(dist):,} junctions reached; median {np.median(mins):.0f} min, "
                   f"{100 * float((mins <= 30).mean()):.0f} % of roads within 30 min",
        "stats": {"reached": len(dist), "median_min": round(float(np.median(mins)), 1)}}


def astar_routes(graph, weight_of):
    """Fixed routes across the city, on free-flow and on congested costs."""
    h = haversine_heuristic(graph)
    congested = lambda e: travel_time_s(e.free_flow_s, weight_of.get(e.id, 25.0))
    free = lambda e: e.free_flow_s
    nodes = {name: graph.nearest_node(*ll) for name, ll in PLACES.items()}
    feats, legs = [], []
    for a, b in ROUTES:
        for mode, fn in (("free_flow", free), ("congested", congested)):
            _, edges, secs = astar(graph, nodes[a], nodes[b], fn, h)
            if not edges:
                continue
            coords, ws = [], []
            for eid in edges:
                e = graph.edges[eid]
                coords.extend(e.geometry if not coords else e.geometry[1:])
                ws.append(weight_of.get(eid, 25.0))
            km = sum(graph.edges[eid].length_m for eid in edges) / 1000.0
            colour = "#9ca3af" if mode == "free_flow" else to_hex(float(np.mean(ws)))
            feats.append(feature("LineString", coords, color=colour, width=3 if mode == "free_flow" else 5,
                                 route=f"{a} to {b}", mode=mode, minutes=round(secs / 60.0, 1),
                                 km=round(km, 1)))
            legs.append({"route": f"{a} to {b}", "mode": mode, "minutes": round(secs / 60.0, 1),
                         "km": round(km, 1)})
    for name in {p for r in ROUTES for p in r}:
        feats.append(feature("Point", list(PLACES[name]), color="#111827", name=name))
    slow = [l for l in legs if l["mode"] == "congested"]
    fast = {l["route"]: l for l in legs if l["mode"] == "free_flow"}
    extra = np.mean([l["minutes"] - fast[l["route"]]["minutes"] for l in slow]) if slow else 0.0
    typical = np.mean([l["minutes"] for l in slow]) if slow else 0.0
    return feats, {
        "name": "A*: routes", "algorithm": "astar",
        "description": "Six trips across Dhaka routed twice with A*: the grey line is the fastest "
                       "route on an empty network, the coloured one the fastest on today's "
                       "traffic, coloured by the traffic it drives through.",
        "legend": [{"color": "#9ca3af", "label": "free flow"}, {"color": RAMP[1], "label": "congested, light"},
                   {"color": RAMP[3], "label": "congested, heavy"}],
        "summary": f"{len(ROUTES)} routes, {typical:.0f} min each on average; today's traffic adds {extra:.1f} min",
        "stats": {"legs": legs}}


def make_demand(graph, tab, weight, observed):
    """Zones over the city and a gravity matrix between them."""
    lon, lat = tab["lon"][observed], tab["lat"][observed]
    w = weight[observed]

    def busy(zlon, zlat):
        near = (np.abs(lon - zlon) < 0.015) & (np.abs(lat - zlat) < 0.015)
        return 1.0 + max(0.0, (float(w[near].mean()) - 25.0) / 40.0) if near.any() else 1.0

    zones = zones_from_grid(graph, DHAKA, 6, 6, weight_fn=busy)
    return zones, gravity_matrix(zones, gamma=1.5)


def demand_lines(zones, matrix, top=60):
    """The busiest zone-to-zone flows as straight lines."""
    by_id = {z["id"]: z for z in zones}
    flows = sorted(matrix.items(), key=lambda kv: -kv[1])[:top]
    tmax = flows[0][1] if flows else 1.0
    feats = [feature("LineString", [[by_id[a]["lon"], by_id[a]["lat"]], [by_id[b]["lon"], by_id[b]["lat"]]],
                     color="#f59e0b", width=round(1.5 + 6.0 * t / tmax, 1), trips=round(t, 2),
                     from_zone=a, to_zone=b)
             for (a, b), t in flows]
    feats += [feature("Point", [z["lon"], z["lat"]], color="#b45309", zone=z["id"],
                      weight=round(z["weight"], 2)) for z in zones]
    total = sum(matrix.values())
    return feats, {
        "name": "Gravity model: demand", "algorithm": "demand",
        "description": "Trips between 36 zones from a gravity model: busier zones make and attract "
                       "more trips, distance suppresses them. Zone weight comes from the observed "
                       "traffic around it. The strongest flows are drawn; width is trips.",
        "legend": [{"color": "#f59e0b", "label": "flow, width = trips"}, {"color": "#b45309", "label": "zone"}],
        "summary": f"{len(zones)} zones, top {len(flows)} flows carry "
                   f"{100 * sum(t for _, t in flows) / total:.0f} % of all trips",
        "stats": {"zones": len(zones), "flows_drawn": len(flows)}}


def assignment_frames(graph, weight_of, zones, matrix, trips=300, per_trip=40.0):
    """New trips on top of today's traffic: before, all at once, and incremental."""
    base = {e.id: weight_of.get(e.id, 25.0) / 100.0 * e.capacity_vph for e in graph.edges.values()}
    by_id = {z["id"]: z for z in zones}
    od = [(by_id[a]["node"], by_id[b]["node"]) for a, b in sample_od_pairs(matrix, trips, seed=0)]
    t0 = time.time()
    naive = Assignment(graph, base_volume=base).all_at_once(od, per_trip)
    a = Assignment(graph, base_volume=base)
    frames = a.incremental(od, batches=10, vehicles_per_trip=per_trip)
    before, after = frames[0], frames[-1]
    secs = time.time() - t0
    changed = [eid for eid in graph.edges
               if after["vc"][eid] != before["vc"][eid] or naive["vc"][eid] != before["vc"][eid]]

    def frame_feats(frame):
        out = []
        for eid in changed:
            vc = frame["vc"][eid]
            cls = 1 if vc < 0.45 else 2 if vc < 0.8 else 3 if vc < 1.1 else 4
            out.append(feature("LineString", graph.edges[eid].geometry, cls=cls, vc=vc,
                               volume=round(frame["volume"].get(eid, 0.0), 1), width=4))
        return out

    legend = [{"color": RAMP[1], "label": "under capacity"}, {"color": RAMP[2], "label": "filling"},
              {"color": RAMP[3], "label": "near capacity"}, {"color": RAMP[4], "label": "over capacity"}]
    sb, sn, sa = before["stats"], naive["stats"], after["stats"]
    desc = (f"{trips} new trips ({trips * per_trip:,.0f} vehicles) added to today's traffic. "
            "Only the roads the new trips use are drawn. ")
    return [
        (frame_feats(before), {
            "name": "Assignment: before", "algorithm": "assignment",
            "description": desc + "This is the load before the new trips.",
            "legend": legend,
            "summary": f"{sb['over_capacity']:,} roads over capacity before the new trips",
            "stats": {"before": sb, "roads_drawn": len(changed)}}),
        (frame_feats(naive), {
            "name": "Assignment: all at once", "algorithm": "assignment",
            "description": desc + "Every trip takes the shortest path on the network as it was, "
                                  "with no feedback. This is what happens without assignment.",
            "legend": legend,
            "summary": f"{sn['over_capacity']:,} roads over capacity when every trip takes "
                       "the shortest path at once",
            "stats": {"all_at_once": sn, "roads_drawn": len(changed)}}),
        (frame_feats(after), {
            "name": "Assignment: incremental", "algorithm": "assignment",
            "description": desc + "Trips are routed with A* in ten batches; each batch sees the load "
                                  "the earlier ones added and goes around it.",
            "legend": legend,
            "summary": f"{sa['over_capacity']:,} roads over capacity with incremental assignment "
                       f"({a.searches + trips:,} A* searches in {secs:.0f} s)",
            "stats": {"incremental": sa, "searches": a.searches + trips, "roads_drawn": len(changed)}}),
    ]


# ---------------------------------------------------------------- main

def build_all(graph: RoadGraph, weights_csv: Path, out: Path, log=print) -> dict:
    t0 = time.time()
    rows = read_csv(weights_csv)
    by_id = {r["edge_id"]: r for r in rows}
    weight_of = {r["edge_id"]: r["weight"] for r in rows}
    tab = edge_table(graph)
    index = {int(i): n for n, i in enumerate(tab["id"])}
    weight = np.zeros(tab["id"].size)
    observed = np.zeros(tab["id"].size, dtype=bool)
    for r in rows:
        n = index.get(r["edge_id"])
        if n is not None:
            weight[n] = r["weight"]
            observed[n] = r["source"] == "observed"
    cfg = Config()

    zones, matrix = make_demand(graph, tab, weight, observed)
    jobs = [
        ("decoder-coverage", lambda: decoder_coverage(graph, rows)),
        ("impute-method", lambda: impute_method(graph, by_id)),
        ("kmeans-zones", lambda: kmeans_zones(graph, tab, weight, observed)),
        ("knn-holdout", lambda: knn_holdout(graph, tab, weight, observed, cfg)),
        ("lr-classes", lambda: lr_classes(graph, tab, weight, observed)),
        ("dijkstra-reach", lambda: dijkstra_reach(graph, weight_of)),
        ("astar-routes", lambda: astar_routes(graph, weight_of)),
        ("demand-flows", lambda: demand_lines(zones, matrix)),
    ]
    entries = []
    for name, fn in jobs:
        t1 = time.time()
        feats, entry = fn()
        n = write(out, name, feats)
        entries.append({"id": name, "file": f"{name}.geojson", "features": n, **entry})
        log(f"  {name:18s} {n:7,} features  {time.time() - t1:5.1f}s")
    t1 = time.time()
    for name, (feats, entry) in zip(("assignment-before", "assignment-all-at-once", "assignment-incremental"),
                                    assignment_frames(graph, weight_of, zones, matrix)):
        n = write(out, name, feats)
        entries.append({"id": name, "file": f"{name}.geojson", "features": n, **entry})
        log(f"  {name:18s} {n:7,} features")
    log(f"  assignment took {time.time() - t1:.1f}s")

    meta = read_meta(weights_csv)
    idx = {"graph_id": graph.graph_id(), "weights": weights_csv.name, "capture": meta.get("capture", ""),
           "slot_utc": rows[0]["slot_utc"] if rows else "",
           "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "seconds": round(time.time() - t0, 1), "layers": entries}
    (out / "index.json").write_text(json.dumps(idx, indent=1), encoding="utf-8")
    return idx


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graph", type=Path, default=Path("output/graph/dhaka.json"))
    ap.add_argument("--weights", type=Path, default=Path("output/traffic/complete.csv"))
    ap.add_argument("--out", type=Path, default=Path("output/layers"))
    args = ap.parse_args()
    graph = RoadGraph.load(args.graph)
    meta = read_meta(args.weights)
    if meta.get("graph_id") and meta["graph_id"] != graph.graph_id():
        print(f"ERROR: {args.weights} belongs to graph {meta['graph_id']}, not {graph.graph_id()}")
        return 2
    print(f"graph {len(graph.edges)} edges; building layers into {args.out}")
    idx = build_all(graph, args.weights, args.out)
    print(f"  {len(idx['layers'])} layers in {idx['seconds']}s -> {args.out / 'index.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
