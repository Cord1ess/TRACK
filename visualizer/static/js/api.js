/* Where the data comes from.

   Locally the dev server answers /api/... live. On GitHub Pages there is no
   server: build_site.py writes the same answers into data/manifest.json and
   marks the page with <meta name="track-static">, so the app reads that file
   instead. The rest of the app calls apiJson() either way and never has to
   know which of the two it is talking to. */

export const STATIC = !!document.querySelector('meta[name="track-static"]');
export const SITE = location.origin + location.pathname.replace(/[^/]*$/, "");

let manifest = null;
export const getManifest = () => manifest;

export async function loadManifest() {
  try {
    const r = await fetch(`${SITE}data/manifest.json?t=${Date.now()}`, { cache: "no-store" });
    if (r.ok) manifest = await r.json();
  } catch { /* keep the last one */ }
  return manifest;
}

export async function apiJson(path) {
  if (!STATIC) {
    const r = await fetch(path, { cache: "no-store" });
    if (!r.ok) throw new Error(`server answered ${r.status}`);
    return await r.json();
  }
  // the model and graph carry a content version, so their answers are re-read
  // from the manifest every time; the rest can come from the copy we hold
  const live = path === "/api/model" || path === "/api/graph";
  const m = live ? (await loadManifest()) : (manifest || await loadManifest());
  if (!m) throw new Error("data unreachable");
  const answers = { "/api/config": { min_zoom: m.min_zoom }, "/api/captures": m.captures,
                    "/api/model": m.model, "/api/graph": m.graph, "/api/layers": m.layers };
  if (!(path in answers)) throw new Error(`no static answer for ${path}`);
  return answers[path];
}
