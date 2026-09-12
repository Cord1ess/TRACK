"""Build the Dhaka RoadGraph from OpenStreetMap via the Overpass API.

    python -m track_algos.graph.build_graph --out output/graph/dhaka.json
        [--bbox N,S,E,W] [--min-class living_street] [--max-edge-m 150] [--geojson]

No heavy GIS dependencies: one Overpass query, then pure-Python assembly.

Two cuts turn OSM ways into edges:
  1. at every node shared by two or more ways (an intersection), so an edge
     runs junction to junction and routing can turn where drivers can turn;
  2. at a fixed spacing along anything still longer than `--max-edge-m`, so one
     edge describes one traffic condition. Without this a 1 km arterial stretch
     would be a single edge that is jammed at one end and clear at the other,
     and whatever value we gave it would be wrong for half its length.
The cut points are new nodes with negative ids (OSM ids are positive), so a
synthetic junction is always recognisable.

A two-way street becomes two directed edges; a one-way street becomes one
(reversed if oneway=-1). `--min-class` drops classes below it in importance;
service roads, tracks and paths are always dropped.
"""

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

from ..geo import haversine_m, polyline_length_m
from .road_graph import RoadGraph

# Public Overpass instances, tried in order. The main one rate-limits and returns
# 504 under load, which is normal rather than exceptional, so never depend on one.
MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]
DHAKA = {"north": 23.902086, "south": 23.711736, "east": 90.499678, "west": 90.326774}   # whole metropolitan area; must match data-collection/config.json
ORDER = ["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street"]
KEEP = set(ORDER) | {c + "_link" for c in ORDER}
MIN_PIECE_M = 20.0   # a trailing stub shorter than this is merged into the piece before it


def _ask(url: str, bbox: dict, timeout: int) -> dict:
    q = (f'[out:json][timeout:{timeout}];'
         f'way["highway"]({bbox["south"]},{bbox["west"]},{bbox["north"]},{bbox["east"]});'
         f'(._;>;);out body;')
    req = urllib.request.Request(url, data=urllib.parse.urlencode({"data": q}).encode(),
                                 headers={"User-Agent": "TRACK-graph-builder/1.0"})
    with urllib.request.urlopen(req, timeout=timeout + 30) as r:
        return json.loads(r.read().decode("utf-8"))


def _quadrants(b: dict) -> list[dict]:
    my, mx = (b["north"] + b["south"]) / 2.0, (b["east"] + b["west"]) / 2.0
    return [{"north": b["north"], "south": my, "west": b["west"], "east": mx},
            {"north": b["north"], "south": my, "west": mx, "east": b["east"]},
            {"north": my, "south": b["south"], "west": b["west"], "east": mx},
            {"north": my, "south": b["south"], "west": mx, "east": b["east"]}]


def overpass_roads(bbox: dict, timeout: int = 180, tries: int = 2, depth: int = 0,
                   log=print) -> dict:
    """All highway ways in the box, with their nodes.

    Robustness, in order: try every mirror, twice each with a short backoff; if
    the box still will not come back, cut it into quadrants and fetch those
    (recursively, to `depth` 2 = at most 16 pieces). A way that straddles a cut
    comes back whole from both sides and is merged by id, so splitting changes
    nothing about the result."""
    errors = []
    for url in MIRRORS:
        for attempt in range(tries):
            try:
                return _ask(url, bbox, timeout)
            except Exception as e:
                errors.append(f"{urllib.parse.urlsplit(url).netloc}: {type(e).__name__} {e}")
                log(f"    {urllib.parse.urlsplit(url).netloc} failed ({type(e).__name__}), "
                    f"attempt {attempt + 1}/{tries}")
                time.sleep(3 * (attempt + 1))
    if depth >= 2:
        raise RuntimeError("Overpass unavailable on every mirror:\n  " + "\n  ".join(errors[-4:]))

    log(f"  splitting the area into 4 and retrying (depth {depth + 1})")
    merged: dict[tuple, dict] = {}
    for i, q in enumerate(_quadrants(bbox), 1):
        part = overpass_roads(q, timeout, tries, depth + 1, log)
        for el in part["elements"]:
            merged[(el["type"], el["id"])] = el
        log(f"    quadrant {i}/4: {len(part['elements'])} elements, {len(merged)} unique so far")
    return {"elements": list(merged.values())}


