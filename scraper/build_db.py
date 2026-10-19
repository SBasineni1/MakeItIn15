"""Build a SQLite database of a roster's class meetings with building locations.

Usage: python3 scraper/build_db.py FA26 --reference FA24

Inputs (from the fetch_* scripts):
  data/raw/api/<ROSTER>/*.json      classes, sections, meeting times
  data/raw/rooms/<ROSTER>/*.json    real rooms, if scraped while logged in
  data/raw/rooms/<REFERENCE>/*.json rooms from an older public roster
  data/raw/buildings.json           campus map coordinates

Every meeting's building comes from the first rule that matches, recorded in
meetings.location_source:
  roster       real room for this roster (logged-in scrape)
  ref_exact    reference roster, same course/component/section/days/start time
  ref_section  reference roster, same course/component/section
  ref_course   reference roster, most common building for course/component
  NULL         unknown (new course, online, or no scheduled time)
"""
import argparse
import collections
import csv
import json
import re
import sqlite3
from pathlib import Path

import roster

SCHEMA = """
CREATE TABLE buildings (
  name TEXT PRIMARY KEY, lat REAL, lng REAL, address TEXT, area TEXT
);
CREATE TABLE courses (
  subject TEXT, catalog_nbr TEXT, crse_id INTEGER, title TEXT,
  acad_career TEXT, acad_group TEXT,
  cross_listed TEXT,  -- other codes for the same class, e.g. 'ENGRD 2110'
  PRIMARY KEY (subject, catalog_nbr)
);
CREATE TABLE sections (
  class_nbr INTEGER PRIMARY KEY, subject TEXT, catalog_nbr TEXT,
  component TEXT, section TEXT, instruction_mode TEXT, location TEXT,
  units_min REAL, units_max REAL
);
CREATE TABLE meetings (
  class_nbr INTEGER, mtg_nbr INTEGER, pattern TEXT,
  time_start TEXT, time_end TEXT, start_min INTEGER, end_min INTEGER,
  start_dt TEXT, end_dt TEXT,
  building TEXT, facility TEXT, location_source TEXT,
  instructors TEXT,   -- 'First Last (netid); ...'
  is_duplicate INTEGER DEFAULT 0,  -- 1 = same meeting as a cross-listed row with a lower course code
  PRIMARY KEY (class_nbr, mtg_nbr)
);
CREATE TABLE meeting_days (
  class_nbr INTEGER, mtg_nbr INTEGER, day TEXT
);
CREATE INDEX meeting_days_day ON meeting_days (day);
CREATE VIEW meeting_locations AS
  SELECT s.subject, s.catalog_nbr, s.component, s.section, m.*, b.lat, b.lng
  FROM meetings m
  JOIN sections s USING (class_nbr)
  LEFT JOIN buildings b ON b.name = m.building;
"""


def split_days(pattern):
    days, i = [], 0
    while i < len(pattern):
        if pattern.startswith("Su", i):
            days.append("Su")
            i += 2
        else:
            days.append(pattern[i])
            i += 1
    return days


def start_of(m):
    return roster.to_minutes(m["time_start"]) if m["time_start"] else None


def load_rooms(term):
    """Scraped meetings keyed by position and by days+time, plus the section rows."""
    by_position, by_time, sections = {}, {}, []
    for path in sorted((roster.RAW_DIR / "rooms" / term).glob("*.json")):
        for sec in json.loads(path.read_text()):
            sections.append(sec)
            for m in sec["meetings"]:
                by_position[(sec["class_nbr"], m["mtg_nbr"])] = m
                by_time.setdefault((sec["class_nbr"], m["pattern"] or "", start_of(m)), m)
    return by_position, by_time, sections


def match_real(by_position, by_time, class_nbr, api_mtg, start):
    """The scraped meeting for an API meeting.

    The web page usually lists meetings in the API's classMtgNbr order, but not
    always, so a positional match is only trusted when its days and start time agree.
    """
    pattern = api_mtg["pattern"] or ""
    m = by_position.get((class_nbr, api_mtg["classMtgNbr"]))
    if m and (m["pattern"] or "") == pattern and start_of(m) == start:
        return m
    return by_time.get((class_nbr, pattern, start))


def reference_index(term):
    exact, section, course = {}, {}, collections.defaultdict(collections.Counter)
    for sec in load_rooms(term)[2]:
        key = (sec["subject"], sec["catalog_nbr"], sec["component"])
        for m in sec["meetings"]:
            if not m["building"]:
                continue
            start = start_of(m)
            exact.setdefault(key + (sec["section"], m["pattern"], start), m)
            section.setdefault(key + (sec["section"],), m)
            course[key][(m["building"], m["facility"])] += 1
    course = {k: dict(zip(("building", "facility"), c.most_common(1)[0][0])) for k, c in course.items()}
    return exact, section, course


