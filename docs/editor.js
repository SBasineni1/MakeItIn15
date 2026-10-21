"use strict";
// Path editor: the walking graph over aerial imagery. Drawn paths are saved by
// serve.py to network/manual_paths.geojson, then build_graph.py reruns so the
// graph includes them. On GitHub Pages (publish.py copies it to docs/) there is
// no serve.py: the map is view-only until a repo collaborator signs in with a
// GitHub token, then it commits that file through the GitHub API and
// .github/workflows/build-graph.yml rebuilds the graph and docs/.

const $ = (id) => document.getElementById(id);
const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const COLOR = { flat: token("--m-flat"), mid: token("--m-mid"), high: token("--m-high"),
  steps: token("--m-steps"), drawn: token("--m-drawn"), indoor: token("--m-indoor"), door: token("--m-door") };
const CASING = "#101614";
const SNAP_PX = 12;     // a drawn point this close to a path (on screen) snaps onto it
const END_JOIN_M = 15;  // build_graph.py joins a drawn path's ends to paths this close (END_SNAP_M)
const CELL_M = 25;      // snap index grid
const LIVE = ["localhost", "127.0.0.1"].includes(location.hostname);  // served by serve.py
const REPO = "SBasineni1/MakeItIn15", PATHS_FILE = "network/manual_paths.geojson";
const TOKEN_KEY = "makeitin15.githubToken";
let editable = LIVE;  // on Pages, once a collaborator signs in

// ---------- map ----------
const map = L.map("map", { maxZoom: 21, zoomSnap: 0.5 });
for (const [name, z] of [["net", 410], ["pins", 440], ["drawn", 450]]) map.createPane(name).style.zIndex = z;
// A canvas takes every click over its area, so only the top layer, drawn paths, can be
// clickable; it is SVG, which lets clicks between its lines through to the map.
const R = { net: L.canvas({ pane: "net", padding: 0.5 }), pins: L.canvas({ pane: "pins", padding: 0.5, tolerance: 5 }),
  drawn: L.svg({ pane: "drawn", padding: 0.5 }) };
const bases = {
  "Aerial, Esri": L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
    maxNativeZoom: 19, maxZoom: 21, attribution: "Imagery: Esri, Maxar, Earthstar Geographics" }),
  // Sharper and newer, but slow; serve.py caches each tile it fetches, Pages can't.
  "Aerial, NYS 2022–25 (slow)": LIVE
    ? L.tileLayer("tiles/nys/{z}/{x}/{y}.jpg", { tileSize: 512, zoomOffset: -1, maxNativeZoom: 20, maxZoom: 21,
      attribution: "Imagery: NYS ITS Geospatial Services" })
    : L.tileLayer.wms("https://orthos.its.ny.gov/arcgis/services/wms/Latest/MapServer/WMSServer", {
      layers: "3,2,1,0", format: "image/jpeg", tileSize: 512, maxZoom: 21, attribution: "Imagery: NYS ITS Geospatial Services" }),
  "Street map": L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxNativeZoom: 19, maxZoom: 21, attribution: "© OpenStreetMap contributors" }),
};
bases["Aerial, Esri"].addTo(map);
const netLayer = L.layerGroup().addTo(map), bldLayer = L.layerGroup().addTo(map), drawnLayer = L.layerGroup().addTo(map);
const indoorLayer = L.layerGroup().addTo(map), doorLayer = L.layerGroup().addTo(map);
L.control.layers(bases, { "Path network": netLayer, "Inside buildings (estimated)": indoorLayer, "Doors": doorLayer,
  "Building pins": bldLayer, "Drawn paths": drawnLayer }).addTo(map);
L.control.scale({ imperial: false }).addTo(map);
const snapRing = L.circleMarker([0, 0], { renderer: R.pins, radius: 8, color: "#fff", weight: 2.5, fill: false, interactive: false });

