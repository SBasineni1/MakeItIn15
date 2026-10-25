"""Slide figures for the mid-semester presentation.

Usage: <python with matplotlib, networkx, numpy> analysis/make_figures.py
Reads data/FA26_meetings.csv and docs/graph.json; writes analysis/figures/*.png
(16:9, 2000x1125) and prints the numbers quoted on the slides.

Walking time uses Tobler's hiking function on each edge's climb and descent
(about 5 km/h on the flat, slower uphill and on steep descents). A "15-minute
transition" pairs every class session ending in building A with every session
starting in building B exactly 15 minutes later on the same day; there are no
student schedules, so this counts possible pairs, not students.
"""
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
from matplotlib.collections import LineCollection, PolyCollection
from matplotlib.lines import Line2D

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "figures"
LIMIT_MIN = 15
DETOUR_MIN_M = 150
CORE_RADIUS_M = 2000  # maps leave out buildings farther than this from campus centre

# Reference palette (dataviz skill, light mode); slots 1-3 validated all-pairs
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
CRITICAL = "#d03b3b"
FOOTPRINT = "#e9e8e3"
PATH = "#d6d5ce"

plt.rcParams.update({
    "font.family": ["Helvetica Neue", "Arial", "DejaVu Sans"],
    "font.size": 13,
    "axes.edgecolor": AXIS,
    "axes.labelcolor": INK_2,
    "axes.titlecolor": INK,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "xtick.labelcolor": INK_2,
    "ytick.labelcolor": INK_2,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": False,
    "grid.color": GRID,
    "grid.linewidth": 0.8,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
})


def figure(title, subtitle, source="FA26 Class Roster; campus walking graph"):
    fig = plt.figure(figsize=(13.333, 7.5), dpi=150)
    fig.text(0.05, 0.93, title, fontsize=24, weight="bold", color=INK, va="top")
    fig.text(0.05, 0.865, subtitle, fontsize=14, color=INK_2, va="top")
    fig.text(0.05, 0.03, source, fontsize=10, color=MUTED)
    return fig


SHORT_NAMES = {
    "Breazzano Family Center for Business Education": "Breazzano Center",
    "Imogene Powers Johnson Center for Birds and Biodiversity": "Johnson Center for Birds",
}


def short(name):
    return SHORT_NAMES.get(name, name)


def place_labels(ax, points, fontsize=11):
    """Label (x, y, text, r_px) points, trying right/left/above/below so no label overlaps another or a dot."""
    fig = ax.figure
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    to_px = ax.transData.transform
    taken = []
    for x, y, _, r in points:
        px, py = to_px((x, y))
        taken.append((px - r, py - r, px + r, py + r))
    for x, y, text, r in points:
        px, py = to_px((x, y))
        gap = r + 4
        for dx, dy, ha, va in ((gap, 0, "left", "center"), (-gap, 0, "right", "center"),
                               (0, gap, "center", "bottom"), (0, -gap, "center", "top"),
                               (gap, gap, "left", "bottom"), (-gap, -gap, "right", "top")):
            t = ax.annotate(text, (x, y), xytext=(dx, dy), textcoords="offset pixels", ha=ha, va=va,
                            fontsize=fontsize, color=INK, zorder=7)
            bb = t.get_window_extent(renderer)
            box = (bb.x0 - 2, bb.y0 - 2, bb.x1 + 2, bb.y1 + 2)
            own = (px - r, py - r, px + r, py + r)
            if not any(box[0] < o[2] and o[0] < box[2] and box[1] < o[3] and o[1] < box[3]
                       for o in taken if o != own):
                taken.append(box)
                break
            t.remove()


def save(fig, name):
    fig.savefig(OUT / f"{name}.png")
    plt.close(fig)
    print(f"  wrote figures/{name}.png")


# ---------------------------------------------------------------- load

def load():
    rows = list(csv.DictReader(open(ROOT / "data/FA26_meetings.csv")))
    g = json.load(open(ROOT / "docs/graph.json"))
    return rows, g


def tobler_s(length, climb, descent):
    slope = (climb - descent) / length if length else 0.0
    return length / (6 * math.exp(-3.5 * abs(slope + 0.05)) / 3.6)