def split_polyline(pts: list, max_len: float) -> list[list]:
    """Cut a lon/lat polyline into consecutive pieces of about max_len metres,
    interpolating a new point wherever a cut falls inside a segment. Returns
    [pts] unchanged when the line is already short enough.

    A final piece shorter than MIN_PIECE_M is folded back into the one before
    it, because a half-metre edge is a routing nuisance and carries no useful
    traffic reading. So the guarantee is `max_len + MIN_PIECE_M`, not `max_len`.
    Total length is always preserved exactly."""
    if max_len <= 0 or polyline_length_m(pts) <= max_len:
        return [pts]
    pieces, cur, acc = [], [pts[0]], 0.0
    for a, b in zip(pts[:-1], pts[1:]):
        pa, left = a, haversine_m(a[0], a[1], b[0], b[1])
        if left == 0.0:
            continue
        while acc + left > max_len:
            t = (max_len - acc) / left
            cut = [pa[0] + (b[0] - pa[0]) * t, pa[1] + (b[1] - pa[1]) * t]
            cur.append(cut)
            pieces.append(cur)
            cur, pa, acc = [cut], cut, 0.0
            left = haversine_m(pa[0], pa[1], b[0], b[1])
        cur.append(b)
        acc += left
    if len(cur) >= 2:
        pieces.append(cur)
    # a short trailing stub is a routing nuisance: fold it back into its neighbour
    if len(pieces) > 1 and polyline_length_m(pieces[-1]) < MIN_PIECE_M:
        tail = pieces.pop()
        pieces[-1] = pieces[-1] + tail[1:]
    return pieces


def _chain(g: RoadGraph, u: int, v: int, pts: list, oneway: bool, kw: dict,
           max_edge_m: float, new_id, stats: Counter) -> None:
    """Add u -> v as one edge, or as a chain of edges through synthetic nodes
    when the geometry is longer than max_edge_m."""
    pieces = split_polyline(pts, max_edge_m)
    ids = [u] + [new_id() for _ in range(len(pieces) - 1)] + [v]
    if len(pieces) > 1:
        stats["split_ways"] += 1
        stats["split_extra_edges"] += len(pieces) - 1
    for i, piece in enumerate(pieces):
        a, b = ids[i], ids[i + 1]
        if a not in g.nodes:
            g.add_node(a, *piece[0])
        if b not in g.nodes:
            g.add_node(b, *piece[-1])
        if a == b:
            continue
        if oneway:
            g.add_edge(a, b, piece, oneway=True, **kw)
            stats["oneway_edges"] += 1
        else:
            g.add_two_way(a, b, piece, **kw)
            stats["twoway_edges"] += 2


