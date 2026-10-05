"""Every processed capture as one table: captures x roads.

Reads each weights/<capture>.csv.gz written by batch.py and keeps two things for
every road at every capture:

    level    0 green, 1 yellow, 2 red, 3 dark red
    painted  True when Google painted the road at that moment (the rest of the
             roads were predicted by the spread)

Two weeks are about 2,600 captures x 110,382 roads. Reading the files takes
minutes, so the table is saved to output/history/history.npz and only rebuilt
when the set of captures changes.

Times: captures are stamped in UTC; Dhaka is UTC+6. In Bangladesh the weekend
is Friday and Saturday.
"""

import gzip
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

DHAKA = timezone(timedelta(hours=6))
WEEKEND_DAYS = (4, 5)                      # Python weekday(): Friday = 4, Saturday = 5
LEVEL_WEIGHTS = (25.0, 55.0, 85.0, 105.0)  # green, yellow, red, dark red
CACHE_NAME = "history.npz"


@dataclass
class History:
    names: list[str]          # capture names, oldest first
    times: np.ndarray         # (captures,) UTC seconds
    levels: np.ndarray        # (captures, roads) uint8, 0..3
    painted: np.ndarray       # (captures, roads) bool

    @property
    def local(self) -> list[datetime]:
        return [datetime.fromtimestamp(t, DHAKA) for t in self.times]

    @property
    def minute_of_day(self) -> np.ndarray:
        """Minutes since local midnight, 0..1439."""
        return np.array([d.hour * 60 + d.minute for d in self.local])

    @property
    def weekend(self) -> np.ndarray:
        return np.array([d.weekday() in WEEKEND_DAYS for d in self.local])

    @property
    def roads(self) -> int:
        return self.levels.shape[1]


def capture_time(name: str) -> float:
    """dhaka-2026-09-17T0841Z -> UTC seconds."""
    stamp = name.split("dhaka-")[-1]
    return datetime.strptime(stamp, "%Y-%m-%dT%H%MZ").replace(tzinfo=timezone.utc).timestamp()


def read_levels(path: Path, roads: int) -> tuple[np.ndarray, np.ndarray]:
    """One weights file -> (level per road, painted per road)."""
    levels = np.zeros(roads, dtype=np.uint8)
    painted = np.zeros(roads, dtype=bool)
    with gzip.open(path, "rt", encoding="utf-8") as f:
        header = f.readline().strip().split(",")
        col_edge, col_weight, col_source = (header.index(k) for k in ("edge_id", "weight", "source"))
        for line in f:
            cells = line.split(",")
            road = int(cells[col_edge])
            weight = float(cells[col_weight])
            # 25 -> 0, 55 -> 1, 85 -> 2, 105 -> 3
            levels[road] = 0 if weight < 40 else 1 if weight < 70 else 2 if weight < 95 else 3
            painted[road] = cells[col_source] == "observed"
    return levels, painted


def _read_one(job):
    path, roads = job
    return read_levels(path, roads)


def build(weights_dirs: list[Path], roads: int, cache_dir: Path, progress=None,
          workers: int = 6) -> History:
    """Load every capture under weights_dirs into one table, using the cache
    when it already holds exactly these captures."""
    files = {}
    for folder in weights_dirs:
        for f in sorted(Path(folder).glob("dhaka-*.csv.gz")):
            files.setdefault(f.name[:-len(".csv.gz")], f)       # a capture in two folders counts once
    names = sorted(files, key=capture_time)
    if not names:
        raise SystemExit(f"no processed captures in {', '.join(map(str, weights_dirs))}")

    cache = Path(cache_dir) / CACHE_NAME
    if cache.exists():
        held = load(cache_dir)
        if held.names == names and held.roads == roads:
            return held

    levels = np.zeros((len(names), roads), dtype=np.uint8)
    painted = np.zeros((len(names), roads), dtype=bool)
    jobs = [(files[n], roads) for n in names]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for i, (lv, pt) in enumerate(pool.map(_read_one, jobs, chunksize=8)):
            levels[i], painted[i] = lv, pt
            if progress and i % 25 == 0:
                progress.step("history", f"reading capture {i + 1:,} of {len(names):,}", i + 1, len(names))
    history = History(names, np.array([capture_time(n) for n in names]), levels, painted)
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, names=np.array(names), times=history.times,
                        levels=levels, painted=np.packbits(painted, axis=1))
    return history


def load(cache_dir: Path) -> History:
    data = np.load(Path(cache_dir) / CACHE_NAME)
    levels = data["levels"]
    painted = np.unpackbits(data["painted"], axis=1, count=levels.shape[1]).astype(bool)
    return History([str(n) for n in data["names"]], data["times"], levels, painted)
