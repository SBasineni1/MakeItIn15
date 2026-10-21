"""Serve the path editor at http://localhost:8015.

Usage: network/.venv/bin/python network/editor/serve.py [FA26] [--port 8015]

The editor shows the walking graph over aerial imagery. Paths drawn in it are
saved to network/manual_paths.geojson and build_graph.py reruns after each
change; its Publish button runs publish.py --push, which pushes that file so
GitHub Actions rebuilds the GitHub Pages copy. Listens on localhost only, since
anyone who can reach it can rewrite that file and push.

NYS orthoimagery has no tile cache and takes seconds per image, so this server
fetches each tile once and keeps it in network/cache/tiles/.
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

EDITOR = Path(__file__).resolve().parent
NETWORK = EDITOR.parent
PATHS = NETWORK / "manual_paths.geojson"
GRAPH = NETWORK / "cache" / "web_graph.json"
BUILD = NETWORK / "build_graph.py"
PUBLISH = NETWORK / "publish.py"
ALLOWED_PROPS = {"id", "highway", "surface", "wheelchair", "step_count", "name", "note", "created"}
TILES = NETWORK / "cache" / "tiles" / "nys"
NYS_WMS = ("https://orthos.its.ny.gov/arcgis/services/wms/Latest/MapServer/WMSServer?service=WMS"
           "&request=GetMap&version=1.1.1&layers=3,2,1,0&styles=&format=image/jpeg&srs=EPSG:3857"
           "&width=512&height=512&bbox={},{},{},{}")  # layers oldest first, so the newest year is on top
TILE_RE = re.compile(r"^/tiles/nys/(\d{1,2})/(\d+)/(\d+)\.jpg$")
rebuild_lock = threading.Lock()


def nys_tile(z, x, y):
    """JPEG bytes for XYZ tile (z, x, y) at 512 px, from disk or from the NYS WMS."""
    path = TILES / str(z) / str(x) / f"{y}.jpg"
    if path.exists():
        return path.read_bytes()
    half = math.pi * 6378137  # Web Mercator half-width, metres
    size = 2 * half / 2 ** z
    bbox = (-half + x * size, half - (y + 1) * size, -half + (x + 1) * size, half - y * size)
    with urlopen(NYS_WMS.format(*bbox), timeout=60) as resp:
        body = resp.read()
    if not body.startswith(b"\xff\xd8"):
        raise ValueError("the NYS server did not return a JPEG")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return body


def validate(fc):
    """Raise ValueError unless fc is a FeatureCollection of plausible drawn paths."""
    if fc.get("type") != "FeatureCollection" or not isinstance(fc.get("features"), list):
        raise ValueError("expected a GeoJSON FeatureCollection")
    for f in fc["features"]:
        geom, props = f.get("geometry") or {}, f.get("properties") or {}
        coords = geom.get("coordinates")
        if geom.get("type") != "LineString" or not isinstance(coords, list) or len(coords) < 2:
            raise ValueError("every feature must be a LineString with at least two points")
        for c in coords:
            if not (isinstance(c, list) and len(c) == 2 and all(isinstance(v, (int, float)) for v in c)
                    and -80 < c[0] < -72 and 40 < c[1] < 46):
                raise ValueError(f"bad coordinate {c!r}")
        if set(props) - ALLOWED_PROPS:
            raise ValueError(f"unknown properties {sorted(set(props) - ALLOWED_PROPS)}")
        if any(not isinstance(v, (str, int, float)) or len(str(v)) > 500 for v in props.values()):
            raise ValueError("property values must be short strings or numbers")


def write_paths(fc):
    """One feature per line, so git diffs show which paths changed."""
    lines = [json.dumps({"type": "Feature", "properties": f.get("properties") or {},
                         "geometry": f["geometry"]}, sort_keys=True) for f in fc["features"]]
    tmp = PATHS.with_suffix(".tmp")
    tmp.write_text('{"type":"FeatureCollection","features":[\n' + ",\n".join(lines) + ("\n" if lines else "") + "]}\n")
    os.replace(tmp, PATHS)


class Handler(SimpleHTTPRequestHandler):
    term = "FA26"

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        if self.path.endswith((".html", ".js", ".css")) or self.path == "/":
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self):
        if m := TILE_RE.match(self.path):
            try:
                body = nys_tile(*map(int, m.groups()))
            except (OSError, ValueError) as e:
                return self.send_error(502, str(e))
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=604800")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/graph.json":
            if not GRAPH.exists():
                return self.send_json({"error": f"run build_graph.py {self.term} first"}, 404)
            body = GRAPH.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/paths":
            fc = json.loads(PATHS.read_text()) if PATHS.exists() else {"type": "FeatureCollection", "features": []}
            self.send_json({"paths": fc, "modified": PATHS.stat().st_mtime if PATHS.exists() else 0})
        elif self.path in ("/", "/index.html", "/editor.js", "/editor.css"):
            super().do_GET()
        else:
            self.send_error(404)

    def do_PUT(self):
        if self.path != "/api/paths":
            return self.send_error(404)
        try:
            fc = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            validate(fc)
        except (ValueError, TypeError, AttributeError) as e:
            return self.send_json({"error": str(e)}, 400)
        write_paths(fc)
        self.send_json({"ok": True, "modified": PATHS.stat().st_mtime})

    def do_POST(self):
        commands = {"/api/rebuild": [BUILD], "/api/publish": [PUBLISH, "--push"]}
        if self.path not in commands:
            return self.send_error(404)
        script, *flags = commands[self.path]
        if not rebuild_lock.acquire(blocking=False):
            return self.send_json({"error": "a rebuild or publish is already running"}, 409)
        try:
            run = subprocess.run([sys.executable, str(script), self.term, *flags], cwd=NETWORK.parent,
                                 capture_output=True, text=True, timeout=900)
        except subprocess.TimeoutExpired:
            return self.send_json({"ok": False, "log": f"{script.name} took over 15 minutes"}, 500)
        finally:
            rebuild_lock.release()
        self.send_json({"ok": run.returncode == 0, "log": (run.stdout + run.stderr)[-4000:]},
                       200 if run.returncode == 0 else 500)

    def log_message(self, fmt, *args):
        if self.command != "GET" or not str(args[1] if len(args) > 1 else "").startswith(("2", "3")):
            super().log_message(fmt, *args)  # saves, rebuilds and errors only


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("roster", nargs="?", default="FA26")
    ap.add_argument("--port", type=int, default=8015)
    args = ap.parse_args()
    Handler.term = args.roster.upper()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, directory=str(EDITOR)))
    print(f"path editor: http://localhost:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
