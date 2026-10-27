"""Live progress for long runs: which algorithm is running, and how far it has got.

The simulation writes its state to a small JSON file, and the map's Simulate
panel reads that file every second to show, for example:

    A*  route 1,234 of 4,000

It is also printed to the terminal whenever the algorithm changes. File writes
are limited to four a second and go through a temporary file that is renamed
into place, so a reader never sees a half-written file.

`live(part, data)` publishes what the algorithm is looking at right now, for the
map to draw while it runs: live-routes.json (the routes A* has just found),
live-roads.json (how full the roads are after each batch), live-csp.json (the
road layout being scored). Each part is written at most twice a second.
"""

import json
import time
from pathlib import Path


class Progress:
    def __init__(self, path: Path | None = None, task: str = ""):
        self.path = Path(path) if path else None
        self.started = time.time()
        self.last_write = 0.0
        self.live_written: dict[str, float] = {}         # part -> when it was last written
        self.state = {"task": task, "running": True, "ok": None, "algorithm": "",
                      "message": "starting", "done": 0, "total": 0, "elapsed_s": 0.0}
        self._write(force=True)

    def step(self, algorithm: str, message: str, done: int = 0, total: int = 0) -> None:
        """Report what is running now. Call as often as you like."""
        if algorithm != self.state["algorithm"]:
            print(f"  [{algorithm}] {message}", flush=True)
        self.state.update(algorithm=algorithm, message=message, done=done, total=total)
        self._write()

    def finish(self, ok: bool = True, message: str = "done") -> None:
        print(f"  [{'done' if ok else 'failed'}] {message}", flush=True)
        self.state.update(running=False, ok=ok, message=message)
        self._write(force=True)

    def live(self, part: str, data: dict, force: bool = False) -> None:
        """Publish a live snapshot beside the progress file. Skipped when the
        last one of this part went out under half a second ago, unless forced."""
        now = time.time()
        if self.path is None or (not force and now - self.live_written.get(part, 0.0) < 0.5):
            return
        self.live_written[part] = now
        self._save(self.path.with_name(f"live-{part}.json"), data)

    def _write(self, force: bool = False) -> None:
        now = time.time()
        if self.path is None or (not force and now - self.last_write < 0.25):
            return
        self.last_write = now
        self.state["elapsed_s"] = round(now - self.started, 1)
        self._save(self.path, self.state)

    @staticmethod
    def _save(path: Path, data: dict) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            pass            # the reader had the file open; the next write will land