def build_graph():
    """The directed walking graph (docs/graph.json merges directions, losing exit-only doors).

    Returns the graph keyed by OSM/graph node id, with length (m) and Tobler
    time (s) per edge, each edge's [(lat, lng), ...] line, and node elevations.
    """
    M = nx.read_graphml(ROOT / "network/cache/campus_walk.graphml")
    xy = {n: (float(d["y"]), float(d["x"])) for n, d in M.nodes(data=True)}
    elev = {n: float(d["elevation"]) for n, d in M.nodes(data=True)}
    G = nx.DiGraph()
    geom = {}
    for u, v, d in M.edges(data=True):
        length = float(d["length"])
        t = tobler_s(length, float(d["climb"]), float(d["descent"]))
        if G.has_edge(u, v) and G[u][v]["time"] <= t:
            continue
        G.add_edge(u, v, length=length, time=t)
        if "geometry" in d:
            pairs = d["geometry"].split("(", 1)[1].rstrip(")").split(",")
            geom[u, v] = [(float(y), float(x)) for x, y in (p.split() for p in pairs)]
        else:
            geom[u, v] = [xy[u], xy[v]]
    return G, geom, elev


def building_nodes():
    """Building -> (graph node, metres from the building's pin to that node)."""
    with open(ROOT / "network/cache/building_nodes.csv", newline="") as f:
        return {r["building"]: (r["node"], float(r["snap_m"])) for r in csv.DictReader(f)}


class Proj:
    """Local equirectangular projection to metres around campus."""

    def __init__(self, lat0, lng0):
        self.lat0, self.lng0 = lat0, lng0
        self.kx = 111_320 * math.cos(math.radians(lat0))

    def __call__(self, lat, lng):
        return (lng - self.lng0) * self.kx, (lat - self.lat0) * 110_540


def walk_tables(G, buildings):
    """Minutes and metres between every pair of buildings, pin to pin (snaps walked on the flat)."""
    T, D = {}, {}
    for a, (na, sa) in buildings.items():
        t = nx.single_source_dijkstra_path_length(G, na, weight="time")
        d = nx.single_source_dijkstra_path_length(G, na, weight="length")
        for b, (nb, sb) in buildings.items():
            if nb in t:
                snap = sa + sb if a != b else 0
                T[a, b] = (t[nb] + tobler_s(snap, 0, 0)) / 60
                D[a, b] = d[nb] + snap
    return T, D


def sessions(routable):
    """(day, minute) -> Counter of building, for class ends and starts."""
    ends, starts = defaultdict(Counter), defaultdict(Counter)
    for r in routable:
        for day in r["days"]:
            ends[day, int(r["end_min"])][r["building"]] += 1
            starts[day, int(r["start_min"])][r["building"]] += 1
    return ends, starts


# ---------------------------------------------------------------- base map

def basemap(ax, g, proj, extent, path_color=PATH, scale_m=500):
    feats = json.load(open(ROOT / "network/footprints.geojson"))["features"]
    polys = []
    for f in feats:
        geom = f["geometry"]
        parts = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for part in parts:
            polys.append([proj(lat, lng) for lng, lat in part[0]])
    ax.add_collection(PolyCollection(polys, facecolor=FOOTPRINT, edgecolor="none", zorder=1))
    segs = [[proj(lat, lng) for lat, lng in zip(c[0::2], c[1::2])]
            for *_, attr, c in g["edges"] if g["attrs"][attr][3] == "osm"]
    ax.add_collection(LineCollection(segs, colors=path_color, linewidths=0.6, zorder=2))
    (x0, x1), (y0, y1) = extent
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal")
    ax.axis("off")
    sx, sy = x0 + 0.03 * (x1 - x0), y0 + 0.04 * (y1 - y0)
    ax.plot([sx, sx + scale_m], [sy, sy], color=INK_2, lw=2, solid_capstyle="butt", zorder=6)
    ax.text(sx + scale_m / 2, sy + 0.015 * (y1 - y0), f"{scale_m} m", ha="center", fontsize=10,
            color=INK_2, zorder=6)


def core_extent(bxy, names=None, pad=150):
    """Bounding box of the given buildings (default all) within CORE_RADIUS_M of campus centre."""
    pts = [bxy[b] for b in (names or bxy)]
    xs = np.array([p[0] for p in pts])
    ys = np.array([p[1] for p in pts])
    cx, cy = np.median(xs), np.median(ys)
    keep = np.hypot(xs - cx, ys - cy) < CORE_RADIUS_M
    return ((xs[keep].min() - pad, xs[keep].max() + pad), (ys[keep].min() - pad, ys[keep].max() + pad))


