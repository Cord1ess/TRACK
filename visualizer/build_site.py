"""Build the static site GitHub Pages serves: the visualizer and its data.

    python build_site.py --out ../site [--keep 3] [--prune]

Everything the dev server answers live is written as files:

    index.html, style.css, js/   the page and its ES modules
    data/manifest.json           what /api/config, /api/captures, /api/model,
                                 /api/graph and /api/layers answer, in one file
    data/model-<part>.<v>.json   the traffic model, versioned by content
    data/graph-<part>.<v>.json   the road graph
    data/layers/*.json           the algorithm layers and their index
    tiles/<capture>/z/x/y.png    captured tiles: native zoom and the pyramid
    tiles-clean/<capture>/...    the pure-colours variant

Only the newest --keep captures with status ok or partial are included;
--prune deletes the others from the captures folder. The page is marked
static, so app.js reads data/manifest.json and polls it for a new version
instead of talking to a server.

An existing site folder is reused: a capture's tiles never change once
written, so only tiles for a capture the site does not have yet are copied,
and tiles for captures no longer kept are deleted. Everything else is
rewritten every time. --fresh forces a build from empty.
"""

import argparse
import gzip
import json
import os
import shutil
import sys
import time
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import graph_data as graph_mod  # noqa: E402
import model as model_mod       # noqa: E402
import server                   # noqa: E402  (capture listing, tile pyramid, clean tiles)


def copy_app(out: Path) -> None:
    """The page, its stylesheet, and the js/ folder of ES modules.

    Every module has to travel, not just the entry point: a module that 404s
    fails silently and the page comes up blank. The folder is removed first so
    a module deleted from the source does not linger on the site."""
    for name in ("index.html", "style.css"):
        shutil.copyfile(HERE / "static" / name, out / name)
    shutil.rmtree(out / "js", ignore_errors=True)
    shutil.copytree(HERE / "static" / "js", out / "js")
    html = (out / "index.html").read_text(encoding="utf-8")
    html = html.replace("<head>", '<head>\n  <meta name="track-static" content="1">', 1)
    # the dev server serves the assets under /static/; on the site they sit
    # beside the page, and relative paths also work under a project URL
    html = html.replace('"/static/', '"')
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")


def pick_captures(keep: int) -> list[dict]:
    caps = [c for c in server.list_captures() if c.get("status") in ("ok", "partial")]
    caps.sort(key=lambda c: c.get("captured_utc") or "", reverse=True)
    return caps[:keep]


def copy_tiles(name: str, out: Path) -> int:
    """Native tiles and the lower-zoom pyramid, laid out as z/x/y.png."""
    root = server.CAPTURES / name
    native = int(json.loads((root / "manifest.json").read_text(encoding="utf-8"))["zoom"])
    n = 0
    for clean in (False, True):
        server.build_pyramid(name, quiet=True, clean=clean)
        dst = out / ("tiles-clean" if clean else "tiles") / name
        native_dir = server.cache_dir(root, "_clean") if clean else root / "tiles"
        for p in native_dir.glob(f"z{native}_*_*.png"):
            _, x, y = p.stem.split("_")
            target = dst / str(native) / x / f"{y}.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, target)
            n += 1
        pyramid = server.cache_dir(root, "_pyramid_clean" if clean else "_pyramid")
        for p in pyramid.rglob("*.png"):
            target = dst / p.relative_to(pyramid)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, target)
            n += 1
    return n


def sync_tiles(caps: list[dict], out: Path) -> None:
    """Copy tiles for captures the site does not have yet and delete the ones
    it should no longer serve. A capture's tiles never change once written, so
    a capture already on disk is left alone: on a rebuild only the new capture
    is copied instead of all of them."""
    wanted = {c["name"] for c in caps}
    for kind in ("tiles", "tiles-clean"):
        base = out / kind
        if not base.exists():
            continue
        for d in base.iterdir():
            if d.is_dir() and d.name not in wanted:
                shutil.rmtree(d)
                print(f"  tiles   dropped {d.name}", flush=True)
    for c in caps:
        if (out / "tiles" / c["name"]).exists() and (out / "tiles-clean" / c["name"]).exists():
            print(f"  tiles   {c['name']}: already on the site", flush=True)
            continue
        n = copy_tiles(c["name"], out)
        print(f"  tiles   {c['name']}: {n} files", flush=True)


def clear_stale(out: Path) -> None:
    """Everything except the tiles is rewritten every build, so remove the old
    copies first.

    Versioned vector payloads would otherwise pile up, and a file the build no
    longer writes would linger for good now that the site folder is reused:
    app.js is left behind from before the app became ES modules."""
    for p in (out / "data").glob("*.json"):
        p.unlink()
    shutil.rmtree(out / "data" / "layers", ignore_errors=True)
    for name in ("app.js",):                     # written by an older build
        (out / name).unlink(missing_ok=True)


def write_vector(service, parts: tuple, out: Path, prefix: str) -> dict:
    """The versioned payloads as plain JSON; the CDN compresses them on the way out.
    Without data the manifest carries ready: false and the page says so."""
    if not service.ensure():
        print(f"  {prefix:7s} no data: {service.error or 'nothing built yet'}", flush=True)
        return {"ready": False, "error": service.error or "not built"}
    for part in parts:
        data, version = service.payload(part)
        (out / f"{prefix}-{part}.{version}.json").write_bytes(gzip.decompress(data))
    return service.info()


