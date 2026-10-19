# MakeItIn15-
Visualizing Class Schedules Across Cornell

## Data

`scraper/` builds a SQLite database of every class meeting in a term, with the
building it meets in and that building's coordinates. Python 3 standard
library only; all requests are spaced 1.1 s apart per the Class Roster API's
1 request/second limit.

| Source | What it gives |
| --- | --- |
| Class Roster API (`classes.cornell.edu/api/2.0`) | courses, sections, days, times |
| Class Roster subject pages | building + room (public through Winter 2025; login required from Spring 2025) |
| Campus map (`cornell.edu/about/maps/data.cfm?action=getLocations`) | building lat/lng |

```sh
python3 scraper/fetch_buildings.py
python3 scraper/fetch_classes.py FA26
python3 scraper/fetch_rooms.py FA24           # public rooms, used as the estimate source
python3 scraper/build_db.py FA26 --reference FA24   # -> data/FA26_meetings.csv
```

### Real rooms for the current term

Rooms for Spring 2025 onward are only shown to logged-in users. Sign in at
classes.cornell.edu with your NetID, open DevTools → Network, click any
request to classes.cornell.edu (e.g. `user-data`), right-click the `Cookie`
request header → Copy value, then:

```sh
pbpaste > .roster_cookie        # git-ignored; delete it when done
python3 scraper/fetch_rooms.py FA26
python3 scraper/build_db.py FA26 --reference FA24
```

Or skip the cookie: while signed in, paste `scraper/browser_scrape.js` into the
DevTools console on the roster page. It downloads `rooms-FA26.json`; then run
`python3 scraper/import_browser_rooms.py ~/Downloads/rooms-FA26.json` and
rebuild the database.

Building names are matched to the campus map automatically, via
`scraper/building_aliases.csv`, or, for buildings not on the map (e.g. new
ones), via hand-entered coordinates in `scraper/building_coords.csv`.
`build_db.py` lists any Ithaca buildings still missing coordinates.

`meetings.location_source` records where each building came from: `roster`
(real), `ref_exact` / `ref_section` / `ref_course` (estimated from the
reference term, most to least specific), or NULL.

### Outputs

- `data/FA26_meetings.csv` — one flat row per meeting: course code, title,
  college, career, cross-listings, component/section, credits, days, times,
  dates, building, room, lat/lng, building area and address, instructors,
  `location_source`, `is_duplicate` (1 = same meeting as a cross-listed row,
  e.g. ENGRD 2110 for CS 2110; filter `is_duplicate = 0` when counting
  classes), and `routable` (1 = an in-person Ithaca meeting with a time and
  coordinates, not a duplicate and not at AgriTech in Geneva; filter
  `routable = 1` for walking analysis)
- `data/raw/FA26.db` — the same data as SQLite tables, below (git-ignored,
  rebuilt with the CSV)

### Tables

- `meetings` — one row per meeting pattern: days, `start_min`/`end_min` (minutes after midnight), building, room
- `meeting_days` — one row per (meeting, day) for "what's on Tuesday" queries
- `sections`, `courses`, `buildings`
- `meeting_locations` — view joining meetings to course codes and coordinates