def fit_extent(extent, aspect):
    """Widen the shorter side so the map fills an axes of width/height = aspect."""
    (x0, x1), (y0, y1) = extent
    w, h = x1 - x0, y1 - y0
    if w / h < aspect:
        grow = (h * aspect - w) / 2
        x0, x1 = x0 - grow, x1 + grow
    else:
        grow = (w / aspect - h) / 2
        y0, y1 = y0 - grow, y1 + grow
    return (x0, x1), (y0, y1)


# ---------------------------------------------------------------- figures

def fig_funnel(rows):
    unique = [r for r in rows if r["is_duplicate"] == "0"]
    in_person = [r for r in unique if r["instruction_mode"] == "P"]
    routable = [r for r in unique if r["routable"] == "1"]
    stages = [
        ("All roster meeting rows", len(rows)),
        ("Unique meetings (cross-listings merged)", len(unique)),
        ("In person", len(in_person)),
        ("Routable: Ithaca, timed, located", len(routable)),
    ]
    real = sum(r["location_source"] == "roster" for r in routable)
    courses = len({(r["subject"], r["catalog_nbr"]) for r in routable})
    subjects = len({r["subject"] for r in routable})
    buildings = len({r["building"] for r in routable})
    print(f"funnel {stages}; routable rooms from roster {real}/{len(routable)}")

    fig = figure("From roster to routable classes",
                 f"{len(routable):,} FA26 class meetings to route: {courses:,} courses, "
                 f"{subjects} subjects, {buildings} buildings")
    ax = fig.add_axes([0.05, 0.14, 0.62, 0.62])
    y = np.arange(len(stages))[::-1]
    ax.barh(y, [n for _, n in stages], height=0.5, color=BLUE, linewidth=0)
    for yi, (label, n) in zip(y, stages):
        ax.text(0, yi + 0.38, label, fontsize=13, color=INK_2, va="bottom")
        ax.text(n + 150, yi, f"{n:,}", fontsize=14, color=INK, va="center", weight="bold")
    ax.set_xlim(0, max(n for _, n in stages) * 1.12)
    ax.set_ylim(-0.6, len(stages) - 0.1)
    ax.axis("off")

    # stat tiles
    tiles = [
        (f"{real / len(routable):.0%}", "of routable rooms come straight\nfrom the logged-in roster"),
        (f"{len(routable) - real:,}", "rooms estimated from FA24\n(same section or course)"),
    ]
    for i, (value, label) in enumerate(tiles):
        top = 0.70 - i * 0.27
        fig.text(0.73, top, value, fontsize=44, weight="bold", color=INK, va="top")
        fig.text(0.73, top - 0.11, label, fontsize=13, color=INK_2, va="top", linespacing=1.4)
    save(fig, "01_data_funnel")


def fig_start_times(routable):
    weekly = Counter()
    for r in routable:
        weekly[int(r["start_min"])] += len(r["days"])
    lo, hi = 7 * 60 + 30, 21 * 60
    fig = figure("Campus moves in lockstep",
                 "Class sessions per week by start time. Most classes begin at a handful of shared times.")
    ax = fig.add_axes([0.07, 0.14, 0.88, 0.64])
    xs = [m for m in weekly if lo <= m <= hi]
    ax.bar(xs, [weekly[m] for m in xs], width=4, color=BLUE, linewidth=0)
    ax.yaxis.grid(True)
    ax.set_axisbelow(True)
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ticks = range(8 * 60, hi + 1, 60)
    ax.set_xticks(list(ticks))
    ax.set_xticklabels([f"{(m // 60 - 1) % 12 + 1}{'am' if m < 720 else 'pm'}" for m in ticks])
    ax.set_xlim(lo - 10, hi + 10)
    ax.set_ylabel("Sessions per week")
    for m, n in weekly.most_common(4):
        ax.text(m, n + 25, f"{(m // 60 - 1) % 12 + 1}:{m % 60:02d}", ha="center", fontsize=12, color=INK)
    print("top start times (weekly sessions)", [(f"{m // 60}:{m % 60:02d}", n) for m, n in weekly.most_common(6)])
    save(fig, "02_start_times")