def copy_layers(out: Path) -> list[dict]:
    src = server.OUTPUT / "layers"
    index = src / "index.json"
    if not index.exists():
        return []
    idx = json.loads(index.read_text(encoding="utf-8"))
    dst = out / "data" / "layers"
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(index, dst / "index.json")
    entries = []
    for l in idx.get("layers", []):
        f = src / l["file"]
        if not f.exists():
            continue
        name = Path(l["file"]).stem + ".json"
        shutil.copyfile(f, dst / name)
        entries.append({**l, "url": f"data/layers/{name}",
                        "built_utc": idx.get("built_utc", ""), "slot_utc": idx.get("slot_utc", "")})
    return entries


def stage_history(caps: list[dict], keep: int = 12) -> dict:
    """What a capture has actually cost, from the manifests we still hold.

    The page uses this to estimate how long the run in progress has left. A
    measured median beats a number typed into the source, which goes stale the
    moment the rate or the grid changes."""
    seen = []
    for c in caps:
        mp = server.CAPTURES / c["name"] / "manifest.json"
        if not mp.exists():
            continue
        try:
            m = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            continue
        if m.get("stage_seconds") and m.get("total_seconds"):
            seen.append((m.get("captured_utc", ""), m["stage_seconds"], m["total_seconds"]))
    if not seen:
        return {}
    seen.sort(reverse=True)
    seen = seen[:keep]
    median = lambda xs: sorted(xs)[len(xs) // 2]
    names = []
    for _, s, _ in seen:
        for k in s:
            if k not in names:
                names.append(k)
    return {
        "samples": len(seen),
        "capture_seconds": round(median([t for _, _, t in seen]), 1),
        "stages": [{"name": n, "seconds": round(median([s.get(n, 0) for _, s, _ in seen]), 1)}
                   for n in names],
    }


def folder_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


class Stage:
    """Times each part of the build, so a slow build says where."""

    def __init__(self):
        self.seconds = {}

    @contextmanager
    def __call__(self, name: str):
        t = time.time()
        try:
            yield
        finally:
            self.seconds[name] = round(self.seconds.get(name, 0) + time.time() - t, 1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=HERE.parent / "site")
    ap.add_argument("--keep", type=int, default=3, help="newest captures to include")
    ap.add_argument("--prune", action="store_true", help="delete captures not included")
    ap.add_argument("--fresh", action="store_true", help="rebuild from empty instead of reusing the site folder")
    ap.add_argument("--captures", type=Path, help="captures folder (default: ../data-collection/captures)")
    ap.add_argument("--output", type=Path, help="pipeline output folder (default: ../algorithms/output)")
    args = ap.parse_args()
    t0 = time.time()

    if args.captures:
        server.CAPTURES = args.captures.resolve()
    if args.output:
        server.OUTPUT = args.output.resolve()
    graph_json = server.OUTPUT / "graph" / "dhaka.json"
    packed = graph_json.with_suffix(".json.gz")
    if not graph_json.exists() and packed.exists():
        graph_json.write_bytes(gzip.decompress(packed.read_bytes()))
        print(f"  graph   unpacked {packed.name}", flush=True)

    stage = Stage()
    out = args.out.resolve()
    if args.fresh:
        shutil.rmtree(out, ignore_errors=True)
    (out / "data").mkdir(parents=True, exist_ok=True)
    clear_stale(out)
    copy_app(out)

    caps = pick_captures(args.keep)
    with stage("tiles"):
        sync_tiles(caps, out)
    if args.prune:
        keep = {c["name"] for c in caps}
        for d in server.CAPTURES.iterdir():
            if d.is_dir() and d.name not in keep and (d / "manifest.json").exists():
                shutil.rmtree(d)
                print(f"  pruned  {d.name}", flush=True)

    with stage("model"):
        model = write_vector(model_mod.discover(server.OUTPUT), ("major", "minor"), out / "data", "model")
    if model.get("ready"):
        print(f"  model   version {model.get('version')}: {model.get('lines', 0):,} lines", flush=True)
    with stage("graph"):
        graph = write_vector(graph_mod.discover(server.OUTPUT), ("nodes", "links"), out / "data", "graph")
    if graph.get("ready"):
        print(f"  graph   version {graph.get('version')}: {graph.get('links', 0):,} segments", flush=True)
    with stage("layers"):
        layers = copy_layers(out)
    print(f"  layers  {len(layers)}", flush=True)

    # on Actions the environment names the repository, so the page can link
    # to the run history; elsewhere there is nothing to link to
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    runs_url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/actions" if repo else ""
    runs_api = f"{os.environ.get('GITHUB_API_URL', 'https://api.github.com')}/repos/{repo}/actions/runs" if repo else ""
    manifest = {
        "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "min_zoom": server.MIN_ZOOM, "keep": args.keep, "runs_url": runs_url, "runs_api": runs_api,
        "captures": caps, "model": model, "graph": graph, "layers": layers,
        "timing": stage_history(caps),
    }
    (out / "data" / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print("  time    " + ", ".join(f"{k} {v}s" for k, v in stage.seconds.items()), flush=True)
    print(f"  site    {folder_size(out) / 1e6:.0f} MB, {sum(1 for _ in out.rglob('*') if _.is_file()):,} files "
          f"-> {out}  ({time.time() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
