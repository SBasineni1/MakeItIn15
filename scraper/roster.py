"""Shared HTTP helpers for classes.cornell.edu and the Cornell campus map.

The roster API asks for at most 1 request per second, so every request made
through `get` is spaced at least MIN_INTERVAL seconds from the previous one.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROSTER_BASE = "https://classes.cornell.edu"
API_BASE = f"{ROSTER_BASE}/api/2.0"
MAP_DATA_URL = "https://www.cornell.edu/about/maps/data.cfm?action=getLocations"

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"

MIN_INTERVAL = 1.1
USER_AGENT = "MakeItIn15 student research scraper (walk-time analysis)"

_last_request = 0.0


def get(url, cookie=None):
    global _last_request
    wait = MIN_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    headers = {"User-Agent": USER_AGENT}
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8")
            break
        except OSError as e:
            _last_request = time.monotonic()
            client_error = isinstance(e, urllib.error.HTTPError) and e.code < 500
            if client_error or attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))
    _last_request = time.monotonic()
    return body


def get_json(url, cookie=None):
    return json.loads(get(url, cookie))


def api(method, **params):
    query = urllib.parse.urlencode(params, doseq=True)
    return get_json(f"{API_BASE}/{method}.json?{query}")


def subjects(roster):
    return [s["value"] for s in api("config/subjects", roster=roster)["data"]["subjects"]]


COOKIE_FILE = ROOT / ".roster_cookie"


def cookie_from_env():
    """Login cookie from CORNELL_ROSTER_COOKIE, else from the git-ignored .roster_cookie file."""
    cookie = os.environ.get("CORNELL_ROSTER_COOKIE")
    if not cookie and COOKIE_FILE.exists():
        cookie = COOKIE_FILE.read_text()
    return cookie.strip() or None if cookie else None


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1))


def to_minutes(t):
    """'10:10AM' or '9:05am' -> minutes since midnight."""
    t = t.strip().upper()
    hm, ampm = t[:-2], t[-2:]
    h, m = (int(x) for x in hm.split(":"))
    if ampm == "PM" and h != 12:
        h += 12
    if ampm == "AM" and h == 12:
        h = 0
    return h * 60 + m