def fig_gaps(ends, starts):
    gap = Counter()
    for (day, e), A in ends.items():
        na = sum(A.values())
        for g in range(0, 41):
            B = starts.get((day, e + g))
            if B:
                gap[g] += na * sum(B.values())
    total = sum(gap.values())
    print(f"gaps 0-40 min: 15 min = {gap[LIMIT_MIN] / total:.0%} of end->start pairs")
    fig = figure("15 minutes is the passing time",
                 "Minutes between a class ending and another starting the same day (pairs of class sessions, gaps up to 40 min)")
    ax = fig.add_axes([0.07, 0.14, 0.88, 0.64])
    xs = list(range(0, 41))
    ax.bar(xs, [gap[g] / 1000 for g in xs], width=0.8,
           color=[BLUE if g == LIMIT_MIN else "#b7d3f6" for g in xs], linewidth=0)
    ax.text(LIMIT_MIN, gap[LIMIT_MIN] / 1000 * 1.02, f"{gap[LIMIT_MIN] / total:.0%} of pairs",
            ha="center", va="bottom", fontsize=13, color=INK, weight="bold")
    ax.yaxis.grid(True)
    ax.set_axisbelow(True)
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.set_xticks(range(0, 41, 5))
    ax.set_xlabel("Gap between classes (minutes)")
    ax.set_ylabel("Session pairs (thousands)")
    save(fig, "03_passing_gaps")


def fig_buildings(routable):
    counts = Counter(r["building"] for r in routable).most_common(15)
    fig = figure("Where classes meet",
                 f"Routable FA26 class meetings, top 15 of {len({r['building'] for r in routable})} buildings")
    ax = fig.add_axes([0.27, 0.10, 0.66, 0.70])
    y = np.arange(len(counts))[::-1]
    ax.barh(y, [n for _, n in counts], height=0.6, color=BLUE, linewidth=0)
    ax.set_yticks(y)
    ax.set_yticklabels([b for b, _ in counts], fontsize=13)
    ax.tick_params(axis="y", length=0)
    for yi, (_, n) in zip(y, counts):
        ax.text(n + 6, yi, str(n), va="center", fontsize=12, color=INK_2)
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_visible(False)
    ax.set_xticks([])
    save(fig, "04_busiest_buildings")


def fig_building_map(g, proj, bxy, routable):
    counts = Counter(r["building"] for r in routable)
    fig = figure("Classes across campus", "Each dot is a building, sized by its FA26 class meetings")
    ax = fig.add_axes([0.02, 0.07, 0.96, 0.76])
    names = [b for b in bxy if counts[b]]
    basemap(ax, g, proj, fit_extent(core_extent(bxy, names), 0.96 * 13.333 / (0.76 * 7.5)))
    size = {b: counts[b] * 1.6 + 12 for b in names}  # points^2
    ax.scatter([bxy[b][0] for b in names], [bxy[b][1] for b in names], s=[size[b] for b in names],
               color=BLUE, alpha=0.75, edgecolors=SURFACE, linewidths=2, zorder=4)
    px_per_pt = fig.dpi / 72
    place_labels(ax, [(*bxy[b], b, math.sqrt(size[b]) / 2 * px_per_pt) for b, _ in counts.most_common(8)])
    save(fig, "05_building_map")


def fig_network(g, proj, bxy):
    by_source = defaultdict(list)
    for *_, attr, c in g["edges"]:
        by_source[g["attrs"][attr][3]].append([proj(lat, lng) for lat, lng in zip(c[0::2], c[1::2])])
    n_manual = len(json.load(open(ROOT / "network/manual_paths.geojson"))["features"])
    fig = figure("The walking network",
                 f"{len(g['nodes']):,} nodes and {len(g['edges']):,} edges: OpenStreetMap paths, "
                 f"{n_manual} hand-drawn paths, {len(g['doors']):,} doors joined to building interiors")
    ax = fig.add_axes([0.02, 0.07, 0.72, 0.76])
    basemap(ax, g, proj, fit_extent(core_extent(bxy), 0.72 * 13.333 / (0.76 * 7.5)), path_color="#c3c2b7")
    ax.add_collection(LineCollection(by_source["indoor_estimate"], colors=AQUA, linewidths=0.9, zorder=3))
    ax.add_collection(LineCollection(by_source["door"], colors=ORANGE, linewidths=1.2, zorder=3))
    ax.add_collection(LineCollection(by_source["manual"], colors=BLUE, linewidths=2.4, zorder=4))
    items = [
        ("#c3c2b7", "OpenStreetMap paths", len(by_source["osm"])),
        (BLUE, "Hand-drawn paths (team)", len(by_source["manual"])),
        (ORANGE, "Door connectors", len(by_source["door"])),
        (AQUA, "Indoor estimates", len(by_source["indoor_estimate"])),
    ]
    for i, (color, label, n) in enumerate(items):
        y = 0.72 - i * 0.09
        fig.add_artist(Line2D([0.765, 0.795], [y, y], color=color, lw=4, solid_capstyle="round"))
        fig.text(0.81, y + 0.012, label, fontsize=13, color=INK, va="center")
        fig.text(0.81, y - 0.025, f"{n:,} edges", fontsize=11, color=INK_2, va="center")
    fig.text(0.765, 0.33, "Indoor estimates route each door\nto a hub at the building's centre.",
             fontsize=11, color=MUTED, va="top", linespacing=1.4)
    save(fig, "06_walking_network")