// ---------- graph ----------
let G = null, index = null, origin = null;
const pairs = (g) => { const out = []; for (let i = 0; i < g.length; i += 2) out.push([g[i], g[i + 1]]); return out; };
function edgeClass(e) {
  if (G.attrs[e[5]][0] === "corridor") return "indoor";  // door to building centre, a guess (build_graph.add_doors)
  if (G.attrs[e[5]][0] === "steps") return "steps";
  if (e[2] < 8) return "flat";  // grades on short segments are mostly elevation noise
  const g = Math.max(e[3], e[4]) / e[2];
  return g > 0.0833 ? "high" : g > 0.05 ? "mid" : "flat";
}

// Local metres around the campus centre; plenty accurate for snapping at this scale.
const xy = (lat, lng) => [(lng - origin.lng) * origin.kx, (lat - origin.lat) * 110540];
const toLatLng = (x, y) => L.latLng(origin.lat + y / 110540, origin.lng + x / origin.kx);
const mPerPx = (lat) => 40075016.686 * Math.cos(lat * Math.PI / 180) / 2 ** (map.getZoom() + 8);

async function loadGraph() {
  const r = await fetch("graph.json", { cache: "no-store" });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
  G = await r.json();
  if (!origin) {
    const lat = G.buildings.reduce((s, b) => s + b[1], 0) / G.buildings.length;
    const lng = G.buildings.reduce((s, b) => s + b[2], 0) / G.buildings.length;
    origin = { lat, lng, kx: 111320 * Math.cos(lat * Math.PI / 180) };
  }
  buildIndex(); renderNetwork(); renderPins(); renderDoors();
}

function renderNetwork() {
  netLayer.clearLayers();
  indoorLayer.clearLayers();
  const groups = { flat: [], mid: [], high: [], steps: [], indoor: [] }, all = [];
  for (const e of G.edges) {
    const ll = pairs(e[6]), k = edgeClass(e);
    groups[k].push(ll); if (k !== "indoor") all.push(ll);
  }
  const line = (lls, color, weight, opacity = 1, layer = netLayer, dashArray = null) =>
    L.polyline(lls, { renderer: R.net, color, weight, opacity, dashArray, interactive: false }).addTo(layer);
  line(groups.indoor, COLOR.indoor, 1.5, 0.8, indoorLayer, "4 4");
  line(all, CASING, 4.5, 0.55);
  line(groups.steps, CASING, 6.5, 0.6);
  for (const k of ["flat", "mid", "high"]) line(groups[k], COLOR[k], 2.5, 0.95);
  line(groups.steps, COLOR.steps, 4.5);
}

function renderPins() {
  bldLayer.clearLayers();
  for (const b of G.buildings)
    L.circleMarker([b[1], b[2]], { renderer: R.pins, radius: 5, color: "#fff", weight: 2, fillColor: "#111",
      fillOpacity: 1, bubblingMouseEvents: false })
      .bindTooltip(b[0], { direction: "top", offset: [0, -6] })
      .on("click", (ev) => drawing && addPoint(ev.latlng))
      .addTo(bldLayer);
}

// Surveyed doors in the graph (fetch_doors.py); exit-only ones are red.
function renderDoors() {
  doorLayer.clearLayers();
  for (const [lat, lng, building, type, access, use] of G.doors || [])
    L.circleMarker([lat, lng], { renderer: R.pins, radius: 3, color: CASING, weight: 1,
      fillColor: use === "out" ? COLOR.high : COLOR.door, fillOpacity: 1, bubblingMouseEvents: false })
      .bindTooltip(`${building}: ${type.toLowerCase()} door${use === "out" ? ", exit only" : ""}${access ? ` (${access})` : ""}`,
        { direction: "top", offset: [0, -4] })
      .on("click", (ev) => drawing && addPoint(ev.latlng))
      .addTo(doorLayer);
}