def building_resolver(map_names):
    """Roster building name -> campus map name, or the roster name if no match.

    Tries scraper/building_aliases.csv first, then a loose match that ignores
    case, punctuation, and parentheticals ("Rhodes Hall (Frank H. T. Rhodes Hall)").
    """
    norm = lambda s: re.sub(r"[^a-z0-9]", "", re.sub(r"\(.*?\)", "", s.lower()))
    with open(Path(__file__).with_name("building_aliases.csv"), newline="") as f:
        aliases = {row["roster_name"]: row["map_name"] for row in csv.DictReader(f)}
    by_norm = {norm(n): n for n in map_names}
    return lambda name: aliases.get(name) or by_norm.get(norm(name), name)


def main(term, reference, out):
    by_position, by_time, _ = load_rooms(term)
    exact, by_section, by_course = reference_index(reference) if reference else ({}, {}, {})

    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    db = sqlite3.connect(out)
    db.executescript(SCHEMA)

    locations = json.loads((roster.RAW_DIR / "buildings.json").read_text())
    for loc in locations:
        db.execute("INSERT OR IGNORE INTO buildings VALUES (?,?,?,?,?)",
                   (loc["Name"], loc["LatLng"]["lat"], loc["LatLng"]["lng"], loc["Address"], loc["Folder"]))
    # Buildings missing from the campus map (e.g. new ones), coordinates entered by hand.
    with open(Path(__file__).with_name("building_coords.csv"), newline="") as f:
        for row in csv.DictReader(f):
            db.execute("INSERT OR REPLACE INTO buildings VALUES (?,?,?,?,?)",
                       (row["name"], float(row["lat"]), float(row["lng"]), row["note"], "manual"))
    resolve = building_resolver(loc["Name"] for loc in locations)

    for path in sorted((roster.RAW_DIR / "api" / term).glob("*.json")):
        for c in json.loads(path.read_text()):
            subj, nbr = c["subject"], c["catalogNbr"]
            cross = sorted({f"{x['subject']} {x['catalogNbr']}" for g in c["enrollGroups"]
                            for x in g["simpleCombinations"]})
            db.execute("INSERT OR IGNORE INTO courses VALUES (?,?,?,?,?,?,?)",
                       (subj, nbr, c["crseId"], c["titleLong"], c["acadCareer"], c["acadGroup"],
                        ", ".join(cross) or None))
            for g in c["enrollGroups"]:
                for s in g["classSections"]:
                    if db.execute("SELECT 1 FROM sections WHERE class_nbr=?", (s["classNbr"],)).fetchone():
                        continue  # same section listed under another enroll group or cross-listing
                    db.execute("INSERT INTO sections VALUES (?,?,?,?,?,?,?,?,?)",
                               (s["classNbr"], subj, nbr, s["ssrComponent"], s["section"],
                                s["instructionMode"], s["location"], g["unitsMinimum"], g["unitsMaximum"]))
                    in_person = s["instructionMode"] == "P" and s["location"] == "ITH"
                    for m in s["meetings"]:
                        start = roster.to_minutes(m["timeStart"]) if m["timeStart"] else None
                        end = roster.to_minutes(m["timeEnd"]) if m["timeEnd"] else None
                        loc, source = None, None
                        real = match_real(by_position, by_time, s["classNbr"], m, start)
                        key = (subj, nbr, s["ssrComponent"])
                        if real and real["building"]:
                            loc, source = real, "roster"
                        elif in_person and start is not None:
                            for source, loc in (("ref_exact", exact.get(key + (s["section"], m["pattern"], start))),
                                                ("ref_section", by_section.get(key + (s["section"],))),
                                                ("ref_course", by_course.get(key))):
                                if loc:
                                    break
                            else:
                                source = None
                        instructors = "; ".join(f"{i['firstName']} {i['lastName']} ({i['netid']})"
                                                for i in m["instructors"])
                        db.execute("INSERT INTO meetings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                                   (s["classNbr"], m["classMtgNbr"], m["pattern"], m["timeStart"], m["timeEnd"],
                                    start, end, m["startDt"], m["endDt"],
                                    resolve(loc["building"]) if loc else None, loc["facility"] if loc else None, source,
                                    instructors or None))
                        for day in split_days(m["pattern"] or ""):
                            db.execute("INSERT INTO meeting_days VALUES (?,?,?)", (s["classNbr"], m["classMtgNbr"], day))
    mark_crosslist_duplicates(db)
    db.commit()
    export_csv(db, roster.DATA_DIR / f"{term}_meetings.csv")
    report(db)