def fig_detours(g, G, geom, proj, bxy, buildings, D):
    ratios = []
    for (a, b), walk in D.items():
        if a < b:
            straight = math.dist(bxy[a], bxy[b])
            if straight >= DETOUR_MIN_M:
                ratios.append((walk / straight, walk, straight, a, b))
    ratios.sort(reverse=True)
    vals = [r[0] for r in ratios]
    med, p90 = statistics.median(vals), float(np.percentile(vals, 90))
    print(f"detours: {len(vals)} pairs, median {med:.2f}, p90 {p90:.2f}, worst {ratios[0]}")

    fig = figure("Straight lines lie",
                 f"Walking distance ÷ straight-line distance for {len(vals):,} building pairs at least "
                 f"{DETOUR_MIN_M} m apart. Median {med:.2f}×, 90th percentile {p90:.2f}×")
    ax = fig.add_axes([0.07, 0.14, 0.40, 0.62])
    ax.hist(vals, bins=np.arange(1, 3.6, 0.05), color=BLUE, rwidth=0.85, linewidth=0)
    ax.axvline(med, color=INK_2, lw=1)
    ax.text(med + 0.04, ax.get_ylim()[1] * 0.95, f"median {med:.2f}×", fontsize=12, color=INK, va="top")
    ax.yaxis.grid(True)
    ax.set_axisbelow(True)
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.set_xlabel("Detour ratio")
    ax.set_ylabel("Building pairs")

    ratio, walk, straight, a, b = ratios[0]
    path = nx.shortest_path(G, buildings[a][0], buildings[b][0], weight="length")
    route = []
    for u, v in zip(path, path[1:]):
        route.extend(proj(lat, lng) for lat, lng in geom[u, v])
    xs, ys = zip(*route)
    pad = 120
    mx = fig.add_axes([0.52, 0.08, 0.45, 0.66])
    extent = ((min(xs) - pad, max(xs) + pad), (min(ys) - pad, max(ys) + pad))
    basemap(mx, g, proj, fit_extent(extent, 0.45 * 13.333 / (0.66 * 7.5)), scale_m=100)
    mx.plot(xs, ys, color=CRITICAL, lw=3, solid_capstyle="round", solid_joinstyle="round", zorder=4)
    (ax_, ay_), (bx_, by_) = bxy[a], bxy[b]
    mx.plot([ax_, bx_], [ay_, by_], color=INK_2, lw=1.5, zorder=4)
    mx.scatter([ax_, bx_], [ay_, by_], s=70, color=INK, edgecolors=SURFACE, linewidths=2, zorder=5)
    place_labels(mx, [(ax_, ay_, a, 8), (bx_, by_, b, 8)], fontsize=12)
    fig.text(0.52, 0.775, f"Worst pair: {walk:.0f} m on foot for {straight:.0f} m apart ({ratio:.1f}×)",
             fontsize=13, color=INK)
    mx.legend(handles=[Line2D([], [], color=CRITICAL, lw=3, label="Shortest walk"),
                       Line2D([], [], color=INK_2, lw=1.5, label="Straight line")],
              loc="upper left", frameon=False, fontsize=11, labelcolor=INK)
    save(fig, "07_detours")


