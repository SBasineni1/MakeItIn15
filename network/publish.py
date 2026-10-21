"""Copy the map and the built graph into docs/, which GitHub Pages serves.

Usage: network/.venv/bin/python network/publish.py FA26 [--push]

Rebuilds the graph first if drawn paths changed since the last build.
.github/workflows/build-graph.yml runs this (without --push) whenever
network/manual_paths.geojson changes on main and commits docs/, so the site at
https://<user>.github.io/MakeItIn15/ updates a few minutes after a push.

With --push, it skips docs/ and commits and pushes only the drawn paths, so
GitHub Actions does the rebuild and local docs/ never conflicts with its commits.
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

NETWORK = Path(__file__).resolve().parent
ROOT = NETWORK.parent
SITE = ROOT / "docs"
PATHS = NETWORK / "manual_paths.geojson"
GRAPH = NETWORK / "cache" / "web_graph.json"
COPIES = {
    NETWORK / "editor" / "index.html": "index.html",
    NETWORK / "editor" / "editor.css": "editor.css",
    NETWORK / "editor" / "editor.js": "editor.js",
    GRAPH: "graph.json",
    PATHS: "manual_paths.geojson",
}


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def copy(term):
    if not GRAPH.exists() or PATHS.stat().st_mtime > GRAPH.stat().st_mtime:
        print("drawn paths changed since the last build; rebuilding")
        subprocess.run([sys.executable, str(NETWORK / "build_graph.py"), term], check=True)
    SITE.mkdir(exist_ok=True)
    for src, name in COPIES.items():
        shutil.copyfile(src, SITE / name)
    (SITE / ".nojekyll").touch()  # serve files as they are, without Jekyll
    print(f"copied the map to {SITE.relative_to(ROOT)}/")


def push():
    tracked = str(PATHS.relative_to(ROOT))
    git("add", "--", tracked)
    if git("diff", "--cached", "--quiet", "--", tracked).returncode == 0:
        print("Nothing new to publish")
        return
    n = PATHS.read_text().count('"LineString"')
    commit = git("commit", "-m", f"Update drawn paths ({n} path{'s' if n != 1 else ''})", "--", tracked)
    if commit.returncode:
        sys.exit(f"git commit failed: {commit.stderr.strip()}")
    pushed = git("push", "origin", "HEAD")
    if pushed.returncode:
        sys.exit(f"Committed but not pushed: {pushed.stderr.strip().splitlines()[-1]} (git pull, then publish again)")
    print("Published; GitHub Actions rebuilds the site in a few minutes")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("roster")
    ap.add_argument("--push", action="store_true", help="commit and push only the drawn paths; Actions rebuilds docs/")
    args = ap.parse_args()
    if args.push:
        push()
    else:
        copy(args.roster.upper())
