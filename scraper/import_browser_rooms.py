"""Load rooms-<ROSTER>.json from browser_scrape.js into data/raw/rooms/<ROSTER>/.

Usage: python3 scraper/import_browser_rooms.py ~/Downloads/rooms-FA26.json
"""
import json
import re
import sys
from pathlib import Path

import roster


def main(path):
    path = Path(path).expanduser()
    term = re.search(r"rooms-(\w+)", path.name).group(1).upper()
    by_subject = json.loads(path.read_text())
    for subject, sections in by_subject.items():
        roster.write_json(roster.RAW_DIR / "rooms" / term / f"{subject}.json", sections)
    mtgs = [m for secs in by_subject.values() for s in secs for m in s["meetings"]]
    print(f"{term}: {len(by_subject)} subjects, {sum(1 for m in mtgs if m['building'])}/{len(mtgs)} meetings with rooms")


if __name__ == "__main__":
    main(sys.argv[1])