def fig_hills(elev_by_node, buildings, T, routable):
    elev = {b: elev_by_node[n] for b, (n, _snap) in buildings.items()}
    counts = Counter(r["building"] for r in routable)
    xs, ys, labels = [], [], []
    for (a, b), t in T.items():
        if a < b and (b, a) in T:
            xs.append(elev[b] - elev[a])
            ys.append(t - T[b, a])
            labels.append((a, b))
    xs, ys = np.array(xs), np.array(ys)
    print(f"hills: max uphill penalty {ys.max():.1f} min; elevation range {min(elev.values()):.0f}-{max(elev.values()):.0f} m")

    fig = figure("Uphill costs you",
                 "Each dot is a pair of buildings: how much longer the walk is going up than coming back down "
                 "(Tobler's hiking function on USGS 1 m elevation)")
    ax = fig.add_axes([0.08, 0.14, 0.86, 0.64])
    ax.axhline(0, color=AXIS, lw=1)
    ax.axvline(0, color=AXIS, lw=1)
    ax.scatter(xs, ys, s=14, color=BLUE, alpha=0.35, linewidths=0)
    ax.yaxis.grid(True)
    ax.xaxis.grid(True)
    ax.set_axisbelow(True)
    ax.set_xlabel("Climb from first building to second (m)")
    ax.set_ylabel("Extra minutes going up vs down")
    # label the biggest uphill penalty between two busy classroom buildings
    busy = [i for i, (a, b) in enumerate(labels) if counts[a] >= 50 and counts[b] >= 50]
    i = max(busy, key=lambda k: abs(ys[k]))
    a, b = labels[i] if ys[i] > 0 else labels[i][::-1]
    x, y = abs(xs[i]), abs(ys[i])
    ax.scatter([x], [y], s=64, color=BLUE, edgecolors=SURFACE, linewidths=2, zorder=5)
    ax.annotate(f"{short(a)} → {short(b)}\nclimbs {x:.0f} m: +{y:.1f} min going up",
                (x, y), xytext=(x - 60, y + 3), textcoords="data",
                ha="right", va="bottom", fontsize=12, color=INK,
                arrowprops=dict(arrowstyle="-", color=INK_2, lw=0.8))
    print(f"hills: labelled {a} -> {b}, climb {x:.0f} m, +{y:.1f} min")
    save(fig, "08_uphill_penalty")


def fig_walk_times(T, flows):
    pair_t = [t for (a, b), t in T.items() if a != b]
    tot = sum(flows.values())
    late = sum(n for ab, n in flows.items() if T[ab] > LIMIT_MIN)
    print(f"walk times: {len(pair_t)} ordered pairs, median {statistics.median(pair_t):.1f} min, "
          f"{sum(t > LIMIT_MIN for t in pair_t) / len(pair_t):.0%} > {LIMIT_MIN}; "
          f"15-min transitions {tot:,}, {late / tot:.1%} too far")

    fig = figure("Can you make it in 15?",
                 f"Walking time for every 15-minute transition between classes: {tot:,} pairs of a session "
                 f"ending and another starting 15 min later")
    ax = fig.add_axes([0.07, 0.14, 0.62, 0.64])
    edges = np.arange(0, 31, 1)
    weights = Counter()
    for ab, n in flows.items():
        weights[min(int(T[ab]), 30)] += n
    xs = edges[:-1]
    hs = [weights[x] / tot * 100 for x in xs]
    ax.bar(xs + 0.5, hs, width=0.85, color=[CRITICAL if x >= LIMIT_MIN else BLUE for x in xs], linewidth=0)
    ax.axvline(LIMIT_MIN, color=INK, lw=1.2)
    ax.text(LIMIT_MIN + 0.3, max(hs) * 0.95, "15-minute passing time", fontsize=12, color=INK, va="top")
    ax.yaxis.grid(True)
    ax.set_axisbelow(True)
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.set_xticks(range(0, 31, 5))
    ax.set_xticklabels([*map(str, range(0, 30, 5)), "30+"])
    ax.set_xlabel("Walking time (minutes)")
    ax.set_ylabel("Share of transitions (%)")

    fig.text(0.74, 0.72, f"{late / tot:.1%}", fontsize=56, weight="bold", color=INK, va="top")
    fig.text(0.74, 0.58, "of 15-minute transitions are\nlonger than 15 minutes on foot",
             fontsize=14, color=INK_2, va="top", linespacing=1.4)
    fig.text(0.74, 0.40, f"Median building-to-building walk:\n{statistics.median(pair_t):.1f} min "
             f"across all {len(pair_t):,} directed pairs", fontsize=12, color=INK_2, va="top", linespacing=1.4)
    fig.text(0.74, 0.25, "Same-building transitions count as 0 min.\nPossible pairs, not real student schedules.",
             fontsize=11, color=MUTED, va="top", linespacing=1.4)
    save(fig, "09_make_it_in_15")