// ---------- snapping ----------
// Every segment of every graph edge's geometry, bucketed into a grid of CELL_M cells.
function buildIndex() {
  const ax = [], ay = [], bx = [], by = [], grid = new Map();
  for (const e of G.edges) {
    const g = e[6];
    for (let j = 0; j + 3 < g.length; j += 2) {
      const [x1, y1] = xy(g[j], g[j + 1]), [x2, y2] = xy(g[j + 2], g[j + 3]), s = ax.length;
      ax.push(x1); ay.push(y1); bx.push(x2); by.push(y2);
      for (let cx = Math.floor(Math.min(x1, x2) / CELL_M); cx <= Math.floor(Math.max(x1, x2) / CELL_M); cx++)
        for (let cy = Math.floor(Math.min(y1, y2) / CELL_M); cy <= Math.floor(Math.max(y1, y2) / CELL_M); cy++) {
          const k = cx + "," + cy; if (!grid.has(k)) grid.set(k, []); grid.get(k).push(s);
        }
    }
  }
  index = { ax, ay, bx, by, grid, seen: new Int32Array(ax.length), stamp: 0 };
}

// Updates best.seg / best.vtx ([distance, x, y]) with segment (x1, y1)-(x2, y2).
function closest(x, y, x1, y1, x2, y2, best) {
  const dx = x2 - x1, dy = y2 - y1, l2 = dx * dx + dy * dy;
  const t = l2 ? Math.max(0, Math.min(1, ((x - x1) * dx + (y - y1) * dy) / l2)) : 0;
  const px = x1 + t * dx, py = y1 + t * dy, d = Math.hypot(x - px, y - py);
  if (d < best.seg[0]) best.seg = [d, px, py];
  for (const [vx, vy] of [[x1, y1], [x2, y2]]) {
    const dv = Math.hypot(x - vx, y - vy); if (dv < best.vtx[0]) best.vtx = [dv, vx, vy];
  }
}

// Nearest graph and drawn-path geometry within r metres, ignoring drawn path `skip`.
function nearest(x, y, r, skip = null) {
  const best = { seg: [Infinity], vtx: [Infinity] };
  if (index) {
    const I = index, stamp = ++I.stamp;
    for (let cx = Math.floor((x - r) / CELL_M); cx <= Math.floor((x + r) / CELL_M); cx++)
      for (let cy = Math.floor((y - r) / CELL_M); cy <= Math.floor((y + r) / CELL_M); cy++)
        for (const s of I.grid.get(cx + "," + cy) || []) {
          if (I.seen[s] === stamp) continue; I.seen[s] = stamp;
          closest(x, y, I.ax[s], I.ay[s], I.bx[s], I.by[s], best);
        }
  }
  for (const f of paths) {
    if (f.properties.id === skip) continue;
    const c = f.geometry.coordinates.map(([lng, lat]) => xy(lat, lng));
    for (let i = 0; i + 1 < c.length; i++) closest(x, y, c[i][0], c[i][1], c[i + 1][0], c[i + 1][1], best);
  }
  return best;
}

// Snap a point onto the nearest path within SNAP_PX, preferring an existing vertex.
function snap(latlng, skip = null) {
  const r = SNAP_PX * mPerPx(latlng.lat), [x, y] = xy(latlng.lat, latlng.lng);
  const n = nearest(x, y, r, skip);
  if (n.vtx[0] <= r && n.vtx[0] <= n.seg[0] + 0.35 * r) return { latlng: toLatLng(n.vtx[1], n.vtx[2]), snapped: true };
  if (n.seg[0] <= r) return { latlng: toLatLng(n.seg[1], n.seg[2]), snapped: true };
  return { latlng, snapped: false };
}
function showRing(s) { if (s.snapped) snapRing.setLatLng(s.latlng).addTo(map); else snapRing.remove(); }

