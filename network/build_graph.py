"""Build the campus walking graph from OpenStreetMap with elevation.

Usage: network/.venv/bin/python network/build_graph.py FA26

Covers every building with a routable meeting in data/<ROSTER>_meetings.csv,
plus a margin. Downloads (cached in network/cache/, skipped when present):
  OSM walk network via osmnx         cache/osmnx/
  USGS 3DEP 1 m elevation (GeoTIFF)  cache/dem.tif
Adds the hand-drawn paths in network/manual_paths.geojson (drawn in the
editor, network/editor/serve.py) as edges with source="manual", and the doors
in network/doors.geojson (fetch_doors.py) with an estimated way through each
building (source="door" / "indoor_estimate"; see add_doors).

Writes:
  cache/campus_walk.graphml   directed graph; every edge has length (m),
                              climb/descent (m, summed over its OSM segments),
                              highway, surface, wheelchair, ... and
                              source/confidence, so edges from other sources
                              (hand-traced, GPS, CV) can be added with the
                              same fields
  cache/building_nodes.csv    building -> its hub (inside the building) or nearest
                              graph node, and snap distance
  cache/web_graph.json        the same graph for the editor (export_web.py)

Walking speed, stairs and grade limits are not stored here; they belong to
the routing profiles applied on top of this graph.
"""
import argparse
import csv
import json
import math
import time
from pathlib import Path

import networkx as nx
import numpy as np
import osmnx as ox
import rasterio
import rasterio.merge
import requests
import shapely.affinity
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

import export_web

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(__file__).resolve().parent / "cache"
MANUAL_PATHS = Path(__file__).resolve().parent / "manual_paths.geojson"
DOORS = Path(__file__).resolve().parent / "doors.geojson"
FOOTPRINTS = Path(__file__).resolve().parent / "footprints.geojson"
DEM_URL = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"
# The main Overpass server is often at capacity; mirrors serve the same OSM data.
OVERPASS_URLS = ["https://overpass-api.de/api", "https://overpass.private.coffee/api"]

MARGIN_DEG = 0.004   # ~350-450 m around the outermost buildings
DEM_RES_M = 1.0      # 3DEP's native resolution for Tompkins County
DEM_TILE_PX = 1500
# Way tags kept on edges, beyond osmnx's defaults; accessibility profiles use these.
EXTRA_WAY_TAGS = ["surface", "wheelchair", "incline", "step_count", "ramp", "ramp:wheelchair",
                  "footway", "sidewalk", "covered", "indoor", "lit", "width", "tunnel"]
# A node stays a graph node wherever any of these changes, so a flight of steps
# is never merged into the path leading up to it.
SPLIT_ON = ["highway", "surface", "wheelchair", "incline", "footway", "indoor", "tunnel", "source"]
# A drawn point this close to an existing path joins it (the editor snaps points
# exactly onto paths); a drawn path's two ends join the nearest path within END_SNAP_M.
VERTEX_SNAP_M = 2.0
END_SNAP_M = 15.0
# Properties of a drawn path kept on its edges.
MANUAL_TAGS = ["highway", "surface", "wheelchair", "step_count", "name", "note"]
# Doors (Cornell's entrance inventory) left out of the graph: not for people, or
# unusable. The access codes are undocumented; "Inside" is read as exit only.
DOOR_SKIP_TYPES = {"Utility", "Utility Multiple", "Garage"}
DOOR_SKIP_ACCESS = {"Sealed", "Blocked/Unusable", "Unusable/Blocked"}
DOOR_SKIP_LOADING = {"Loading", "Service", "Both"}
DOOR_EXIT_ACCESS = {"Inside", "Inside Night"}
DOOR_CONNECT_M = 25.0   # a door joins the nearest path this close
PIN_FOOTPRINT_M = 15.0  # a building's map pin this close to a footprint routes into it


def routable_buildings(term):
    """{building: (lat, lng)} for buildings with at least one routable meeting."""
    with open(ROOT / "data" / f"{term}_meetings.csv", newline="") as f:
        return {r["building"]: (float(r["lat"]), float(r["lng"]))
                for r in csv.DictReader(f) if r["routable"] == "1"}


