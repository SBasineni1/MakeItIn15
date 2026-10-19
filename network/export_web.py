"""Write the walking graph as compact JSON for the path editor.

Usage: network/.venv/bin/python network/export_web.py FA26
(build_graph.py runs this itself; run it alone to re-export a saved graph.)

cache/web_graph.json holds:
  nodes      [lat, lng, elevation m]
  edges      [u, v, length m, climb u->v, descent u->v, attr index, [lat, lng, ...]],
             one per walkable pair (the graph has both directions)
  attrs      [highway, surface, wheelchair, source], shared by many edges
  buildings  [name, lat, lng, node, snap m, routable meetings]
  detours    the 25 building pairs (at least 150 m apart) whose walk is
             longest relative to the straight line: [ratio, walk m, straight m, a, b]
"""
import argparse
import csv
import json
import math
import statistics
import time
from collections import Counter
from pathlib import Path

import networkx as nx
import osmnx as ox

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(__file__).resolve().parent / "cache"
DETOUR_MIN_M = 150
DETOURS_SHOWN = 25


def haversine(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 12_742_000 * math.asin(math.sqrt(h))


def write(G, term, out):
    with open(ROOT / "data" / f"{term}_meetings.csv", newline="") as f:
        meetings = [r for r in csv.DictReader(f) if r["routable"] == "1"]
    count = Counter(r["building"] for r in meetings)
    pins = {r["building"]: (float(r["lat"]), float(r["lng"])) for r in meetings}
    with open(CACHE / "building_nodes.csv", newline="") as f:
        snaps = list(csv.DictReader(f))

    ids = list(G.nodes)
    index = {n: i for i, n in enumerate(ids)}
    nodes = [[round(d["y"], 6), round(d["x"], 6), round(d["elevation"], 1)] for _, d in G.nodes(data=True)]
    attrs, attr_index, edges, seen = [], {}, [], set()
    for u, v, d in G.edges(data=True):
        key = (min(u, v), max(u, v), round(d["length"], 1))
        if key in seen:
            continue
        seen.add(key)
        a = tuple(str(d.get(k, "")) for k in ("highway", "surface", "wheelchair", "source"))
        if a not in attr_index:
            attr_index[a] = len(attrs)
            attrs.append(list(a))
        line = d["geometry"].coords if "geometry" in d else [(G.nodes[n]["x"], G.nodes[n]["y"]) for n in (u, v)]
        flat = [c for x, y in line for c in (round(y, 6), round(x, 6))]
        edges.append([index[u], index[v], round(d["length"], 1), round(d["climb"], 1),
                      round(d["descent"], 1), attr_index[a], flat])

    buildings = [[s["building"], *pins[s["building"]], index[int(s["node"])], float(s["snap_m"]),
                  count[s["building"]]] for s in snaps]

    # Walk = pin-to-path snaps plus the shortest path between the snapped nodes.
    pairs = []
    for i, (a, alat, alng, an, asnap, _) in enumerate(buildings):
        lengths = nx.single_source_dijkstra_path_length(G, ids[an], weight="length")
        for b, blat, blng, bn, bsnap, _ in buildings[i + 1:]:
            straight = haversine(alat, alng, blat, blng)
            path = lengths.get(ids[bn])
            if straight >= DETOUR_MIN_M and path is not None:
                walk = asnap + path + bsnap
                pairs.append([round(walk / straight, 2), round(walk), round(straight), a, b])
    ratios = sorted(p[0] for p in pairs)
    pairs.sort(reverse=True)

    out.write_text(json.dumps({
        "term": term,
        "built": time.time(),
        "nodes": nodes, "edges": edges, "attrs": attrs, "buildings": buildings,
        "detours": pairs[:DETOURS_SHOWN],
        "stats": {"pairs": len(pairs), "median": statistics.median(ratios),
                  "p90": ratios[int(len(ratios) * 0.9)]},
    }, separators=(",", ":")))
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB): {len(edges)} edges, "
          f"detour median {statistics.median(ratios):.2f}, top {pairs[0][0]} {pairs[0][3]} - {pairs[0][4]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("roster")
    term = ap.parse_args().roster.upper()
    G = ox.load_graphml(CACHE / "campus_walk.graphml",
                        edge_dtypes={"climb": float, "descent": float, "confidence": float})
    write(G, term, CACHE / "web_graph.json")
