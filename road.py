"""The real roads the shuttle actually drives.

--------------------------------------------------------------------------
WHY THIS EXISTS
--------------------------------------------------------------------------
route.py used to assume the bus travels in a straight line from one stop to the
next, then multiplied every distance by 1.35 to make up for the fact that it
doesn't. One fudge factor, applied to the whole loop.

But the loop isn't uniform. Horseshoe Gate to Monroe Street is a short hop on
campus roads that bends very little. Hood Seminary out to the Marriott runs
along Jake Alexander at highway speed and bends a lot. A single multiplier
can't be right for both, and a single average speed can't either.

So instead of guessing, we ask.

--------------------------------------------------------------------------
WHAT IT DOES
--------------------------------------------------------------------------
OSRM is a free public routing engine built on OpenStreetMap. We hand it two
stops and it answers "here is how you drive between them":

    * the shape of the road, as a few hundred GPS points
    * the real driving distance in meters
    * the real driving time in seconds - which already accounts for speed
      limits, turns, and junctions

We do that once for each of the nine legs, stitch the answers into one loop,
and write the whole thing to route_geometry.json.

--------------------------------------------------------------------------
WHY IT'S CACHED
--------------------------------------------------------------------------
Roads don't move. There is no reason to ask the same question every time a
student opens the map, and three very good reasons not to:

    it would be slow          - a network round trip inside every request
    it would be fragile       - the map breaks whenever OSRM is down
    it would be rude          - that's a free server somebody else pays for

So we ask once and keep the answer. After the first run this file never touches
the network, and the app works fine on a laptop with the wifi switched off.

If you change a stop's coordinates, the cache notices (the signature stops
matching) and refetches on the next start. You can also force it by hand:

    python road.py
"""

import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path

# The public OSRM demo server. No key, no signup, no bill.
OSRM_URL = "https://router.project-osrm.org/route/v1/driving"
USER_AGENT = "BearTracker/1.0 (Livingstone College Academic Shuttle)"
TIMEOUT_SECONDS = 20

CACHE_FILE = Path(__file__).parent / "route_geometry.json"
CACHE_VERSION = 1

# Road points are stored to 5 decimal places, which is a bit over a meter.
# Anything finer is GPS noise and just makes the file bigger.
COORD_PRECISION = 5


class RoadDataUnavailable(Exception):
    """We couldn't get real road data, so the caller should fall back."""


def _signature(stops):
    """A short fingerprint of the route.

    If anybody edits a stop's coordinates, this changes, and the cached geometry
    - which describes the OLD route - is correctly treated as stale. Without
    this you'd move a stop, see nothing happen, and spend an afternoon
    wondering why.
    """
    raw = ";".join(f"{s['id']}:{s['lat']:.5f},{s['lng']:.5f}" for s in stops)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _fetch_leg(start, end):
    """Ask OSRM how you drive from one stop to the next."""
    coords = f"{start['lng']},{start['lat']};{end['lng']},{end['lat']}"
    url = f"{OSRM_URL}/{coords}?overview=full&geometries=geojson"

    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, ValueError) as err:
        raise RoadDataUnavailable(f"couldn't reach the routing server ({err})") from err

    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise RoadDataUnavailable(
            f"no route from {start['id']} to {end['id']} ({payload.get('code', 'unknown')})"
        )

    best = payload["routes"][0]

    # OSRM writes coordinates longitude-first. Everywhere else in this project
    # they're latitude-first, so flip them here, once, at the edge - rather than
    # remembering to flip them at every use site later.
    points = [
        [round(lat, COORD_PRECISION), round(lng, COORD_PRECISION)]
        for lng, lat in best["geometry"]["coordinates"]
    ]

    return {
        "from": start["id"],
        "to": end["id"],
        "distance": round(best["distance"], 1),   # meters of real pavement
        "duration": round(best["duration"], 1),   # seconds of real driving
        "points": points,
    }


def fetch(stops, quiet=False):
    """Route every leg of the loop and save the result. Hits the network."""
    legs = []
    for i, stop in enumerate(stops):
        nxt = stops[(i + 1) % len(stops)]   # the last stop routes back to the first
        if not quiet:
            print(f"  routing {stop['id']} -> {nxt['id']} ...")
        legs.append(_fetch_leg(stop, nxt))

    data = {"version": CACHE_VERSION, "signature": _signature(stops), "legs": legs}

    try:
        CACHE_FILE.write_text(json.dumps(data))
    except OSError as err:
        # Worth saying out loud - it means we'll re-hit the network next start -
        # but not worth failing over, since the data in hand is still good.
        print(f"  (couldn't save {CACHE_FILE.name}: {err})")

    return data


def load(stops):
    """Read the cached geometry, or None if there isn't a usable one."""
    if not CACHE_FILE.exists():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text())
    except (json.JSONDecodeError, OSError) as err:
        print(f"  (couldn't read {CACHE_FILE.name}: {err})")
        return None

    if data.get("version") != CACHE_VERSION:
        return None
    if data.get("signature") != _signature(stops):
        print("  (the route changed since this geometry was saved)")
        return None
    if len(data.get("legs", [])) != len(stops):
        return None

    return data


def get(stops):
    """The real road geometry for the loop, or None if we have to do without.

    Cache first, network second, shrug third. Returning None is a normal
    outcome, not a crash: route.py falls back to straight lines and the app
    still runs, just with rougher numbers.
    """
    cached = load(stops)
    if cached:
        return cached

    print("Fetching real road geometry (first run - this happens once):")
    try:
        data = fetch(stops)
    except RoadDataUnavailable as err:
        print(f"  {err}")
        print("  Falling back to straight-line estimates. Run `python road.py` to retry.")
        return None

    print(f"  saved to {CACHE_FILE.name}")
    return data


if __name__ == "__main__":
    # Imported here rather than at the top because route.py imports THIS module,
    # and two modules that import each other at load time deadlock.
    from route import STOPS

    print("Refetching road geometry for all legs:")
    result = fetch(STOPS)
    total_km = sum(leg["distance"] for leg in result["legs"]) / 1000
    total_min = sum(leg["duration"] for leg in result["legs"]) / 60
    points = sum(len(leg["points"]) for leg in result["legs"])
    print(f"\n  {len(result['legs'])} legs, {points} road points")
    print(f"  {total_km:.2f} km of pavement, {total_min:.1f} minutes of driving")
    print(f"  written to {CACHE_FILE.name}")