def fig_flow_map(g, proj, bxy, T, flows):
    pairs = defaultdict(lambda: [0, 0.0])  # undirected: [transitions, worse direction minutes]
    for (a, b), n in flows.items():
        if a != b:
            key = tuple(sorted((a, b)))
            pairs[key][0] += n
            pairs[key][1] = max(pairs[key][1], T[a, b])
    top = sorted(pairs.items(), key=lambda kv: -kv[1][0])[:300]
    biggest = top[0][1][0]

    fig = figure("The transitions that don't fit",
                 "Top 300 building pairs by 15-minute transitions; line width = transitions. "
                 f"Red: over {LIMIT_MIN} min in at least one direction")
    ax = fig.add_axes([0.02, 0.07, 0.72, 0.76])
    used = {b for (a, c), _ in top for b in (a, c)}
    basemap(ax, g, proj, fit_extent(core_extent(bxy, used), 0.72 * 13.333 / (0.76 * 7.5)))
    ok = [kv for kv in top if kv[1][1] <= LIMIT_MIN]
    late = [kv for kv in top if kv[1][1] > LIMIT_MIN]
    for group, color, alpha, z in ((ok, "#9ec5f4", 0.8, 3), (late, CRITICAL, 0.9, 4)):
        segs = [[bxy[a], bxy[b]] for (a, b), _ in group]
        widths = [0.5 + 5.5 * n / biggest for _, (n, _t) in group]
        ax.add_collection(LineCollection(segs, colors=color, linewidths=widths, alpha=alpha,
                                         capstyle="round", zorder=z))
    ax.scatter([bxy[b][0] for b in used], [bxy[b][1] for b in used], s=10, color=INK_2, zorder=5)

    for i, (color, label) in enumerate(((CRITICAL, f"Over {LIMIT_MIN} min"), ("#9ec5f4", f"{LIMIT_MIN} min or less"))):
        y = 0.74 - i * 0.06
        fig.add_artist(Line2D([0.765, 0.795], [y, y], color=color, lw=4, solid_capstyle="round"))
        fig.text(0.81, y, label, fontsize=13, color=INK, va="center")
    fig.text(0.765, 0.60, "Busiest red pairs", fontsize=13, weight="bold", color=INK)
    for i, ((a, b), (n, t)) in enumerate(late[:6]):
        fig.text(0.765, 0.555 - i * 0.07, f"{short(a)} ↔ {short(b)}", fontsize=11, color=INK)
        fig.text(0.765, 0.528 - i * 0.07, f"{t:.1f} min, {n:,} transitions", fontsize=10, color=INK_2)
    print("busiest red pairs", [(a, b, n, round(t, 1)) for (a, b), (n, t) in late[:6]])
    save(fig, "10_flow_map")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows, g = load()
    routable = [r for r in rows if r["routable"] == "1"]
    G, geom, elev = build_graph()
    buildings = building_nodes()
    lat0 = statistics.median(b[1] for b in g["buildings"])
    lng0 = statistics.median(b[2] for b in g["buildings"])
    proj = Proj(lat0, lng0)
    bxy = {b: proj(lat, lng) for b, lat, lng, *_ in g["buildings"]}
    T, D = walk_tables(G, buildings)
    ends, starts = sessions(routable)
    flows = Counter()
    for (day, e), A in ends.items():
        B = starts.get((day, e + LIMIT_MIN))
        if B:
            for a, na in A.items():
                for b, nb in B.items():
                    if (a, b) in T:
                        flows[a, b] += na * nb

    fig_funnel(rows)
    fig_start_times(routable)
    fig_gaps(ends, starts)
    fig_buildings(routable)
    fig_building_map(g, proj, bxy, routable)
    fig_network(g, proj, bxy)
    fig_detours(g, G, geom, proj, bxy, buildings, D)
    fig_hills(elev, buildings, T, routable)
    fig_walk_times(T, flows)
    fig_flow_map(g, proj, bxy, T, flows)


if __name__ == "__main__":
    main()
