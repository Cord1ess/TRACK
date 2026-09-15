"""Keyless traffic-tile fetching for the TRACK collector.

Google's tile server serves the traffic layer as its own transparent 256 px PNG
when only that layer is requested. No API key, no browser. This module is the
ONLY place the tile URL lives.

    traffic_url(z, x, y)      -> URL of the traffic-only overlay tile
    validate_tile(data, px)   -> strict integrity check before a tile is accepted
    fetch(z, x, y, ...)       -> validated bytes or None, with retries and backoff
    Limiter(rate)             -> token bucket so we stay polite

Every worker thread keeps one HTTPS connection open and reuses it, so the rate
is set by the limiter alone, not by a TLS handshake per tile. Measured from a
home connection: clean responses up to about 145 requests a second sustained,
with no rise in latency. The collector runs well below that (config.json).
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
    """Token bucket: `rate` requests per second. The jitter is centred on the
    interval, so the nominal rate is the real one. slow_down() halves the rate
    for the rest of the run, at most four times."""

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
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
    _local.conn = None


def validate_tile(data: bytes, tile_px: int) -> tuple[bool, str]:
    """Accept a tile only if it is a COMPLETE, decodable, correctly sized,
    transparent traffic overlay. Rejects truncated downloads (valid header but
    cut off mid-stream), HTML error bodies, wrong sizes and opaque base tiles,
    any of which would silently drop traffic that Google actually served."""
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
          incidents: bool = False, retries: int = 4, timeout: int = 30):
    """Fetch one tile; return ((x, y), data | None, reason). Retries on HTTP
    5xx/429, network errors and integrity failures with capped backoff. A 429
    or 503 also halves the rate for the rest of the run."""
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
                    if attempt:
                        stats["recovered_after_retry"] += 1
                    return (x, y), data, "ok"
                last = reason
                stats[f"invalid_{reason}"] += 1
            else:
                last = f"http_{status}"
                if status in SLOW_STATUS:
                    stats["slow_downs"] += 1
                    limiter.slow_down()
                elif status not in RETRY_STATUS:
                    return (x, y), None, last
        if attempt < retries:
            time.sleep(min(8, 2 ** attempt) + random.uniform(0, 1))
    return (x, y), None, last
