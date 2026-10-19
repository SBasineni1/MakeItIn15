"""Copy the map and the built graph into docs/, which GitHub Pages serves.

Usage: network/.venv/bin/python network/publish.py FA26 [--push]

Rebuilds the graph first if drawn paths changed since the last build. With
--push, commits docs/ and network/manual_paths.geojson (nothing else) and
pushes, so the site at https://<user>.github.io/MakeItIn15/ updates in a
minute or two.
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


def main(term, push):
    if not GRAPH.exists() or PATHS.stat().st_mtime > GRAPH.stat().st_mtime:
        print("drawn paths changed since the last build; rebuilding")
        subprocess.run([sys.executable, str(NETWORK / "build_graph.py"), term], check=True)
    SITE.mkdir(exist_ok=True)
    for src, name in COPIES.items():
        shutil.copyfile(src, SITE / name)
    (SITE / ".nojekyll").touch()  # serve files as they are, without Jekyll
    print(f"copied the map to {SITE.relative_to(ROOT)}/")
    if not push:
        return

    tracked = ["docs", str(PATHS.relative_to(ROOT))]
    git("add", "--", *tracked)
    if git("diff", "--cached", "--quiet", "--", *tracked).returncode == 0:
        print("Nothing new to publish")
        return
    n = PATHS.read_text().count('"LineString"')
    commit = git("commit", "-m", f"Update walking graph ({n} drawn path{'s' if n != 1 else ''})", "--", *tracked)
    if commit.returncode:
        sys.exit(f"git commit failed: {commit.stderr.strip()}")
    pushed = git("push", "origin", "HEAD")
    if pushed.returncode:
        sys.exit(f"Committed but not pushed: {pushed.stderr.strip().splitlines()[-1]} (git pull, then publish again)")
    print("Published; the site updates in a minute or two")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("roster")
    ap.add_argument("--push", action="store_true", help="commit docs/ and the drawn paths, then git push")
    args = ap.parse_args()
    main(args.roster.upper(), args.push)
