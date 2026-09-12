"""Self-test for the TRACK collector: proves every component before a capture.
Needs no network.   python tests/selftest.py [--network]

--network additionally fetches ONE real tile over Kakrail to prove the endpoint
and the validator agree on live data.
"""

import argparse
import io
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "collector"))
import analyze  # noqa: E402
import checks  # noqa: E402
import fetch  # noqa: E402
import grid  # noqa: E402
import tiles as tiles_mod  # noqa: E402

RESULTS = []


def test(name):
    def deco(fn):
        def run(*a, **k):
            t0 = time.time()
            try:
                detail, ok = fn(*a, **k) or "ok", True
            except Exception as e:
                ok, detail = False, f"{type(e).__name__}: {e}"
                if os.environ.get("SELFTEST_TRACE"):
                    traceback.print_exc()
            RESULTS.append((name, ok, detail))
            print(f"  [{'ok' if ok else 'FAIL'}] {name}: {detail} ({time.time() - t0:.1f}s)")
            return ok
        return run
    return deco


def synth_tile(colour, tile_px=256, alpha=True):
    img = Image.new("RGBA", (tile_px, tile_px), (0, 0, 0, 0))
    ImageDraw.Draw(img).line([0, tile_px // 2, tile_px, tile_px // 2], fill=colour + (255,), width=6)
    buf = io.BytesIO()
    (img if alpha else img.convert("RGB")).save(buf, "PNG")
    return buf.getvalue()


@test("config valid")
def t_config(cfg):
    r = checks.validate_config(cfg)
    assert r["ok"], r["detail"]
    return r["detail"]


@test("grid math round trip + independent anchor tile")
def t_grid(cfg):
    r = checks.grid_roundtrip()
    assert r["ok"], r["detail"]
    return r["detail"]


@test("tile URL builder (element counts)")
def t_url(cfg):
    u = fetch.traffic_url(17, 98451, 56635)
    assert "!1i17!2i98451!3i56635" in u and "!2m3!1e2!2straffic" in u
    ui = fetch.traffic_url(17, 98451, 56635, incidents=True)
    assert "!2m9!1e2!2straffic" in ui and "incidents" in ui
    return "plain 2m3, incidents 2m9"


@test("tile validator accepts good, rejects truncated/opaque/wrong-size/html")
def t_validate(cfg):
    good = synth_tile((242, 60, 50))
    cases = {
        "valid": (good, True), "empty_transparent": (synth_tile((0, 0, 0)) and analyze.png_bytes(Image.new("RGBA", (256, 256), (0, 0, 0, 0))), True),
        "truncated": (good[: len(good) // 2], False), "opaque": (synth_tile((242, 60, 50), alpha=False), False),
        "wrong_size": (analyze.png_bytes(Image.new("RGBA", (128, 128), (0, 0, 0, 0))), False),
        "html": (b"<html>error</html>", False),
    }
    for name, (data, want) in cases.items():
        ok, reason = fetch.validate_tile(data, 256)
        assert ok == want, f"{name}: got {ok} ({reason}), want {want}"
    return f"{len(cases)} cases"


@test("palette classification")
def t_palette(cfg):
    pal, tol = cfg["palette"], cfg["palette_tolerance"]
    refs = analyze.palette_refs(pal)
    rgb, want = [], []
    for i, (_, arr) in enumerate(refs):
        for c in arr:
            rgb.append(c.tolist()); want.append(i)
        if len(arr) > 1:                       # anti-aliased blend of fill and border
            rgb.append(((arr[0] + arr[1]) // 2).tolist()); want.append(i)
    names = [n for n, _ in refs]
    # edge pixels of cased lines: red #d1352b and dark red #a92727 half blended with white casing
    rgb.append([232, 154, 149]); want.append(names.index("red"))
    rgb.append([212, 147, 147]); want.append(names.index("darkred"))
    # grey, white, black and nearly pure casing must not match
    neutrals = [[200, 200, 200], [255, 255, 255], [0, 0, 0], [250, 242, 242]]
    cls = analyze.classify_pixels(np.asarray(rgb + neutrals), pal, tol)
    for k, w in enumerate(want):
        assert cls[k] == w, f"reference {rgb[k]} -> class {cls[k]}, want {w}"
    assert all(c == -1 for c in cls[len(want):]), "neutrals/casing must not match"
    return f"{len(refs)} classes, {len(want)} references+blends+casing edges matched, neutrals rejected"


@test("blocks + audit + georef + file verification, hole and tamper detected")
def t_blocks(cfg):
    z, tp = cfg["zoom"], cfg["tile_px"]
    bbox = {"north": 23.745, "south": 23.735, "east": 90.415, "west": 90.40}
    expected = grid.expected_tiles(bbox, z)
    cols = [tuple(int(v) for v in refs[0]) for _, refs in analyze.palette_refs(cfg["palette"])]
    tiles = {xy: synth_tile(tuple(cols[i % len(cols)]), tp) for i, xy in enumerate(sorted(expected))}
    hole = sorted(expected)[3]
    del tiles[hole]
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        blocks = tiles_mod.build_blocks(tiles, z, expected, out, cfg, tp)
        audit = tiles_mod.audit_tiles(tiles, expected, out, cfg)
        g = checks.georef_check(blocks, z, bbox)
        assert g["ok"], g["detail"]
        v = checks.verify_files(out, blocks)
        assert v["ok"], v["detail"]
        assert sum(b["tiles_present"] for b in blocks) == len(tiles)
        assert any(list(hole) in b["tiles_missing"] for b in blocks), "hole not reported"
        assert audit["tiles_ok"] == len(tiles) and audit["rows"] == len(expected)
        p = out / blocks[0]["file"]
        p.write_bytes(p.read_bytes()[:-10])
        assert not checks.verify_files(out, blocks)["ok"], "truncated file not detected"
    return f"{len(blocks)} blocks, {len(expected)} tiles"


@test("disk space")
def t_disk(cfg):
    r = checks.disk_check(ROOT / "captures", cfg["min_free_gb"])
    assert r["ok"], r["detail"]
    return r["detail"]


@test("network: one live tile over Kakrail validates")
def t_live(cfg):
    from collections import Counter
    st = Counter()
    (_, _), data, reason = fetch.fetch(17, 98451, 56635, fetch.Limiter(4), st, 256)
    assert data, f"no tile: {reason} {dict(st)}"
    img = analyze.load_image(data).convert("RGBA")
    tf = analyze.traffic_fraction(img, cfg["palette"], cfg["palette_tolerance"])
    pm = analyze.palette_match(img, cfg["palette"], cfg["palette_tolerance"])
    if pm["coloured"] >= 200:   # enough traffic pixels to judge: a Google restyle fails here
        assert pm["frac"] >= 0.7, f"palette drift? only {pm['frac']:.0%} of {pm['coloured']} coloured px match"
    return (f"{len(data)} bytes, transparent overlay, traffic frac {tf['traffic_frac']}, "
            f"palette match {pm['frac']:.0%} of {pm['coloured']} coloured px")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", action="store_true")
    args = ap.parse_args()
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    print("TRACK collector self-test")
    for fn in (t_config, t_grid, t_url, t_validate, t_palette, t_blocks, t_disk):
        fn(cfg)
    if args.network:
        t_live(cfg)
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
