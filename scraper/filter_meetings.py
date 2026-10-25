"""Narrow a term's routable meetings to the undergraduate classes students walk between.

Usage: python3 scraper/filter_meetings.py FA26   # data/FA26_meetings.csv -> data/FA26_meetings_filtered.csv

Starts from the routable rows (in person, Ithaca, timed, located, not a
cross-listed copy, so online and asynchronous sections are already out), then:
  - keeps career UG only (drops GR, GM, LA, VM)
  - drops buildings more than FAR_M from the meeting-weighted centre of campus
    (Lab of Ornithology, sailing and equestrian centres, Guterman)
  - merges meetings the roster lists once per date into one row per pattern.
    PHYS 2207 labs, for one, are six single-day rows per section, which made
    Rockefeller Hall look like the busiest building by far. n_dates counts the
    dates merged into a row; start_dt/end_dt become the first and last.
"""
import csv
import math
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAR_M = 1600  # Muenscher Lab (~1.5 km) and the Vet School stay in; Guterman (~1.7 km) and beyond are off campus


def parse_dt(s):
    return datetime.strptime(s, "%m/%d/%Y")


def main(term):
    src, out = ROOT / "data" / f"{term}_meetings.csv", ROOT / "data" / f"{term}_meetings_filtered.csv"
    with open(src, newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames + ["n_dates"]
        rows = [r for r in reader if r["routable"] == "1"]
    print(f"routable: {len(rows)}")

    rows = [r for r in rows if r["career"] == "UG"]
    print(f"undergraduate: {len(rows)}")

    lat = sum(float(r["lat"]) for r in rows) / len(rows)
    lng = sum(float(r["lng"]) for r in rows) / len(rows)
    kx = 111320 * math.cos(math.radians(lat))
    dist = lambda r: math.hypot((float(r["lat"]) - lat) * 111320, (float(r["lng"]) - lng) * kx)
    far = sorted({r["building"] for r in rows if dist(r) > FAR_M})
    rows = [r for r in rows if dist(r) <= FAR_M]
    print(f"within {FAR_M} m: {len(rows)} (dropped {', '.join(far)})")

    merged = {}
    for r in rows:
        key = (r["class_nbr"], r["days"], r["start_min"], r["end_min"], r["room"])
        if key not in merged:
            merged[key] = dict(r, n_dates=1 if r["start_dt"] == r["end_dt"] else "")
            continue
        m = merged[key]
        m["start_dt"] = min(m["start_dt"], r["start_dt"], key=parse_dt)
        m["end_dt"] = max(m["end_dt"], r["end_dt"], key=parse_dt)
        m["n_dates"] = (m["n_dates"] or 1) + 1
    rows = list(merged.values())
    print(f"after merging per-date rows: {len(rows)}")

    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out}")


if __name__ == "__main__":
    main(sys.argv[1].upper())
