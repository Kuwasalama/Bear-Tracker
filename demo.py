"""FAKE driver. Pretends to be a phone in a bus driving the real route.

This is only so you can watch the map work before you've set up a real phone.
It is not part of the app - nothing in app.py knows this file exists. It just
posts to the same door the driver's phone posts to.

    python demo.py        (with `python app.py` already running in another window)

It used to drive a 350m circle near campus, which was fine when the route was
nine straight lines. It isn't any more: the server now matches the bus to the
nearest real road, so a bus driving through the middle of campus gets snapped
to whatever road it happens to be closest to and every ETA comes out nonsense.

So this drives the actual loop - along the same road geometry the server
measures against, at the pace the road data says each road takes, stopping to
board at each stop. Which means the numbers you see on the map while this runs
are the numbers you'd see from a real bus doing everything right.
"""

import json
import math
import time
import urllib.request

import route

STEP_SECONDS = 2        # how often a real phone would report in
SPEED_FACTOR = 6.0      # drive the loop this many times faster than real life
BOARDING_SECONDS = 4    # a token pause at each stop, sped up like everything else

along = 0.0             # how far round the loop the fake bus has driven
next_stop_index = 1     # the stop it's heading for, so we know when to pause


def leg_speed(position):
    """How fast the road the bus is on is supposed to be driven, in m/s."""
    leg = route.STOPS[route._leg_index(position)]
    if leg["leg_seconds"] <= 0:
        return 10.0
    return leg["leg_to_next"] / leg["leg_seconds"]


def post(lat, lng, heading):
    body = json.dumps({"lat": lat, "lng": lng, "heading": heading}).encode()
    request = urllib.request.Request(
        "http://localhost:5000/api/location",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(request, timeout=5).read()
    except Exception as err:
        print(f"  couldn't reach the server: {err}")


print(f"Fake shuttle driving the real {route.LOOP_LENGTH / 1000:.1f} km loop "
      f"at {SPEED_FACTOR:.0f}x speed.")
print("Open http://localhost:5000  (Ctrl+C to stop)")

while True:
    lat, lng = route.point_at(along)

    # Face whichever way the next bit of road goes.
    ahead_lat, ahead_lng = route.point_at(along + 25)
    heading = math.degrees(math.atan2(
        (ahead_lng - lng) * math.cos(math.radians(lat)),
        ahead_lat - lat,
    )) % 360

    post(lat, lng, heading)

    # Pull in and board if we've reached the stop we were heading for.
    target = route.STOPS[next_stop_index % len(route.STOPS)]
    if route.driving_distance(along, target["along"]) < leg_speed(along) * STEP_SECONDS * SPEED_FACTOR:
        along = target["along"]
        print(f"  boarding at {target['name']}")
        time.sleep(BOARDING_SECONDS)
        next_stop_index += 1

    along = (along + leg_speed(along) * STEP_SECONDS * SPEED_FACTOR) % route.LOOP_LENGTH
    time.sleep(STEP_SECONDS)
