"""City-scale experiments on every processed capture.

    python simulate.py history                read every processed capture into one table
    python simulate.py forecast               K-means scenarios, and the forecasters
                                              tested on captures they never saw
    python simulate.py city --day tuesday --time 17:00 --trips 2000
                                              thousands of A* searches across the city,
                                              all at once against in batches
    python simulate.py changes --area farmgate --max-changes 2
                                              CSP: which street changes help an area
    python simulate.py diversion --capture dhaka-2026-09-22T1305Z --minutes 90
                                              the real traffic from that capture on,
                                              against the same traffic steered around jams

Each command writes one JSON file into --out (default output/simulation) for
the map to show, and keeps --progress (default <out>/progress.json) up to date
so the map can say which algorithm is running and how far it has got.

`changes` reuses the trips of the last `city` run, so run `city` first.

Where each step's code lives:

    history    track_algos/forecast/history.py
    forecast   track_algos/forecast/scenarios.py   (K-means)
               track_algos/forecast/predict.py     (KNN, logistic regression, baselines)
               track_algos/forecast/evaluate.py    (the test)
    city       track_algos/simulation/city.py      (demand.py, assignment.py, A*)
    changes    track_algos/planning/road_changes.py, csp.py
    diversion  track_algos/simulation/diversion.py (A* detours, the coordinator)
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from track_algos.forecast import history as history_mod
from track_algos.forecast.evaluate import evaluate
from track_algos.forecast.predict import KNNForecast, LogisticForecast, all_forecasters
from track_algos.forecast.scenarios import find_scenarios
from track_algos.graph.areas import AREAS, CHANGE_RADIUS_M, STUDY_RADIUS_M, edges_within, street_names
from track_algos.graph.road_graph import RoadGraph
from track_algos.planning.road_changes import RoadChangePlanner
from track_algos.progress import Progress
from track_algos.simulation.city import run_city
from track_algos.simulation.diversion import run_diversion

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEK_OF = datetime(2026, 9, 21, tzinfo=history_mod.DHAKA)    # a Monday; only weekday and time are used


def target_time(day: str, clock: str) -> datetime:
    """'tuesday', '17:00' -> a Dhaka datetime on that weekday at that time."""
    hour, minute = (int(x) for x in clock.split(":"))
    return WEEK_OF.replace(day=WEEK_OF.day + DAYS.index(day), hour=hour, minute=minute)


def label(when: datetime) -> str:
    return when.strftime("%A %I:%M %p").replace(" 0", " ")


def write(out: Path, name: str, data: dict) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    path = out / name
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)
    return path


def load_history(args, graph: RoadGraph, progress) -> history_mod.History:
    folders = [Path(f) for f in args.weights] if args.weights else sorted(REPO.glob("week*/weights"))
    progress.step("history", f"reading processed captures from {len(folders)} folder(s)")
    return history_mod.build(folders, len(graph.edges), args.history, progress)


# ── the four commands ──────────────────────────────────────────────────────────

def cmd_history(args, graph, progress) -> dict:
    h = load_history(args, graph, progress)
    local = h.local
    return {"captures": len(h.names), "roads": h.roads,
            "from": label(local[0]) + local[0].strftime(", %d %b"),
            "to": label(local[-1]) + local[-1].strftime(", %d %b"),
            "days": round((h.times[-1] - h.times[0]) / 86400, 2)}


def cmd_forecast(args, graph, progress) -> dict:
    h = load_history(args, graph, progress)
    scenarios = find_scenarios(h, graph, k=args.scenarios, progress=progress)
    split = None
    if args.split:
        split = datetime.fromisoformat(args.split).replace(tzinfo=history_mod.DHAKA).timestamp()
    test = evaluate(h, all_forecasters(), split=split, progress=progress)
    return {"scenarios": scenarios, "evaluation": test}


def cmd_city(args, graph, progress) -> dict:
    h = load_history(args, graph, progress)
    when = target_time(args.day, args.time)
    model = KNNForecast() if args.model == "knn" else LogisticForecast()
    progress.step(model.name, f"learning from all {len(h.names):,} captures")
    model.fit(h, np.arange(len(h.names)))
    progress.step(model.name, f"forecasting every road for {label(when)}")
    levels = model.predict(when)
    result = run_city(graph, levels, trips=args.trips, batches=args.batches,
                      vehicles_per_trip=args.vehicles, seed=args.seed, progress=progress)
    result["when"] = {"day": args.day, "time": args.time, "label": label(when)}
    result["model"] = model.name
    result["forecast_mix"] = np.bincount(levels, minlength=4).tolist()
    return result


def cmd_changes(args, graph, progress) -> dict:
    city_file = args.out / "city.json"
    if not city_file.exists():
        raise SystemExit("run `python simulate.py city` first: the planner reuses its trips")
    city = json.loads(city_file.read_text(encoding="utf-8"))
    levels = np.frombuffer(city["forecast_levels"].encode("ascii"), dtype=np.uint8) - ord("0")
    area = AREAS[args.area]
    progress.step("areas", f"finding the options around {area['name']}")
    names = street_names(HERE / "output" / "graph" / "dhaka-osm.json", graph,
                         edges_within(graph, area, STUDY_RADIUS_M))
    planner = RoadChangePlanner(graph, area, levels, city["routes"], city["route_vehicles"],
                                names=names, streets=args.streets)
    result = planner.plan(max_changes=args.max_changes, progress=progress)
    result["area"] = {"key": args.area, **area, "change_radius_m": CHANGE_RADIUS_M,
                      "study_radius_m": STUDY_RADIUS_M}
    result["when"] = city["when"]
    result["city_trips"] = city["trips"]
    result["rules"] = [f"At most {args.max_changes} changes from today",
                       "Nobody is trapped: every junction that reaches every other today still does, "
                       "and every journey across the area possible today still is",
                       "Two U-turn gaps less than 400 m apart are not both closed",
                       "Main roads (motorway, trunk, primary, secondary) are never changed"]
    return result


def cmd_diversion(args, graph, progress) -> dict:
    """Actual traffic against the same traffic steered around the jams, from one
    capture forward through the real captures for --minutes."""
    if not args.capture:
        raise SystemExit("give --capture: the capture to start from")
    folders = [Path(f) for f in args.weights] if args.weights else sorted(REPO.glob("week*/weights"))
    files = {}
    for folder in folders:
        for f in sorted(Path(folder).glob("dhaka-*.csv.gz")):
            files.setdefault(f.name[:-len(".csv.gz")], f)          # a capture in two weeks counts once
    if args.capture not in files:
        raise SystemExit(f"{args.capture} is not processed")
    start = history_mod.capture_time(args.capture)
    names = [n for n in sorted(files, key=history_mod.capture_time)
             if 0 <= history_mod.capture_time(n) - start <= args.minutes * 60]
    progress.step("captures", f"reading {len(names)} captures, {args.minutes} minutes from {args.capture}")
    captures = [(n, history_mod.read_levels(files[n], len(graph.edges))[0]) for n in names]
    result = run_diversion(graph, captures, progress)
    first, last = history_mod.capture_time(names[0]), history_mod.capture_time(names[-1])
    result["from"] = label(datetime.fromtimestamp(first, history_mod.DHAKA))
    result["to"] = label(datetime.fromtimestamp(last, history_mod.DHAKA))
    result["captures"] = names
    result["minutes"] = round((last - first) / 60)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["history", "forecast", "city", "changes", "diversion"])
    ap.add_argument("--graph", type=Path, default=HERE / "output" / "graph" / "dhaka.json")
    ap.add_argument("--weights", nargs="*", help="folders of processed captures (default: ../week*/weights)")
    ap.add_argument("--history", type=Path, default=HERE / "output" / "history")
    ap.add_argument("--out", type=Path, default=HERE / "output" / "simulation")
    ap.add_argument("--progress", type=Path, help="progress file (default: <out>/progress.json)")
    # forecast
    ap.add_argument("--scenarios", type=int, default=5, help="K for K-means")
    ap.add_argument("--split", help="test from this Dhaka time on, e.g. 2026-09-23T19:55")
    # city
    ap.add_argument("--day", choices=DAYS, default="tuesday")
    ap.add_argument("--time", default="17:00", help="HH:MM, Dhaka time")
    ap.add_argument("--trips", type=int, default=2000)
    ap.add_argument("--batches", type=int, default=10)
    ap.add_argument("--vehicles", type=float, default=10.0, help="vehicles each trip stands for")
    ap.add_argument("--model", choices=["knn", "logistic"], default="knn")
    ap.add_argument("--seed", type=int, default=0)
    # changes
    ap.add_argument("--area", choices=sorted(AREAS), default="farmgate")
    ap.add_argument("--max-changes", type=int, default=2)
    ap.add_argument("--streets", type=int, default=8, help="side streets the planner may change")
    # diversion
    ap.add_argument("--capture", help="the capture to start from, e.g. dhaka-2026-09-22T1305Z")
    ap.add_argument("--minutes", type=int, default=90, help="how far forward to run")
    args = ap.parse_args()

    progress = Progress(args.progress or args.out / "progress.json", task=args.command)
    started = time.time()
    try:
        graph = RoadGraph.load(args.graph)
        if sorted(graph.edges) != list(range(len(graph.edges))):
            raise SystemExit("edge ids are not 0..n-1; the history table needs them to be")
        handler = {"history": cmd_history, "forecast": cmd_forecast, "city": cmd_city,
                   "changes": cmd_changes, "diversion": cmd_diversion}[args.command]
        result = handler(args, graph, progress)
        result["seconds"] = round(time.time() - started, 1)
        result["built"] = datetime.now(history_mod.DHAKA).strftime("%d %b %I:%M %p")
        name = f"changes-{args.area}.json" if args.command == "changes" else f"{args.command}.json"
        path = write(args.out, name, result)
        progress.finish(True, f"{args.command} finished in {result['seconds']:.0f} s -> {path.name}")
        return 0
    except SystemExit as stop:
        progress.finish(False, str(stop))
        print(stop, file=sys.stderr)
        return 2
    except Exception as error:                  # the map shows this rather than spinning forever
        progress.finish(False, f"{type(error).__name__}: {error}")
        raise


if __name__ == "__main__":
    sys.exit(main())