// ---------- drawn paths ----------
let paths = [], pathsModified = 0, selectedId = null, drawing = null, saveTimer = null, saving = false;
const r7 = (v) => Math.round(v * 1e7) / 1e7;
const lls = (f) => f.geometry.coordinates.map(([lng, lat]) => L.latLng(lat, lng));
function endJoined(f, end) {
  const [lng, lat] = f.geometry.coordinates.at(end), [x, y] = xy(lat, lng);
  return nearest(x, y, END_JOIN_M, f.properties.id).seg[0] <= END_JOIN_M;
}

function renderDrawn() {
  drawnLayer.clearLayers();
  for (const f of paths) {
    const on = f.properties.id === selectedId, ll = lls(f);
    const casing = L.polyline(ll, { renderer: R.drawn, color: CASING, weight: on ? 9 : 7, opacity: 0.7, interactive: false }).addTo(drawnLayer);
    const line = L.polyline(ll, { renderer: R.drawn, color: COLOR.drawn, weight: on ? 6 : 4, interactive: editable,
      bubblingMouseEvents: false }).addTo(drawnLayer);
    line.on("click", (ev) => (drawing ? addPoint(ev.latlng) : select(f.properties.id)));
    // A red dot marks an end the graph build will leave unconnected.
    for (const end of [0, -1]) if (!endJoined(f, end))
      L.circleMarker(ll.at(end), { renderer: R.drawn, radius: 6, color: "#fff", weight: 2, fillColor: "#e5322d",
        fillOpacity: 1, interactive: false }).addTo(drawnLayer);
    if (on) addHandles(f, line, casing);
  }
}

const vertexIcon = L.divIcon({ className: "vh", iconSize: [14, 14] });
const midIcon = L.divIcon({ className: "vh mid", iconSize: [10, 10] });
// Drag a vertex to move it, drag a midpoint to add one, Alt-click a vertex to remove it.
function addHandles(f, line, casing) {
  const c = f.geometry.coordinates, id = f.properties.id;
  const moved = (i, latlng) => {
    const s = snap(latlng, id); c[i] = [r7(s.latlng.lng), r7(s.latlng.lat)]; showRing(s);
    line.setLatLngs(lls(f)); casing.setLatLngs(lls(f));
  };
  const done = () => { snapRing.remove(); changed(); };
  c.forEach(([lng, lat], i) => {
    L.marker([lat, lng], { icon: vertexIcon, draggable: true, keyboard: false, zIndexOffset: 1000 }).addTo(drawnLayer)
      .on("drag", (ev) => moved(i, ev.latlng)).on("dragend", done)
      .on("click", (ev) => { if (ev.originalEvent.altKey && c.length > 2) { c.splice(i, 1); changed(); } });
  });
  for (let i = 0; i + 1 < c.length; i++) {
    const mid = [(c[i][0] + c[i + 1][0]) / 2, (c[i][1] + c[i + 1][1]) / 2];
    L.marker([mid[1], mid[0]], { icon: midIcon, draggable: true, keyboard: false, zIndexOffset: 900 }).addTo(drawnLayer)
      .on("dragstart", () => c.splice(i + 1, 0, mid)).on("drag", (ev) => moved(i + 1, ev.latlng)).on("dragend", done);
  }
}

function select(id) { selectedId = id; renderDrawn(); }
function changed() { save(); renderDrawn(); }

function save() {
  clearTimeout(saveTimer); saving = true; setStatus("Saving…");
  // Each GitHub save is a commit and a graph rebuild, so wait for a pause in editing.
  saveTimer = setTimeout(LIVE ? saveLocal : saveGitHub, LIVE ? 250 : 1500);
}
async function saveLocal() {
  try {
    const r = await fetch("api/paths", { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: "FeatureCollection", features: paths }) });
    const j = await r.json();
    if (!r.ok) return setStatus(`Not saved: ${j.error}`, true);
    pathsModified = j.modified; saving = false; rebuild();
  } catch (err) { setStatus("Not saved: is serve.py running?", true); }
}
window.addEventListener("beforeunload", (ev) => { if (saving || rebuilding) ev.preventDefault(); });