def download_graph(bbox):
    """OSM walk network, unsimplified, trying each Overpass server in turn."""
    for url in OVERPASS_URLS:
        ox.settings.overpass_url = url
        try:
            return ox.graph_from_bbox(bbox, network_type="walk", simplify=False)
        except (ox._errors.ResponseStatusCodeError, requests.RequestException) as e:
            print(f"{url} failed ({str(e).splitlines()[0]}); trying the next server")
    raise RuntimeError("every Overpass server failed; try again later")


def download_dem(bbox, path):
    """Fetch 3DEP elevation for bbox (left, bottom, right, top) as one EPSG:4326 GeoTIFF.

    The service times out on large images, so it is fetched in tiles of at most
    DEM_TILE_PX a side (kept in cache/dem_tiles/) and merged.
    """
    if path.exists():
        return
    left, bottom, right, top = bbox
    mid_lat = math.radians((bottom + top) / 2)
    width = (right - left) * 111_320 * math.cos(mid_lat) / DEM_RES_M
    height = (top - bottom) * 110_540 / DEM_RES_M
    nx_, ny = math.ceil(width / DEM_TILE_PX), math.ceil(height / DEM_TILE_PX)
    dx, dy = (right - left) / nx_, (top - bottom) / ny
    tile_dir = path.parent / "dem_tiles"
    tile_dir.mkdir(exist_ok=True)
    tiles = []
    for i in range(nx_):
        for j in range(ny):
            tile = tile_dir / f"{i}_{j}.tif"
            tiles.append(tile)
            if tile.exists():
                continue
            l, b = left + i * dx, bottom + j * dy
            params = {"bbox": f"{l},{b},{l + dx},{b + dy}", "bboxSR": 4326, "imageSR": 4326,
                      "size": f"{round(width / nx_)},{round(height / ny)}", "format": "tiff",
                      "pixelType": "F32", "interpolation": "RSP_BilinearInterpolation", "f": "image"}
            for attempt in range(4):  # the service returns occasional 502/504s
                resp = requests.get(DEM_URL, params=params, timeout=300)
                if resp.ok:
                    break
                time.sleep(5 * 2 ** attempt)
            resp.raise_for_status()
            if not resp.content.startswith((b"II*\x00", b"MM\x00*")):
                raise RuntimeError(f"3DEP did not return a GeoTIFF: {resp.text[:300]}")
            tile.write_bytes(resp.content)
            print(f"  DEM tile {len(tiles)}/{nx_ * ny}")
    sources = [rasterio.open(t) for t in tiles]
    mosaic, transform = rasterio.merge.merge(sources)
    profile = sources[0].profile | {"height": mosaic.shape[1], "width": mosaic.shape[2], "transform": transform}
    for s in sources:
        s.close()
    with rasterio.open(path, "w", **profile) as out:
        out.write(mosaic)
    print(f"wrote {path} ({mosaic.shape[2]}x{mosaic.shape[1]} px)")


