"""TRACK visualizer dev server.

    python server.py [--port 8765] [--captures ../data-collection/captures]
                     [--output ../algorithms/output] [--build-pyramid <capture>]

Serves the map UI and, more importantly, the captured traffic as a REAL
slippy-map tile layer instead of a screenshot:

    GET /                                   map UI (static/index.html)
    GET /api/config                         min_zoom and folder paths
    GET /api/captures                       list of captures with summary
    GET /api/captures/<name>                full manifest
    GET /tiles/<name>/<z>/<x>/<y>.png[?clean=1]  traffic tile (clean=1: pure colours only,
                                            white casing removed). Native zoom straight from
                                            disk; lower zooms built by max-alpha
                                            pooling of the 4 children (cached in
                                            <capture>/_pyramid/); missing = transparent
    GET /api/layers                         GeoJSON layers found under algorithms/output
                                            and <capture>/layers (algorithm outputs)
    GET /layers/<relative path>.geojson     one layer
    GET /api/model                          what TRACK's own traffic data looks like
    GET /api/graph                          the road graph itself: junction and edge counts
    GET /graph.geojson[?part=nodes|links]   junctions as points, directed edges as lines,
                                            for looking at the network A* actually walks
    GET /model.geojson[?part=major|minor]   that data as vector features (gzipped), so the
                                            browser can re-colour and re-cost every road
                                            live as the weight sliders move. Major roads
                                            are a separate, much smaller part so the map
                                            paints before the side streets arrive.

Zero dependencies beyond Pillow + numpy. No keys anywhere.
"""

import argparse
import hashlib
import io
import json
import shutil
import mimetypes
import sys
import threading
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import numpy as np
from PIL import Image

import graph_data as graph_mod
import model as model_mod

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
CAPTURES = (HERE.parent / "data-collection" / "captures").resolve()
OUTPUT = (HERE.parent / "algorithms" / "output").resolve()
MIN_ZOOM = 8   # lowest zoom the server will build; the UI reads it from /api/config
MODEL: "model_mod.ModelData | None" = None   # TRACK's own decoded + predicted traffic
GRAPH: "graph_mod.GraphData | None" = None   # the road graph: junctions and directed edges

_LOCK = threading.Lock()


def _transparent_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (256, 256), (0, 0, 0, 0)).save(buf, "PNG")
    return buf.getvalue()


TRANSPARENT = _transparent_png()

# Google's four traffic classes with their reference colours (line fill, then
# border), MEASURED on the test capture; same values as
# data-collection/config.json and algorithms/track_algos/decode/palette.py.
# "Pure colours" tiles keep only pixels within PALETTE_TOL of one of these
# (plus recovered cased-line edges, see clean_tile) and drop everything else,
# notably the white casing Google draws around red lines, which shows as white
# specks on dark backgrounds. The clean caches carry a PALETTE_KEY stamp and
# are rebuilt automatically when these values change.
PALETTE = np.asarray([
    [22, 224, 152], [4, 156, 101],                    # green: fill, border
    [255, 207, 67], [245, 192, 37],                   # orange (amber): fill, border
    [209, 53, 43], [242, 78, 66], [152, 66, 58],      # red: cased fill, bordered fill, border
    [169, 39, 39], [112, 35, 35],                     # dark red: fill, border
], dtype=np.int32)
PALETTE_TOL = 50
PALETTE_KEY = hashlib.sha1(json.dumps(PALETTE.tolist() + [PALETTE_TOL]).encode()).hexdigest()[:10]
_STAMPED: set = set()


def cache_dir(root: Path, kind: str) -> Path:
    """<capture>/<kind> cache folder. Palette-dependent caches (*clean) carry a
    .palette stamp; a stamp that does not match PALETTE_KEY wipes the folder
    once, so a palette change can never serve stale pure-colours tiles."""
    d = root / kind
    if kind.endswith("clean"):
        with _LOCK:
            if d not in _STAMPED:
                stamp = d / ".palette"
                if d.exists() and (not stamp.exists() or stamp.read_text(encoding="utf-8") != PALETTE_KEY):
                    print(f"  palette changed: rebuilding {root.name}/{kind}", flush=True)
                    shutil.rmtree(d, ignore_errors=True)
                d.mkdir(parents=True, exist_ok=True)
                stamp.write_text(PALETTE_KEY, encoding="utf-8")
                _STAMPED.add(d)
    return d