// ---------- saving to GitHub (Pages) ----------
const stored = (v) => {  // the token, kept in this browser only
  try { if (v === undefined) return localStorage.getItem(TOKEN_KEY); if (v) localStorage.setItem(TOKEN_KEY, v); else localStorage.removeItem(TOKEN_KEY); }
  catch (err) { return null; }
};
let gh = null;  // { token, sha, base: the paths as last read from or written to GitHub }
let ghBusy = false, ghAgain = false;

async function ghFetch(path, opts = {}) {
  const r = await fetch(`https://api.github.com/repos/${REPO}${path}`, { cache: "no-store", ...opts,
    headers: { Accept: "application/vnd.github+json", Authorization: `Bearer ${gh.token}` } });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(j.message || r.statusText), { status: r.status });
  return j;
}
const fromB64 = (s) => new TextDecoder().decode(Uint8Array.from(atob(s.replace(/\s/g, "")), (c) => c.charCodeAt(0)));
const toB64 = (s) => { let bin = ""; for (const b of new TextEncoder().encode(s)) bin += String.fromCharCode(b); return btoa(bin); };

async function ghRead() {
  const j = await ghFetch(`/contents/${PATHS_FILE}?ref=main`);
  return { sha: j.sha, features: JSON.parse(fromB64(j.content)).features };
}

// The same text serve.py's write_paths writes (Python json.dumps, sort_keys), one path per line.
const pyJSON = (v) => Array.isArray(v) ? "[" + v.map(pyJSON).join(", ") + "]"
  : v && typeof v === "object" ? "{" + Object.keys(v).sort().map((k) => JSON.stringify(k) + ": " + pyJSON(v[k])).join(", ") + "}"
  : JSON.stringify(v);
function pathsText(features) {
  const lines = features.map((f) => pyJSON({ type: "Feature", properties: f.properties || {}, geometry: f.geometry }));
  return '{"type":"FeatureCollection","features":[\n' + lines.join(",\n") + (lines.length ? "\n" : "") + "]}\n";
}

const byId = (fs) => new Map(fs.map((f) => [f.properties.id, f]));
const same = (a, b) => (a && pyJSON(a)) === (b && pyJSON(b));
// Paths changed here since `base` keep this version (or stay deleted); every other path takes theirs.
function merge(base, mine, theirs) {
  const B = byId(base), M = byId(mine), T = byId(theirs);
  const ids = [...T.keys(), ...[...M.keys()].filter((id) => !T.has(id))];
  return ids.map((id) => (same(M.get(id), B.get(id)) ? T.get(id) : M.get(id))).filter(Boolean);
}
function commitMessage(base, mine) {
  const B = byId(base), M = byId(mine), n = { add: 0, edit: 0, delete: 0 };
  for (const [id, f] of M) if (!B.has(id)) n.add++; else if (!same(f, B.get(id))) n.edit++;
  for (const id of B.keys()) if (!M.has(id)) n.delete++;
  const parts = Object.entries(n).filter(([, c]) => c).map(([verb, c]) => `${verb} ${c} path${c > 1 ? "s" : ""}`);
  return `Map: ${parts.join(", ") || "save paths"}`;
}