class Snapper:
    """Joins points to the graph's segments, in metres around the area's centre.

    A point within snap_m of a segment joins it at the nearest node or at a new
    node splitting the segment; otherwise it becomes a new node of its own.
    """

    def __init__(self, G, bbox):
        self.G = G
        left, bottom, right, top = bbox
        self.kx, self.ky = 111_320 * math.cos(math.radians((bottom + top) / 2)), 110_540  # metres per degree
        self.segs = sorted({(min(u, v), max(u, v)) for u, v in G.edges() if u != v})
        self.arrays = None
        self.next_id = min(min(G.nodes), 0) - 1  # OSM node ids are positive

    def metres(self, lng, lat):
        return np.array([lng * self.kx, lat * self.ky])

    def xy(self, n):
        return self.metres(self.G.nodes[n]["x"], self.G.nodes[n]["y"])

    def dist(self, a, b):
        return float(np.hypot(*(self.xy(a) - self.xy(b))))

    def new_node(self, p, **attrs):
        n = self.next_id
        self.G.add_node(n, x=p[0] / self.kx, y=p[1] / self.ky, **attrs)
        self.next_id -= 1
        return n

    def join(self, p, snap_m, create=True):
        """Graph node for point p: an existing node, a split, or (if create) a new node, else None."""
        G = self.G
        if self.arrays is None:
            self.arrays = np.array([self.xy(u) for u, _ in self.segs]), np.array([self.xy(v) for _, v in self.segs])
        a, b = self.arrays
        ab = b - a
        t = np.clip(((p - a) * ab).sum(1) / np.maximum((ab ** 2).sum(1), 1e-9), 0, 1)
        q = a + t[:, None] * ab
        d = np.hypot(*(p - q).T)
        i = int(d.argmin())
        if d[i] > snap_m:
            return (self.new_node(p) if create else None), False
        u, v = self.segs[i]
        seg_len = float(np.hypot(*ab[i]))
        if t[i] * seg_len <= 1.0:
            return u, True
        if (1 - t[i]) * seg_len <= 1.0:
            return v, True
        n = self.new_node(q[i])
        for s, e in ((u, v), (v, u)):
            for k, data in list(G.get_edge_data(s, e, default={}).items()):
                G.remove_edge(s, e, k)
                G.add_edge(s, n, **(data | {"length": self.dist(s, n)}))
                G.add_edge(n, e, **(data | {"length": self.dist(n, e)}))
        self.segs[i:i + 1] = [(u, n), (n, v)]
        self.arrays = None
        return n, True

    def add_edge(self, u, v, both_ways=True, snap_to=True, **tags):
        """Edge u->v (and v->u). With snap_to, later points can join it."""
        tags.setdefault("oneway", not both_ways)
        self.G.add_edge(u, v, **tags, reversed=False, length=self.dist(u, v))
        if both_ways:
            self.G.add_edge(v, u, **tags, reversed=True, length=self.dist(u, v))
        if snap_to:
            self.segs.append((min(u, v), max(u, v)))
            self.arrays = None


def add_manual_paths(G, snapper, path, bbox):
    """Add the hand-drawn LineStrings in path to the unsimplified graph G.

    Each drawn point within VERTEX_SNAP_M of a path (and each end within
    END_SNAP_M) joins it, at the nearest node or at a new node splitting the
    segment; other points become new nodes. Paths are added in file order, so a
    later path can join an earlier one. Returns (paths added, ends left unjoined).
    """
    if not path.exists():
        return 0, []
    features = json.loads(path.read_text())["features"]
    left, bottom, right, top = bbox
    added, loose = 0, []
    for f in features:
        coords = f["geometry"]["coordinates"]
        props = f.get("properties") or {}
        if not all(left <= x <= right and bottom <= y <= top for x, y in coords):
            print(f"skipping drawn path {props.get('id')}: it leaves the graph's area")
            continue
        nodes = []
        for i, (x, y) in enumerate(coords):
            end = i in (0, len(coords) - 1)
            n, joined = snapper.join(snapper.metres(x, y), END_SNAP_M if end else VERTEX_SNAP_M)
            if end and not joined:
                loose.append((props.get("id"), round(y, 6), round(x, 6)))
            if not nodes or nodes[-1] != n:
                nodes.append(n)
        tags = {k: str(props[k]) for k in MANUAL_TAGS if props.get(k) not in (None, "")}
        tags.setdefault("highway", "footway")
        tags |= {"source": "manual", "manual_id": str(props.get("id"))}
        for u, v in zip(nodes, nodes[1:]):
            snapper.add_edge(u, v, **tags)
        added += 1
    return added, loose


def door_use(p):
    """'in_out', 'out' (exit only), or None for a door people can't use to get in or out."""
    if (p["type"] in DOOR_SKIP_TYPES or p["access_control"] in DOOR_SKIP_ACCESS
            or p["loading_service"] in DOOR_SKIP_LOADING or p["interior_access"] == "No" or not p["fac_code"]):
        return None
    return "out" if p["type"] == "Exit Only" or p["access_control"] in DOOR_EXIT_ACCESS else "in_out"


