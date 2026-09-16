"""Downloads one traffic tile from Google.

Ask Google's map server for the traffic layer on its own and it sends back a
small transparent image with just the coloured lines on it. No API key and no
browser needed. This is the only file that knows the address.

    traffic_url    builds the address of one tile
    validate_tile  refuses anything that is not a whole, correct tile
    fetch          downloads one tile, retrying if it has to
    Limiter        holds the whole collector to a set number of requests a second

Each worker keeps one connection open and reuses it, rather than starting a new
encrypted connection for every tile. Google answered cleanly in testing up to
about 145 requests a second, with no slowdown; the collector runs at a third of
that, set in config.json.
"""

import http.client
import random
import threading
import time

import analyze

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122 Safari/537.36"
PNG_SIG = b"\x89PNG\r\n\x1a\n"
IEND = b"IEND\xaeB`\x82"

HOST = "www.google.com"
HEADERS = {"User-Agent": UA, "Referer": "https://www.google.com/maps/",
           "Accept": "image/png,image/*,*/*"}
RETRY_STATUS = (429, 500, 502, 503, 504)
SLOW_STATUS = (429, 503)                 # the server is asking for less: halve the rate
BLOCK_STATUS = (403,)                    # the server is refusing us, not throttling us

# A block is the one failure where trying harder makes things worse. When this
# many tiles in a row come back blocked, the sweep stops rather than putting
# the rest of the grid into a server that is already refusing. Chosen low: a
# real block hits every tile, so a handful is enough to recognise it, while a
# stray 403 among thousands of good tiles never reaches the count.
BLOCK_STREAK = 25


class Blocked(Exception):
    """Raised when the server is refusing us rather than throttling us.

    Different from a slow-down: there is no rate at which a block succeeds, so
    the capture ends and the next run tries later with a fresh runner address."""


class BlockWatch:
    """Counts consecutive blocked responses across every worker.

    Any success resets it, so this only fires when the server is refusing
    more or less everything."""

    def __init__(self, limit: int = BLOCK_STREAK):
        self.limit = limit
        self.streak = 0
        self.lock = threading.Lock()

    def ok(self) -> None:
        with self.lock:
            self.streak = 0

    def blocked(self) -> bool:
        """Record a block; True once the streak says we are being refused."""
        with self.lock:
            self.streak += 1
            return self.streak >= self.limit

# Layer group: 1e2 = traffic overlay, 2straffic, 3i999999 = latest data.
# The 2mN prefix must equal the number of !-elements that follow, or the
# server answers 400.
_LAYER_PLAIN = "!2m3!1e2!2straffic!3i999999"
_LAYER_INCIDENTS = ("!2m9!1e2!2straffic!3i999999!4m2!1sincidents!2s1"
                    "!4m2!1sincidents_text!2s1")
_TAIL = "!3m8!2sen!3sbd!5e1105!12m4!1e68!2m2!1sset!2sRoadmap!4e0!5m1!1e0"


def traffic_url(z: int, x: int, y: int, incidents: bool = False) -> str:
    layer = _LAYER_INCIDENTS if incidents else _LAYER_PLAIN
    return f"https://{HOST}/maps/vt/pb=!1m4!1m3!1i{z}!2i{x}!3i{y}{layer}{_TAIL}"


class Limiter:
    """Keeps every worker together to a set number of requests a second.

    Each request waits its turn. The wait is jittered a little either side of
    the gap, so requests do not arrive in a perfectly regular drum beat, but
    the average comes out at the rate asked for. If Google ever asks us to
    slow down, slow_down() halves the rate for the rest of the run."""

    def __init__(self, rate: float):
        self.interval = 1.0 / rate if rate > 0 else 0.0
        self.lock = threading.Lock()
        self.next = time.monotonic()
        self.slowed = 0

    @property
    def rate(self) -> float:
        return round(1.0 / self.interval, 1) if self.interval else 0.0

    def wait(self) -> None:
        if not self.interval:
            return
        with self.lock:
            now = time.monotonic()
            self.next = max(now, self.next) + self.interval * random.uniform(0.75, 1.25)
            sleep = self.next - now
        if sleep > 0:
            time.sleep(sleep)

    def slow_down(self) -> float:
        with self.lock:
            if self.interval and self.slowed < 4:
                self.interval *= 2
                self.slowed += 1
        return self.rate