async function saveGitHub() {
  if (ghBusy) { ghAgain = true; return; }
  ghBusy = true;
  try {
    for (let attempt = 0; ; attempt++) {
      const sent = structuredClone(paths);
      if (pathsText(sent) === pathsText(gh.base)) break;
      try {
        const j = await ghFetch(`/contents/${PATHS_FILE}`, { method: "PUT", body: JSON.stringify({
          message: commitMessage(gh.base, sent), content: toB64(pathsText(sent)), sha: gh.sha, branch: "main" }) });
        gh.sha = j.content.sha; gh.base = sent;
        break;
      } catch (err) {
        if (![409, 422].includes(err.status) || attempt >= 4) throw err;
        // A teammate saved first: put this browser's changes on top of theirs and try again.
        const latest = await ghRead();
        paths = merge(gh.base, paths, latest.features); gh.base = structuredClone(latest.features); gh.sha = latest.sha;
        renderDrawn();
      }
    }
    if (!ghAgain) {
      saving = false;
      setStatus("Saved. The public graph rebuilds in a few minutes."); statusTimer = setTimeout(() => setStatus(""), 4000);
    }
  } catch (err) {
    setStatus(`Not saved: ${err.status === 401 ? "GitHub rejected the token; sign out and in again" : err.message}`, true);
  } finally {
    ghBusy = false;
    if (ghAgain) { ghAgain = false; saveGitHub(); }
  }
}

// Checks the token has write access, then switches the map to editing.
async function signIn(token) {
  gh = { token };
  try {
    const repo = await ghFetch("");
    if (!repo.permissions?.push) throw new Error(`this GitHub account can't push to ${REPO}`);
    const latest = await ghRead();
    paths = latest.features; gh.base = structuredClone(paths); gh.sha = latest.sha;
  } catch (err) {
    gh = null;
    if (err.status === 401) stored(null);
    setStatus(`Not signed in: ${err.status === 401 ? "GitHub rejected the token" : err.message}`, true);
    return false;
  }
  stored(token); editable = true;
  $("signin").hidden = true; $("signout").hidden = $("draw").hidden = false;
  renderDrawn(); setStatus("");
  return true;
}
$("signin").onclick = () => { $("signin").hidden = true; $("tokenform").hidden = false; $("token").focus(); };
$("tokencancel").onclick = () => { $("tokenform").hidden = true; $("signin").hidden = false; };
$("tokenform").onsubmit = async (ev) => {
  ev.preventDefault();
  const token = $("token").value.trim();
  if (!token) return;
  setStatus("Signing in…");
  if (await signIn(token)) { $("tokenform").hidden = true; $("token").value = ""; }
};
$("signout").onclick = () => {
  if (saving) return setStatus("Still saving; sign out once it finishes", true);
  if (drawing) endDraw();
  stored(null); gh = null; editable = false; selectedId = null;
  $("signout").hidden = $("draw").hidden = true; $("signin").hidden = false;
  renderDrawn();
};

// ---------- adding to the graph ----------
let rebuilding = false, again = false, statusTimer = null;
function setStatus(text, warn = false) {
  clearTimeout(statusTimer);
  const s = $("status"); s.textContent = text; s.classList.toggle("warn", warn);
}
async function rebuild() {
  if (rebuilding) { again = true; return; }
  rebuilding = true; setStatus("Adding to graph…");
  try {
    const r = await fetch("api/rebuild", { method: "POST" });
    const j = await r.json();
    if (r.status === 409) again = true;
    else if (!j.ok) { console.error(j.log || j.error); setStatus("Graph build failed (see console)", true); }
    else {
      await loadGraph(); renderDrawn();
      setStatus("Saved and added to graph"); statusTimer = setTimeout(() => setStatus(""), 2500);
    }
  } catch (err) {
    setStatus("Graph build failed: is serve.py running?", true);
  } finally {
    rebuilding = false;
    if (again) { again = false; rebuild(); }
  }
}

// ---------- publishing ----------
$("publish").onclick = async () => {
  const bt = $("publish");
  bt.disabled = true; setStatus("Publishing…");
  try {
    const r = await fetch("api/publish", { method: "POST" });
    const j = await r.json();
    if (!r.ok || !j.ok) { console.error(j.log || j.error); setStatus(`Publish failed: ${(j.log || j.error || "").trim().split("\n").at(-1)}`, true); }
    else setStatus((j.log || "").trim().split("\n").at(-1));
  } catch (err) {
    setStatus("Publish failed: is serve.py running?", true);
  } finally {
    bt.disabled = false;
  }
};