def add_doors(G, snapper, doors_path, footprints_path, bbox):
    """Add surveyed doors and an estimated way through each building.

    Each usable door becomes a node with a short footway to the nearest path
    within DOOR_CONNECT_M (one way, outwards, for exit-only doors). Inside, each
    building gets one hub node, at its footprint's centroid, joined to every
    door by a straight corridor edge (source "indoor_estimate"), so walking
    through a building costs door -> centre -> door. Corridors and footways
    keep the door's survey fields (door_access, door_type, ...) for routing
    profiles to filter on; nothing here decides which locks students can open.
    Returns {fac_code: hub node}.
    """
    if not doors_path.exists():
        return {}
    left, bottom, right, top = bbox
    footprints = {f["properties"]["fac_code"]: f for f in json.loads(footprints_path.read_text())["features"]}
    by_building, connected, skipped = {}, 0, 0
    for f in json.loads(doors_path.read_text())["features"]:
        p = f["properties"]
        x, y = f["geometry"]["coordinates"]
        use = door_use(p)
        if use is None or not (left <= x <= right and bottom <= y <= top) or p["fac_code"] not in footprints:
            skipped += 1
            continue
        tags = {"door_id": p["door_id"], "door_type": p["type"], "door_access": p["access_control"],
                "door_auto_opener": p["auto_opener"], "door_main": p["primary_"], "door_use": use}
        building = footprints[p["fac_code"]]["properties"]["name"]
        d = snapper.new_node(snapper.metres(x, y), door=p["door_id"], building=building, **tags)
        by_building.setdefault(p["fac_code"], []).append((d, tags))
        n, joined = snapper.join(snapper.metres(x, y), DOOR_CONNECT_M, create=False)
        if joined and n != d:
            # Outside: door -> path (exit only) or both ways; other doors don't snap to it.
            snapper.add_edge(d, n, both_ways=use == "in_out", snap_to=False, highway="footway",
                             source="door", confidence=0.8, **tags)
            connected += 1
    hubs = {}
    for fac, doors in by_building.items():
        poly = shape(footprints[fac]["geometry"])
        c = poly.centroid if poly.contains(poly.centroid) else poly.representative_point()
        hub = snapper.new_node(snapper.metres(c.x, c.y), hub=fac, building=footprints[fac]["properties"]["name"])
        hubs[fac] = hub
        for d, tags in doors:
            snapper.add_edge(hub, d, snap_to=False, highway="corridor", indoor="yes",
                             source="indoor_estimate", confidence=0.5, **tags)
    print(f"added {sum(map(len, by_building.values()))} doors in {len(hubs)} buildings "
          f"({connected} joined to a path within {DOOR_CONNECT_M:.0f} m); skipped {skipped} unusable doors")
    return hubs


def add_climb(G):
    """Per-segment rise, split into climb and descent so simplification can sum them."""
    for u, v, d in G.edges(data=True):
        rise = G.nodes[v]["elevation"] - G.nodes[u]["elevation"]
        d["climb"], d["descent"] = max(rise, 0.0), max(-rise, 0.0)
        d.setdefault("source", "osm")
        d.setdefault("confidence", 1.0)


def snap_buildings(G, buildings, footprints_path):
    """Graph node for each building's map pin (haversine, metres).

    A pin on or within PIN_FOOTPRINT_M of a footprint with a hub routes to that
    hub, the inside of the building (snap 0 m); any other pin to its nearest
    outdoor node. Only nodes that can reach, and be reached from, the rest of the
    graph count, so a building whose only usable doors are exit-only isn't a dead end.
    """
    main = max(nx.strongly_connected_components(G), key=len)
    hubs = {fac: n for n, fac in G.nodes(data="hub") if fac and n in main}
    ids = np.array([n for n, d in G.nodes(data=True) if n in main and not d.get("door") and not d.get("hub")])
    lat = np.radians([G.nodes[n]["y"] for n in ids])
    lng = np.radians([G.nodes[n]["x"] for n in ids])
    footprints, tree = [], None
    if footprints_path.exists() and hubs:
        footprints = [f for f in json.loads(footprints_path.read_text())["features"]
                      if f["properties"]["fac_code"] in hubs]
        kx = 111_320 * math.cos(math.radians(float(np.degrees(lat.mean()))))
        scaled = [shapely.affinity.scale(shape(f["geometry"]), kx, 110_540, origin=(0, 0)) for f in footprints]
        tree = STRtree(scaled)
    rows = []
    for name, (blat, blng) in sorted(buildings.items()):
        i = [] if tree is None else tree.query_nearest(Point(blng * kx, blat * 110_540), max_distance=PIN_FOOTPRINT_M)
        if len(i):
            rows.append({"building": name, "node": int(hubs[footprints[int(i[0])]["properties"]["fac_code"]]),
                         "snap_m": 0.0})
            continue
        blat, blng = math.radians(blat), math.radians(blng)
        h = np.sin((lat - blat) / 2) ** 2 + math.cos(blat) * np.cos(lat) * np.sin((lng - blng) / 2) ** 2
        dist = 12_742_000 * np.arcsin(np.sqrt(h))
        i = int(dist.argmin())
        rows.append({"building": name, "node": int(ids[i]), "snap_m": round(float(dist[i]), 1)})
    return rows