def casing_blend(rgb: np.ndarray, refs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Google draws red and dark red inside a WHITE casing, so a line's edge
    pixels are fill blended with white: p = t*C + (1-t)*white. For every pixel
    fit t against each reference colour C (channels where C is itself near
    white carry no information and are skipped) and return (index of the best
    fitting reference, t). t is the fill coverage: 1 = pure fill, 0 = pure
    casing; a poor fit (channels disagree by more than 0.08) gives t = 0."""
    p = rgb.astype(np.float64)
    denom = 255.0 - refs.astype(np.float64)                             # (K,3)
    valid = denom > 8.0
    t = (255.0 - p[:, None, :]) / np.where(valid, denom, 1.0)[None]     # (N,K,3)
    t = np.where(valid[None], t, 0.0)
    tm = t.sum(axis=2) / valid.sum(axis=1)[None, :]                     # (N,K)
    spread = (np.abs(t - tm[:, :, None]) * valid[None]).max(axis=2)
    tm = np.where((spread < 0.08) & (tm >= 0.0) & (tm <= 1.0), tm, 0.0)
    best = tm.argmax(axis=1)
    return best, tm[np.arange(len(best)), best]


def clean_tile(data: bytes) -> bytes:
    """Pure-colours variant of a tile. Keeps pixels within PALETTE_TOL of a
    reference colour; re-draws a cased line's edge pixels (fill blended with
    the white casing) as the fill colour with alpha = fill coverage, so red
    lines keep their anti-aliased width without a white halo; drops the rest."""
    a = np.asarray(Image.open(io.BytesIO(data)).convert("RGBA")).copy()
    flat = a.reshape(-1, 4)
    rgb = flat[:, :3].astype(np.int32)
    d2 = ((rgb[:, None, :] - PALETTE[None, :, :]) ** 2).sum(axis=2).min(axis=1)
    opaque = flat[:, 3] > 128
    keep = (d2 < PALETTE_TOL ** 2) & opaque
    edge = np.flatnonzero(opaque & ~keep)
    if edge.size:
        idx, t = casing_blend(rgb[edge], PALETTE)
        vis = t >= 0.15
        edge, idx, t = edge[vis], idx[vis], t[vis]
        flat[edge, :3] = PALETTE[idx]
        flat[edge, 3] = np.round(t * flat[edge, 3]).astype(np.uint8)
        keep[edge] = True
    flat[~keep] = 0
    buf = io.BytesIO()
    Image.fromarray(a, "RGBA").save(buf, "PNG", optimize=False)
    return buf.getvalue()


# ---------------------------------------------------------------- captures

def list_captures() -> list[dict]:
    out = []
    if not CAPTURES.exists():
        return out
    for d in sorted(CAPTURES.iterdir()):
        mp = d / "manifest.json"
        if not d.is_dir() or not mp.exists():
            continue
        try:
            m = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({
            "name": d.name, "zoom": m.get("zoom"), "bbox": m.get("bbox"),
            "tile_range": m.get("tile_range"), "coverage_pct": m.get("coverage_pct"),
            "captured_utc": m.get("captured_utc"), "status": m.get("status", "?"),
            # captures taken before the two timelines existed carry no series;
            # they were all taken by hand, so they are tests
            "series": m.get("series", "test"),
            "tiles_nonempty": m.get("tiles_nonempty"), "expected_tiles": m.get("expected_tiles"),
            "note": m.get("note", ""),
        })
    return out


@lru_cache(maxsize=64)
def capture_zoom(name: str) -> int | None:
    mp = CAPTURES / name / "manifest.json"
    if not mp.exists():
        return None
    return int(json.loads(mp.read_text(encoding="utf-8")).get("zoom", 17))


# ---------------------------------------------------------------- tiles

def pool2(children: list[bytes | None]) -> bytes:
    """Build one 256 px tile from its four children (NW, NE, SW, SE) at the
    next zoom by MAX-ALPHA pooling: each output pixel keeps the most opaque of
    its 2x2 source pixels, so thin traffic lines survive instead of averaging
    into transparency."""
    canvas = np.zeros((512, 512, 4), dtype=np.uint8)
    for i, data in enumerate(children):
        if not data:
            continue
        try:
            a = np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))
        except Exception:
            continue
        y0, x0 = (i // 2) * 256, (i % 2) * 256
        canvas[y0:y0 + 256, x0:x0 + 256] = a
    # 512x512 -> 256x256: every 2x2 block keeps its most opaque pixel.
    blocks = canvas.reshape(256, 2, 256, 2, 4).transpose(0, 2, 1, 3, 4).reshape(256, 256, 4, 4)
    idx = blocks[:, :, :, 3].argmax(axis=2)
    pooled = np.take_along_axis(blocks, idx[:, :, None, None], axis=2)[:, :, 0, :]
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(pooled), "RGBA").save(buf, "PNG", optimize=False)
    return buf.getvalue()


def _write_cache(path: Path, data: bytes) -> None:
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def tile_bytes(name: str, z: int, x: int, y: int, clean: bool = False) -> bytes | None:
    """Return PNG bytes for a tile, building the pyramid level on demand.
    clean=True serves the pure-colours variant (separate cache trees)."""
    native = capture_zoom(name)
    if native is None or z > native or z < MIN_ZOOM:
        return None
    root = CAPTURES / name
    if z == native:
        p = root / "tiles" / f"z{z}_{x}_{y}.png"
        if not p.exists():
            return None
        if not clean:
            return p.read_bytes()
        cache = cache_dir(root, "_clean") / f"z{z}_{x}_{y}.png"
        if cache.exists():
            return cache.read_bytes()
        data = clean_tile(p.read_bytes())
        _write_cache(cache, data)
        return data
    cache = cache_dir(root, "_pyramid_clean" if clean else "_pyramid") / str(z) / str(x) / f"{y}.png"
    if cache.exists():
        return cache.read_bytes()
    children = [tile_bytes(name, z + 1, 2 * x + dx, 2 * y + dy, clean) for dy in (0, 1) for dx in (0, 1)]
    if not any(children):
        return None
    data = pool2(children)
    _write_cache(cache, data)
    return data


def build_pyramid(name: str, quiet: bool = False, clean: bool = False) -> None:
    """Pre-build every lower-zoom tile for a capture (one-time, so the UI is instant)."""
    mp = CAPTURES / name / "manifest.json"
    m = json.loads(mp.read_text(encoding="utf-8"))
    native, r = int(m["zoom"]), m["tile_range"]
    total = 0
    for z in range(native - 1, MIN_ZOOM - 1, -1):
        dz = native - z
        x0, x1 = r["x0"] >> dz, r["x1"] >> dz
        y0, y1 = r["y0"] >> dz, r["y1"] >> dz
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                if tile_bytes(name, z, x, y, clean):
                    total += 1
        if not quiet:
            print(f"  z{z}: {(x1 - x0 + 1) * (y1 - y0 + 1)} tiles")
    tag = "_pyramid_clean/" if clean else "_pyramid/"
    print(f"  pyramid ready for {name}: {total} tiles (z{MIN_ZOOM}..z{native - 1}) under {tag}", flush=True)


# ---------------------------------------------------------------- layers

def list_layers() -> list[dict]:
    # the pipeline's layers stage writes an index with a description and legend
    # per layer; when it exists, that is the list
    index = OUTPUT / "layers" / "index.json"
    if index.exists():
        try:
            data = json.loads(index.read_text(encoding="utf-8"))
            return [{**l, "id": l["id"], "url": f"/layers/output/layers/{l['file']}",
                     "built_utc": data.get("built_utc", ""), "slot_utc": data.get("slot_utc", "")}
                    for l in data.get("layers", []) if (OUTPUT / "layers" / l["file"]).exists()]
        except Exception as e:
            print(f"  layers index unreadable ({e}); scanning instead", flush=True)
    layers = []
    roots = [("output", OUTPUT)]
    if CAPTURES.exists():
        roots += [(f"capture:{d.name}", d / "layers") for d in CAPTURES.iterdir() if (d / "layers").is_dir()]
    for source, root in roots:
        if not root.exists():
            continue
        for p in sorted(root.rglob("*.geojson")):
            rel = p.relative_to(root).as_posix()
            key = f"{source}/{rel}"
            layers.append({"id": key, "name": p.stem, "source": source, "url": f"/layers/{key}"})
    return layers


def layer_path(key: str) -> Path | None:
    source, _, rel = key.partition("/")
    if source == "output":
        base = OUTPUT
    elif source.startswith("capture:"):
        base = CAPTURES / source[len("capture:"):] / "layers"
    else:
        return None
    p = (base / rel).resolve()
    if base.resolve() not in p.parents or not p.exists() or p.suffix != ".geojson":
        return None
    return p


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def handle(self):
        try:
            super().handle()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-store",
              encoding: str | None = None, headers: dict | None = None) -> None:
        self.send_response(code)
        if code != 304:
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            if encoding:
                self.send_header("Content-Encoding", encoding)
        self.send_header("Cache-Control", cache)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if code != 304:
            self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def _vector(self, service, query: str, default_part: str) -> None:
        """A versioned, gzipped GeoJSON payload with conditional-request support."""
        q = parse_qs(query or "")
        part = (q.get("part") or [default_part])[0]
        asked = (q.get("v") or [""])[0]
        data, version = service.payload(part) if service else (None, "")
        if data is None:
            self._json({"error": (service.error if service else "not configured") or "no data"}, 503)
            return
        etag = f'"{version}-{part}"'
        # A URL that names the version being served can never change, so the browser
        # may keep it for good. Anything else revalidates, and an unchanged payload
        # then costs a 304 instead of megabytes.
        cache = "public, max-age=31536000, immutable" if asked == version else "no-cache"
        if etag in (self.headers.get("If-None-Match") or ""):
            self._send(304, b"", "", cache, headers={"ETag": etag})
        else:
            self._send(200, data, "application/geo+json", cache, encoding="gzip",
                       headers={"ETag": etag})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        clean = "clean=1" in (parsed.query or "")
        try:
            if path in ("/", "/index.html"):
                self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif path.startswith("/static/"):
                p = (STATIC / path[len("/static/"):]).resolve()
                if STATIC.resolve() in p.parents and p.is_file():
                    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
                    self._send(200, p.read_bytes(), ctype)
                else:
                    self._json({"error": "not found"}, 404)
            elif path == "/api/config":
                self._json({"min_zoom": MIN_ZOOM, "captures_dir": str(CAPTURES), "output_dir": str(OUTPUT)})
            elif path == "/api/captures":
                self._json(list_captures())
            elif path.startswith("/api/captures/"):
                mp = CAPTURES / path[len("/api/captures/"):] / "manifest.json"
                if mp.exists() and CAPTURES in mp.resolve().parents:
                    self._send(200, mp.read_bytes(), "application/json")
                else:
                    self._json({"error": "unknown capture"}, 404)
            elif path.startswith("/tiles/"):
                parts = path[len("/tiles/"):].split("/")
                if len(parts) != 4 or not parts[3].endswith(".png"):
                    self._json({"error": "bad tile path"}, 400)
                    return
                name, z, x, y = parts[0], int(parts[1]), int(parts[2]), int(parts[3][:-4])
                data = tile_bytes(name, z, x, y, clean)
                self._send(200, data or TRANSPARENT, "image/png", "public, max-age=3600")
            elif path == "/api/model":
                self._json(MODEL.info() if MODEL else {"ready": False, "error": "not configured"})
            elif path == "/model.geojson":
                self._vector(MODEL, parsed.query, "all")
            elif path == "/api/graph":
                self._json(GRAPH.info() if GRAPH else {"ready": False, "error": "not configured"})
            elif path == "/graph.geojson":
                self._vector(GRAPH, parsed.query, "nodes")
            elif path == "/api/layers":
                self._json(list_layers())
            elif path.startswith("/layers/"):
                p = layer_path(path[len("/layers/"):])
                if p:
                    self._send(200, p.read_bytes(), "application/geo+json")
                else:
                    self._json({"error": "unknown layer"}, 404)
            else:
                self._json({"error": "unknown route"}, 404)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass  # client cancelled the request (normal during fast zoom/pan); nothing to answer
        except Exception as e:
            try:
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass


def main() -> int:
    global CAPTURES, OUTPUT, MODEL, GRAPH
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--captures", type=Path, default=CAPTURES)
    ap.add_argument("--output", type=Path, default=OUTPUT)
    ap.add_argument("--build-pyramid", metavar="CAPTURE", help="pre-build lower zooms for a capture and exit")
    args = ap.parse_args()
    CAPTURES, OUTPUT = args.captures.resolve(), args.output.resolve()
    capture_zoom.cache_clear()
    MODEL = model_mod.discover(OUTPUT)
    GRAPH = graph_mod.discover(OUTPUT)
    if args.build_pyramid:
        build_pyramid(args.build_pyramid)
        build_pyramid(args.build_pyramid, clean=True)
        return 0
    caps = list_captures()

    def prewarm():  # raw pyramid first, then the pure-colours one, so first views are instant
        for clean in (False, True):
            for c in caps:
                try:
                    build_pyramid(c["name"], quiet=True, clean=clean)
                except Exception as e:
                    print(f"  pyramid for {c['name']} failed: {type(e).__name__}: {e}", flush=True)
    threading.Thread(target=prewarm, daemon=True).start()

    print(f"\n  TRACK visualizer  ->  http://127.0.0.1:{args.port}", flush=True)
    print(f"  captures: {', '.join(c['name'] for c in caps) or 'none'}  ({CAPTURES})", flush=True)
    print(f"  layers:   {len(list_layers())} geojson  ({OUTPUT})", flush=True)
    mi = MODEL.info()
    if mi.get("ready"):
        print(f"  model:    {mi['edges']} edges -> {mi['lines']} lines "
              f"({mi['observed']} observed, {mi['predicted']} predicted) from {mi['weights']}, "
              f"{mi['bytes'] / 1048576:.1f} MB gzipped "
              f"({mi['bytes_major'] / 1048576:.1f} MB for the major roads)\n", flush=True)
    else:
        print(f"  model:    not available ({mi.get('error')}) - run the algorithms pipeline", flush=True)
    gi = GRAPH.info()
    if gi.get("ready"):
        print(f"  graph:    {gi['nodes']} nodes ({gi['junctions']} junctions, "
              f"{gi['cut_points']} cut points), {gi['links']} directed edges, "
              f"{gi['bytes'] / 1048576:.1f} MB gzipped\n", flush=True)
    else:
        print(f"  graph:    not available ({gi.get('error')})\n", flush=True)
    # On Windows SO_REUSEADDR lets a second server bind a port that is already
    # in use; the old process keeps answering and you debug code that is not
    # running. Refuse instead, and say which process to kill.
    class Server(ThreadingHTTPServer):
        allow_reuse_address = False

    try:
        srv = Server(("127.0.0.1", args.port), Handler)
    except OSError as e:
        print(f"\n  cannot bind port {args.port}: {e}", flush=True)
        print("  another server is already running there; stop it first:", flush=True)
        print(f"    powershell \"Get-NetTCPConnection -LocalPort {args.port} -State Listen | "
              f"ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force }}\"", flush=True)
        return 1
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
