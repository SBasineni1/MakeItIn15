"""Scrape meeting rooms from Class Roster subject pages.

Usage: python3 scraper/fetch_rooms.py FA24
       pbpaste > .roster_cookie && python3 scraper/fetch_rooms.py FA26

The API stopped returning rooms starting with Spring 2025, and the public web
pages hide them for logged-out visitors. Rosters through Winter 2025 work
without a cookie. For newer rosters, sign in to classes.cornell.edu with your
NetID, copy the request's Cookie header from browser DevTools, and save it to
.roster_cookie at the repo root (`pbpaste > .roster_cookie`) or pass it in
CORNELL_ROSTER_COOKIE.

Writes data/raw/rooms/<ROSTER>/<SUBJECT>.json: a list of sections, each with
its class number and meetings (pattern, time, building, facility text).
"""
import html
import re
import sys
import urllib.error
import urllib.parse

import roster

SECTION_SPLIT = re.compile(r'<ul class="section[ "]')
CLASS_NBR = re.compile(r'data-class-nbr="(\d+)"')
COMPONENT = re.compile(r'data-ssr-component="(\w+)" data-section="(\w+)"')
COURSE = re.compile(r'<span class="course-repeater">([A-Z]+)\s+(\w+)')
MEETING = re.compile(r'<li class="section-alt section-alt-details dates">(.*?)</li>', re.S)
PATTERN = re.compile(r'<span class="pattern-only">(?:<[^>]+>)*([^<]*)<')
TIME = re.compile(r'<time class="time">\s*([\d:]+[ap]m)\s*-\s*([\d:]+[ap]m)')
FACILITY = re.compile(r'<a class="facility-search" href="([^"]*)"[^>]*>([^<]*)</a>')


def parse_subject_page(page):
    sections = []
    for block in SECTION_SPLIT.split(page)[1:]:
        nbr = CLASS_NBR.search(block)
        if not nbr:
            continue
        comp = COMPONENT.search(block)
        course = COURSE.search(block)
        meetings = []
        for mtg_nbr, m in enumerate(MEETING.findall(block), 1):
            pattern = PATTERN.search(m)
            times = TIME.search(m)
            fac = FACILITY.search(m)
            building = None
            if fac:
                q = urllib.parse.parse_qs(urllib.parse.urlparse(html.unescape(fac.group(1))).query)
                building = q.get("q", [None])[0]
            meetings.append({
                "mtg_nbr": mtg_nbr,
                "pattern": pattern.group(1).strip() if pattern else None,
                "time_start": times.group(1) if times else None,
                "time_end": times.group(2) if times else None,
                "building": building,
                "facility": html.unescape(fac.group(2)).strip() if fac else None,
            })
        sections.append({
            "class_nbr": int(nbr.group(1)),
            "subject": course.group(1) if course else None,
            "catalog_nbr": course.group(2) if course else None,
            "component": comp.group(1) if comp else None,
            "section": comp.group(2) if comp else None,
            "meetings": meetings,
        })
    return sections


def check_login(term, cookie):
    info = roster.get_json(f"{roster.ROSTER_BASE}/browse/roster/{term}/user-data?currentPath=/", cookie)
    status = info.get("login", {}).get("status")
    print(f"logged in: {status}; authRosters: {info.get('authRosters')}")
    if not status:
        sys.exit("Cookie was sent but the roster says you are not logged in. Copy a fresh Cookie header.")


def main(term):
    cookie = roster.cookie_from_env()
    print(f"login cookie: {'found' if cookie else 'none'}")
    if cookie:
        check_login(term, cookie)
    out_dir = roster.RAW_DIR / "rooms" / term
    subs = roster.subjects(term)
    print(f"{term}: {len(subs)} subjects")
    total_mtgs = total_rooms = 0
    for i, subject in enumerate(subs, 1):
        path = out_dir / f"{subject}.json"
        if path.exists():
            continue
        try:
            page = roster.get(f"{roster.ROSTER_BASE}/browse/roster/{term}/subject/{subject}", cookie)
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            page = ""  # subject listed in config but has no browse page
        sections = parse_subject_page(page)
        mtgs = [m for s in sections for m in s["meetings"]]
        rooms = sum(1 for m in mtgs if m["building"])
        total_mtgs += len(mtgs)
        total_rooms += rooms
        # Rooms hidden (not logged in on a restricted roster): stop early instead of
        # writing dozens of empty files that later runs would skip.
        if total_mtgs >= 200 and total_rooms == 0:
            sys.exit(f"No rooms found in {total_mtgs} meetings. {term} hides rooms from logged-out "
                     "visitors and no login cookie was found; save it to .roster_cookie (see README).")
        if total_rooms == 0 and cookie is None:
            continue  # rooms may be hidden; don't cache until we've seen at least one
        roster.write_json(path, sections)
        print(f"  [{i}/{len(subs)}] {subject}: {len(sections)} sections, {rooms}/{len(mtgs)} meetings with rooms")


if __name__ == "__main__":
    main(sys.argv[1].upper())