def main(term):
    CACHE.mkdir(exist_ok=True)
    ox.settings.cache_folder = CACHE / "osmnx"
    ox.settings.useful_tags_way = list(dict.fromkeys(ox.settings.useful_tags_way + EXTRA_WAY_TAGS))

    buildings = routable_buildings(term)
    lats = [p[0] for p in buildings.values()]
    lngs = [p[1] for p in buildings.values()]
    bbox = (min(lngs) - MARGIN_DEG, min(lats) - MARGIN_DEG, max(lngs) + MARGIN_DEG, max(lats) + MARGIN_DEG)
    print(f"{len(buildings)} buildings, bbox {tuple(round(x, 4) for x in bbox)}")

    G = download_graph(bbox)
    print(f"OSM walk graph: {len(G)} nodes, {G.number_of_edges()} edges (unsimplified)")
    snapper = Snapper(G, bbox)
    added, loose = add_manual_paths(G, snapper, MANUAL_PATHS, bbox)
    if added:
        print(f"added {added} drawn paths from {MANUAL_PATHS.name}")
    for fid, lat, lng in loose:
        print(f"  drawn path {fid} has an end at {lat}, {lng} that joins no path within {END_SNAP_M:.0f} m")
    add_doors(G, snapper, DOORS, FOOTPRINTS, bbox)

    dem = CACHE / "dem.tif"
    download_dem(bbox, dem)
    G = ox.elevation.add_node_elevations_raster(G, dem, cpus=1)
    missing = [n for n, e in G.nodes(data="elevation") if e is None or not math.isfinite(e)]
    if missing:
        raise RuntimeError(f"{len(missing)} nodes have no elevation; is the DEM covering the bbox?")
    add_climb(G)

    G = ox.simplify_graph(G, edge_attrs_differ=SPLIT_ON,
                          edge_attr_aggs={"length": sum, "climb": sum, "descent": sum})
    out = CACHE / "campus_walk.graphml"
    ox.save_graphml(G, out)

    rows = snap_buildings(G, buildings, FOOTPRINTS)
    with open(CACHE / "building_nodes.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["building", "node", "snap_m"])
        w.writeheader()
        w.writerows(rows)

    elev = [e for _, e in G.nodes(data="elevation")]
    steps = sum(1 for *_, h in G.edges(data="highway") if h == "steps")
    far = [r for r in rows if r["snap_m"] > 75]
    print(f"wrote {out}: {len(G)} nodes, {G.number_of_edges()} edges, {steps} directed step edges, "
          f"elevation {min(elev):.0f}-{max(elev):.0f} m, strongly connected: {nx.is_strongly_connected(G)}")
    print(f"buildings routed inside: {sum(r['snap_m'] == 0 for r in rows)} of {len(rows)}; snapped: median {sorted(r['snap_m'] for r in rows)[len(rows) // 2]} m; "
          f"over 75 m: {[(r['building'], r['snap_m']) for r in far]}")
    export_web.write(G, term, CACHE / "web_graph.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("roster")
    main(ap.parse_args().roster.upper())