// ---------- drawing ----------
function startDraw() {
  if (drawing) return endDraw();
  select(null);
  document.activeElement.blur();  // so Enter finishes the path instead of pressing the button again
  drawing = {
    pts: [],
    line: L.polyline([], { renderer: R.drawn, color: COLOR.drawn, weight: 4, interactive: false }).addTo(map),
    ghost: L.polyline([], { renderer: R.drawn, color: COLOR.drawn, weight: 2, dashArray: "6 6", interactive: false }).addTo(map),
  };
  map.doubleClickZoom.disable();
  $("map").classList.add("drawing");
  $("draw").setAttribute("aria-pressed", "true");
}
function addPoint(latlng) {
  const s = snap(latlng), last = drawing.pts.at(-1);
  if (last && map.latLngToContainerPoint(last).distanceTo(map.latLngToContainerPoint(s.latlng)) < 6) return finishDraw();
  drawing.pts.push(s.latlng); drawing.line.setLatLngs(drawing.pts); drawing.ghost.setLatLngs([]);
}
function finishDraw() {
  const pts = drawing.pts; endDraw();
  if (pts.length < 2) return;
  const id = "p" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  paths.push({ type: "Feature", properties: { id, highway: "footway", created: new Date().toISOString().slice(0, 10) },
    geometry: { type: "LineString", coordinates: pts.map((p) => [r7(p.lng), r7(p.lat)]) } });
  changed();
}
function endDraw() {
  drawing.line.remove(); drawing.ghost.remove(); snapRing.remove(); drawing = null;
  setTimeout(() => map.doubleClickZoom.enable(), 400);  // the finishing double-click must not zoom
  $("map").classList.remove("drawing");
  $("draw").setAttribute("aria-pressed", "false");
}
$("draw").onclick = startDraw;
map.on("mousemove", (ev) => {
  if (!drawing) return;
  const s = snap(ev.latlng); showRing(s);
  const last = drawing.pts.at(-1); drawing.ghost.setLatLngs(last ? [last, s.latlng] : []);
});
map.on("click", (ev) => { if (drawing) addPoint(ev.latlng); else if (selectedId) select(null); });
document.addEventListener("keydown", (ev) => {
  if (!editable || ev.target.tagName === "INPUT") return;
  if (drawing) {
    if (ev.key === "Enter") { ev.preventDefault(); finishDraw(); }
    else if (ev.key === "Escape") endDraw();
    else if (ev.key === "Backspace") {
      ev.preventDefault(); drawing.pts.pop(); drawing.line.setLatLngs(drawing.pts); drawing.ghost.setLatLngs([]);
    }
    return;
  }
  if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
  if (selectedId && (ev.key === "Delete" || ev.key === "Backspace")) {
    ev.preventDefault(); paths = paths.filter((f) => f.properties.id !== selectedId); selectedId = null; changed();
  } else if (ev.key === "Escape" && selectedId) select(null);
  else if (ev.key === "d") startDraw();
});

// ---------- start ----------
(async () => {
  try {
    await loadGraph();
  } catch (err) {
    return setStatus(`Could not load the graph: ${err.message}`, true);
  }
  map.fitBounds(L.latLngBounds(G.buildings.map((b) => [b[1], b[2]])), { padding: [30, 30] });
  if (!LIVE) {
    // The copy in docs/ lags a save by the graph rebuild; signing in reads the file itself.
    paths = (await (await fetch("manual_paths.geojson", { cache: "no-store" })).json()).features;
    renderDrawn();
    const token = stored();
    if (!token || !(await signIn(token))) $("signin").hidden = false;
    return;
  }
  $("draw").hidden = $("publish").hidden = false;
  const j = await (await fetch("api/paths", { cache: "no-store" })).json();
  paths = j.paths.features; pathsModified = j.modified;
  renderDrawn();
  if (pathsModified > G.built) rebuild();  // paths changed since the graph was last built
})();