_local = threading.local()


def _connection(timeout: int) -> http.client.HTTPSConnection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _local.conn = http.client.HTTPSConnection(HOST, timeout=timeout)
    return conn


def _drop_connection() -> None:
    """Throw this thread's connection away.

    The thread-local is cleared FIRST. If close() raises and we cleared after,
    the dead connection would stay bound to this worker for the rest of the
    run, and every tile it touched afterwards would fail."""
    conn = getattr(_local, "conn", None)
    _local.conn = None
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass                      # it is already unusable; nothing to salvage


def validate_tile(data: bytes, tile_px: int) -> tuple[bool, str]:
    """Accept a tile only if it is whole and the right kind of image.

    Catches a download cut off part way, an error page sent instead of an
    image, the wrong size, and a solid map tile where a see-through traffic
    tile was expected. Any of those would quietly lose real traffic, so a tile
    that fails here is fetched again rather than kept."""
    if not data or len(data) < 67:
        return False, "too_small"
    if data[:8] != PNG_SIG:
        return False, "not_png"
    if not data.rstrip(b"\x00").endswith(IEND):
        return False, "truncated"
    try:
        im = analyze.load_image(data)
        im.load()
    except Exception as ex:
        return False, f"decode_{type(ex).__name__}"
    if im.size != (tile_px, tile_px):
        return False, f"size_{im.width}x{im.height}"
    if not analyze.image_info(data)["transparent"]:
        return False, "opaque"
    return True, "ok"


def fetch(z: int, x: int, y: int, limiter: Limiter, stats, tile_px: int,
          incidents: bool = False, retries: int = 4, timeout: int = 30,
          watch: "BlockWatch | None" = None):
    """Download one tile. Returns its position, the image, and why if there is
    no image.

    Tries again on a server error, a network failure, or an image that does
    not pass validation, waiting longer between each attempt. If the server
    says it is too busy, the whole run slows down, not just this tile. If the
    server is refusing us outright, raises Blocked so the sweep can stop
    instead of hammering it."""
    path = traffic_url(z, x, y, incidents)[len(f"https://{HOST}"):]
    last = "unknown"
    for attempt in range(retries + 1):
        limiter.wait()
        try:
            conn = _connection(timeout)
            conn.request("GET", path, headers=HEADERS)
            r = conn.getresponse()
            data, status = r.read(), r.status
        except Exception as e:
            _drop_connection()
            stats[type(e).__name__] += 1
            last = type(e).__name__
        else:
            stats[f"http_{status}"] += 1
            if status == 200:
                ok, reason = validate_tile(data, tile_px)
                if ok:
                    if watch:
                        watch.ok()
                    if attempt:
                        stats["recovered_after_retry"] += 1
                    return (x, y), data, "ok"
                last = reason
                stats[f"invalid_{reason}"] += 1
                # A body that did not validate often means a truncated read on
                # a connection the server is closing. Start the next attempt
                # on a fresh one rather than inheriting whatever state this is.
                _drop_connection()
            else:
                last = f"http_{status}"
                # Any error status: the server may close the connection right
                # after sending it, and reusing it then raises on the next
                # request. Measured on a stub: a reused socket after a
                # server-side close raises ConnectionResetError, and a fresh
                # connection recovers.
                _drop_connection()
                if status in BLOCK_STATUS:
                    # Being refused is not being throttled. Retrying a block,
                    # or carrying on through the rest of the grid, is the one
                    # response that makes it worse: it was costing four
                    # minutes of full-rate requests into a server already
                    # saying no.
                    if watch and watch.blocked():
                        raise Blocked(f"{status} on {watch.limit} tiles in a row")
                    return (x, y), None, last
                if status in SLOW_STATUS:
                    stats["slow_downs"] += 1
                    limiter.slow_down()
                elif status not in RETRY_STATUS:
                    return (x, y), None, last
        if attempt < retries:
            time.sleep(min(8, 2 ** attempt) + random.uniform(0, 1))
    return (x, y), None, last
