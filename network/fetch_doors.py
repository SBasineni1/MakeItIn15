"""Download building doors and footprints from Cornell Facilities' GIS.

Usage: network/.venv/bin/python network/fetch_doors.py FA26

Sources (public ArcGIS feature services at gis.fcs.cornell.edu, no login):
  Production/EntranceInventory_2025  every surveyed exterior door: type,
                                     access control, auto opener, main entrance
  Production/Buildings               building footprints, with facility code and name

Writes, for the area build_graph.py covers (both are committed, so graph
builds don't depend on that server):
  doors.geojson       one door per line, with the facility code of the building
                      it belongs to (the footprint it is on or nearest, within
                      DOOR_BUILDING_M), or none
  footprints.geojson  footprints of the buildings that have doors

build_graph.py turns these into graph edges; see add_doors there.
"""
import argparse
import json
import math
from pathlib import Path

import requests
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

import build_graph

NETWORK = Path(__file__).resolve().parent
GIS = "https://gis.fcs.cornell.edu/arcgis/rest/services/Production"
DOORS_URL = f"{GIS}/EntranceInventory_2025/FeatureServer/0/query"
BUILDINGS_URL = f"{GIS}/Buildings/FeatureServer/0/query"
DOOR_FIELDS = ["door_id", "type", "access_control", "auto_opener", "primary_", "interior_access", "loading_service"]
DOOR_BUILDING_M = 6.0  # doors are surveyed on the building outline, give or take a few metres
PAGE = 2000            # the services' maxRecordCount


def query(url, bbox, fields):
    """Every feature in bbox (left, bottom, right, top) as GeoJSON, a page at a time."""
    features = []
    while True:
        resp = requests.get(url, timeout=120, params={
            "where": "1=1", "geometry": ",".join(map(str, bbox)), "geometryType": "esriGeometryEnvelope",
            "inSR": 4326, "outSR": 4326, "outFields": ",".join(fields), "orderByFields": "OBJECTID",
            "resultOffset": len(features), "resultRecordCount": PAGE, "f": "geojson"})
        resp.raise_for_status()
        page = resp.json()
        if "error" in page:
            raise RuntimeError(f"{url}: {page['error']}")
        features += page["features"]
        if len(page["features"]) < PAGE:
            return features


def rounded(coords):
    """Coordinates to 7 decimals (~1 cm), nested however deep."""
    if isinstance(coords[0], (int, float)):
        return [round(c, 7) for c in coords]
    return [rounded(c) for c in coords]


def write_lines(path, features):
    """One feature per line, like manual_paths.geojson, so git diffs stay readable."""
    lines = [json.dumps(f, sort_keys=True) for f in features]
    path.write_text('{"type":"FeatureCollection","features":[\n' + ",\n".join(lines) + "\n]}\n")


def main(term):
    buildings = build_graph.routable_buildings(term)
    lats = [p[0] for p in buildings.values()]
    lngs = [p[1] for p in buildings.values()]
    m = build_graph.MARGIN_DEG
    bbox = (min(lngs) - m, min(lats) - m, max(lngs) + m, max(lats) + m)

    doors = query(DOORS_URL, bbox, DOOR_FIELDS)
    footprints = [f for f in query(BUILDINGS_URL, bbox, ["fac_code", "facil_name"])
                  if f["geometry"] and f["properties"]["fac_code"]]
    print(f"{len(doors)} doors, {len(footprints)} building footprints")

    # Match in local metres around the area's centre.
    lat0 = math.radians((bbox[1] + bbox[3]) / 2)
    kx, ky = 111_320 * math.cos(lat0), 110_540

    def local(coords):
        if isinstance(coords[0], (int, float)):
            return [coords[0] * kx, coords[1] * ky]
        return [local(c) for c in coords]

    shapes = [shape({"type": f["geometry"]["type"], "coordinates": local(f["geometry"]["coordinates"])})
              for f in footprints]
    tree = STRtree(shapes)
    used, out, unmatched = set(), [], 0
    for f in doors:
        lng, lat = f["geometry"]["coordinates"][:2]
        i = tree.query_nearest(Point(lng * kx, lat * ky), max_distance=DOOR_BUILDING_M)
        fac = footprints[int(i[0])]["properties"]["fac_code"] if len(i) else None
        unmatched += fac is None
        used.add(fac)
        props = {k: (str(f["properties"].get(k) or "").strip()) for k in DOOR_FIELDS}
        out.append({"type": "Feature", "properties": props | {"fac_code": fac},
                    "geometry": {"type": "Point", "coordinates": rounded([lng, lat])}})
    write_lines(NETWORK / "doors.geojson", out)
    kept = [{"type": "Feature", "properties": {"fac_code": f["properties"]["fac_code"],
                                               "name": f["properties"]["facil_name"]},
             "geometry": {"type": f["geometry"]["type"], "coordinates": rounded(f["geometry"]["coordinates"])}}
            for f in footprints if f["properties"]["fac_code"] in used]
    write_lines(NETWORK / "footprints.geojson", kept)
    print(f"wrote doors.geojson ({len(out)} doors, {unmatched} on no building) "
          f"and footprints.geojson ({len(kept)} buildings)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("roster")
    main(ap.parse_args().roster.upper())