def build(osm: dict, min_class: str = "living_street", max_edge_m: float = 150.0) -> tuple[RoadGraph, dict]:
    allowed = set(ORDER[: ORDER.index(min_class) + 1])
    allowed |= {c + "_link" for c in allowed}
    nodes = {el["id"]: (el["lon"], el["lat"]) for el in osm["elements"] if el["type"] == "node"}
    ways = [el for el in osm["elements"] if el["type"] == "way"
            and el.get("tags", {}).get("highway") in allowed and len(el.get("nodes", [])) >= 2]

    # A node used by 2+ ways, or at a way end, is a junction: split there.
    use = Counter()
    for w in ways:
        for n in w["nodes"]:
            use[n] += 1
        use[w["nodes"][0]] += 1
        use[w["nodes"][-1]] += 1
    junction = {n for n, c in use.items() if c >= 2}

    g = RoadGraph()
    stats = Counter()
    counter = [0]

    def new_id() -> int:
        counter[0] -= 1
        return counter[0]

    for w in ways:
        tags = w.get("tags", {})
        hw = tags["highway"]
        lanes = None
        try:
            lanes = int(str(tags.get("lanes", "")).split(";")[0])
        except ValueError:
            pass
        ow = str(tags.get("oneway", "no")).lower()
        oneway = ow in ("yes", "true", "1", "-1")
        speed = None
        try:
            speed = float(str(tags.get("maxspeed", "")).split()[0])
        except (ValueError, IndexError):
            pass
        seq = w["nodes"]
        seg = [seq[0]]
        for n in seq[1:]:
            seg.append(n)
            if n in junction or n == seq[-1]:
                pts = [list(nodes[k]) for k in seg if k in nodes]
                u, v = seg[0], seg[-1]
                if len(pts) >= 2 and u in nodes and v in nodes and u != v:
                    if oneway and ow == "-1":
                        pts, u, v = list(reversed(pts)), v, u
                    for k in (u, v):
                        if k not in g.nodes:
                            g.add_node(k, *nodes[k])
                    _chain(g, u, v, pts, oneway, dict(highway=hw, lanes=lanes, free_flow_kmph=speed),
                           max_edge_m, new_id, stats)
                    stats[hw] += 1
                seg = [n]
    stats["synthetic_nodes"] = -counter[0]
    return g, dict(stats)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("output/graph/dhaka.json"))
    ap.add_argument("--bbox", help="north,south,east,west")
    ap.add_argument("--min-class", default="living_street", choices=ORDER)
    ap.add_argument("--max-edge-m", type=float, default=150.0,
                    help="cut anything longer into pieces of at most this length (0 = never cut)")
    ap.add_argument("--geojson", action="store_true", help="also write <out>_edges.geojson for the visualizer")
    ap.add_argument("--osm-cache", type=Path, help="reuse/save the raw Overpass JSON here")
    args = ap.parse_args()
    bbox = DHAKA
    if args.bbox:
        n, s, e, w = (float(v) for v in args.bbox.split(","))
        bbox = {"north": n, "south": s, "east": e, "west": w}

    # The cache records the box it was fetched for. Reusing a cache from a
    # different area silently rebuilds the wrong city, so a mismatch re-queries.
    osm = None
    if args.osm_cache and args.osm_cache.exists():
        cached = json.loads(args.osm_cache.read_text(encoding="utf-8"))
        same = cached.get("bbox") == bbox
        if same:
            osm = cached
            print(f"loaded cached Overpass response ({len(osm['elements'])} elements)")
        else:
            print(f"cached Overpass response is for a different area {cached.get('bbox')}; re-querying")
    if osm is None:
        print("querying Overpass (this can take a minute)...")
        t0 = time.time()
        osm = overpass_roads(bbox)
        osm["bbox"] = bbox
        print(f"  {len(osm['elements'])} elements in {time.time() - t0:.0f}s")
        if args.osm_cache:
            args.osm_cache.parent.mkdir(parents=True, exist_ok=True)
            args.osm_cache.write_text(json.dumps(osm), encoding="utf-8")

    g, stats = build(osm, args.min_class, args.max_edge_m)
    g.save(args.out)
    lens = [e.length_m for e in g.edges.values()]
    total_km = sum(lens) / 1000.0
    print(f"graph: {len(g.nodes)} nodes, {len(g.edges)} directed edges -> {args.out}")
    print(f"  {total_km:.0f} km of directed road, edge length min/median/max "
          f"{min(lens):.0f}/{sorted(lens)[len(lens) // 2]:.0f}/{max(lens):.0f} m")
    print(f"  ways split by the {args.max_edge_m:.0f} m cap: {stats.get('split_ways', 0)} "
          f"(+{stats.get('split_extra_edges', 0)} pieces, {stats.get('synthetic_nodes', 0)} new nodes)")
    print("  ways by class:", {k: v for k, v in sorted(stats.items()) if k in KEEP})
    if args.geojson:
        gj = args.out.with_name(args.out.stem + "_edges.geojson")
        gj.write_text(json.dumps(g.to_geojson()), encoding="utf-8")
        print(f"  edges geojson -> {gj}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
