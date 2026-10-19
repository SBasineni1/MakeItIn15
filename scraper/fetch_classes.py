"""Download every class in a roster from the Class Roster API.

Usage: python3 scraper/fetch_classes.py FA26

Writes one JSON file per subject to data/raw/api/<ROSTER>/<SUBJECT>.json.
Subjects already on disk are skipped, so an interrupted run can be resumed.
"""
import sys

import roster


def main(term):
    out_dir = roster.RAW_DIR / "api" / term
    subs = roster.subjects(term)
    print(f"{term}: {len(subs)} subjects")
    for i, subject in enumerate(subs, 1):
        path = out_dir / f"{subject}.json"
        if path.exists():
            continue
        resp = roster.api("search/classes", roster=term, subject=subject)
        classes = resp.get("data", {}).get("classes", []) if resp.get("status") == "success" else []
        roster.write_json(path, classes)
        print(f"  [{i}/{len(subs)}] {subject}: {len(classes)} classes")


if __name__ == "__main__":
    main(sys.argv[1].upper())