def mark_crosslist_duplicates(db):
    """Flag rows that are the same physical meeting as a cross-listed course's row.

    CS 2110 and ENGRD 2110 are one lecture in one room but separate sections in
    the roster. Rows count as the same meeting when their courses are cross-listed
    and component, days, times, dates, and room match (plus section number when
    the room is unknown, so distinct same-time sections aren't merged). The row
    with the lowest course code is kept; the others get is_duplicate = 1.
    """
    linked = {}
    for subj, nbr, cross in db.execute("SELECT subject, catalog_nbr, cross_listed FROM courses"):
        linked[f"{subj} {nbr}"] = set(cross.split(", ")) if cross else set()
    groups = collections.defaultdict(list)
    for row in db.execute("""SELECT m.class_nbr, m.mtg_nbr, s.subject || ' ' || s.catalog_nbr, s.component, s.section,
                                    m.pattern, m.start_min, m.end_min, m.start_dt, m.end_dt, m.facility
                             FROM meetings m JOIN sections s USING (class_nbr)
                             WHERE m.start_min IS NOT NULL"""):
        class_nbr, mtg_nbr, course, component, section, *when, facility = row
        key = (component, *when, facility, None if facility else section)
        groups[key].append((course, class_nbr, mtg_nbr))
    for rows in groups.values():
        rows.sort()
        for i, (course, class_nbr, mtg_nbr) in enumerate(rows):
            if any(earlier in linked.get(course, ()) or course in linked.get(earlier, ())
                   for earlier, *_ in rows[:i]):
                db.execute("UPDATE meetings SET is_duplicate = 1 WHERE class_nbr = ? AND mtg_nbr = ?",
                           (class_nbr, mtg_nbr))


# A meeting someone walks to on the Ithaca campus: in person, scheduled, located,
# not a cross-listed copy. AgriTech is in Geneva but its sections are tagged ITH.
ROUTABLE = """(s.instruction_mode = 'P' AND s.location = 'ITH' AND m.start_min IS NOT NULL
               AND b.lat IS NOT NULL AND b.area != 'AgriTech' AND m.is_duplicate = 0)"""


def export_csv(db, path):
    """One flat row per meeting: course, section, days/times, room, coordinates."""
    cur = db.execute(f"""
        SELECT s.subject, s.catalog_nbr, c.title, c.acad_group AS college, c.acad_career AS career,
               c.cross_listed, s.component, s.section, s.class_nbr, m.mtg_nbr,
               s.units_min AS credits_min, s.units_max AS credits_max,
               m.pattern AS days, m.time_start, m.time_end, m.start_min, m.end_min,
               m.start_dt, m.end_dt, s.instruction_mode, s.location AS campus,
               m.building, m.facility AS room, m.location_source, b.lat, b.lng,
               b.area AS building_area, b.address AS building_address,
               m.instructors, m.is_duplicate, {ROUTABLE} AS routable
        FROM meetings m
        JOIN sections s USING (class_nbr)
        JOIN courses c ON c.subject = s.subject AND c.catalog_nbr = s.catalog_nbr
        LEFT JOIN buildings b ON b.name = m.building
        ORDER BY s.subject, s.catalog_nbr, s.component, s.section, m.mtg_nbr""")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(col[0] for col in cur.description)
        w.writerows(cur)
    print(f"wrote {path}")


def report(db):
    q = lambda sql: db.execute(sql).fetchall()
    print("courses:", q("SELECT COUNT(*) FROM courses")[0][0],
          "| sections:", q("SELECT COUNT(*) FROM sections")[0][0],
          "| meetings:", q("SELECT COUNT(*) FROM meetings")[0][0])
    print("in-person Ithaca meetings with a time, by location source:")
    for src, n in q("""SELECT COALESCE(location_source,'unknown'), COUNT(*) FROM meetings JOIN sections USING (class_nbr)
                       WHERE instruction_mode='P' AND location='ITH' AND start_min IS NOT NULL
                       GROUP BY 1 ORDER BY 2 DESC"""):
        print(f"  {src:12} {n}")
    missing = q("""SELECT building, COUNT(*) FROM meetings JOIN sections USING (class_nbr)
                   WHERE location = 'ITH' AND building IS NOT NULL AND building != 'Virtual'
                   AND building NOT IN (SELECT name FROM buildings) GROUP BY 1 ORDER BY 2 DESC""")
    print(f"Ithaca buildings without coordinates (add to building_coords.csv): {len(missing)}", missing)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("roster")
    ap.add_argument("--reference", help="older roster with public rooms, e.g. FA24")
    ap.add_argument("--out", help="default data/raw/<ROSTER>.db")
    a = ap.parse_args()
    term = a.roster.upper()
    main(term, a.reference and a.reference.upper(), Path(a.out) if a.out else roster.RAW_DIR / f"{term}.db")
