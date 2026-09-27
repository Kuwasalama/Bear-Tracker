"""The Livingstone College Academic Shuttle route.

    "We Drive. You Thrive."

This file answers one question: given where the bus is right now, how long until
it reaches each stop?

--------------------------------------------------------------------------
THE IDEA
--------------------------------------------------------------------------
The route is a loop. Lay it out as a line and measure along it:

    Horseshoe Gate .... Monroe .... College Park .... Lloyd .... (etc) .... back
    0 km             0.4 km       1.1 km          2.2 km                19 km

Every stop gets a number: how far along the loop it sits. Then we take the bus's
GPS position and find its number the same way. Now:

    distance the bus still has to drive  =  stop's number - bus's number

and if that comes out negative, the bus is PAST that stop and has to go all the
way round the loop to reach it again. Which is exactly "you missed it."

--------------------------------------------------------------------------
WHAT THE LINE IS MADE OF
--------------------------------------------------------------------------
That line used to be nine straight segments joining the stops, with every
distance multiplied by 1.35 to stand in for the fact that roads bend.

It isn't any more. road.py fetches the actual roads - a few thousand GPS points
tracing real pavement - and the line is now those points. Two things follow:

    Distances are measured, not estimated. There is no fudge factor left to
    be wrong about, and the bus gets matched to the road it's genuinely on
    rather than to the straight line it's nearest to.

    Time is per-road, not per-loop. Each leg carries its own driving duration
    from the routing engine, so Jake Alexander at 45 mph and a campus side
    street at 25 don't get averaged into one meaningless middle number.

If road.py can't get that data - no network on first run - everything below
falls back to the old straight-line arithmetic and the app still works. The
numbers are just rougher, and `eta_source` says so.

--------------------------------------------------------------------------
WHAT'S STILL ASSUMED
--------------------------------------------------------------------------
* Coordinates came from geocoding the published addresses. Verify them on the
  map - a stop 30m off is a stop on the wrong side of the road.
* Every stop costs PICKUP_SECONDS to service.
* Driving times are free-flow: real speed limits and junctions, but no traffic
  and no weather. The bus's own GPS speed corrects for that live - see
  pace_factor below.
* At Horseshoe Gate the bus waits for its next scheduled departure. That part
  comes from the timetable, not GPS - a parked bus can't tell you when it
  intends to leave.
"""

import bisect
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import road

TIMEZONE = ZoneInfo("America/New_York")


# ===========================================================================
#  THE ROUTE
# ===========================================================================

# In the order the driver drives them. After the last one it returns to the
# first, which is what makes this a loop.
STOPS = [
    {"id": "horseshoe",  "name": "Horseshoe Gate",            "address": "701 W. Monroe Street",
     "lat": 35.67129, "lng": -80.48568, "note": "Main campus - all departures start here"},

    {"id": "monroe",     "name": "Monroe Street Housing",     "address": "1312 Monroe Street",
     "lat": 35.67460, "lng": -80.48691, "note": ""},

    {"id": "collegepark", "name": "College Park Residence Hall", "address": "1710 Old Wilkesboro Road",
     "lat": 35.67816, "lng": -80.49337, "note": ""},

    {"id": "lloyd",      "name": "Lloyd Street Housing",      "address": "301 Lloyd Street",
     "lat": 35.67427, "lng": -80.48266, "note": "Campus drop-off available after this stop"},

    {"id": "hood",       "name": "Hood Theological Seminary", "address": "1810 Lutheran Synod Drive",
     "lat": 35.64388, "lng": -80.48309, "note": ""},

    {"id": "marriott",   "name": "Courtyard by Marriott",     "address": "120 Marriott Circle",
     "lat": 35.65811, "lng": -80.46236, "note": ""},

    {"id": "wyndham",    "name": "Wyndham Property",          "address": "925 Bendix Drive",
     "lat": 35.65026, "lng": -80.46909, "note": ""},

    {"id": "hilton",     "name": "Hilton Property",           "address": "1001 Klumac Road",
     "lat": 35.64233, "lng": -80.48495, "note": ""},

    {"id": "culinary",   "name": "Culinary Arts Facility",    "address": "530 Jake Alexander Blvd S",
     "lat": 35.64652, "lng": -80.48850, "note": "Final stop before returning to campus"},
]

# Departures from Horseshoe Gate. Note there is no 12:15 - the midday gap.
DEPARTURES = [
    "07:15", "08:15", "09:15", "10:15", "11:15",
    "13:15", "14:15", "15:15", "16:15", "17:15", "18:15", "19:15", "20:15",
]

LAYOVER_STOP = "horseshoe"   # where the bus waits between loops


# ===========================================================================
#  THE ASSUMPTIONS
# ===========================================================================

PICKUP_SECONDS = 120      # your 2 minutes of boarding time per stop
AT_STOP_METERS = 60       # closer than this and the bus counts as being AT the stop
SCHEDULED_LOOP_MINUTES = 35   # departs :15, back at the gate around :50

# Only used when we have no road data. Straight lines understate real driving
# distance, and this is the blunt correction that used to apply everywhere.
ROAD_FACTOR = 1.35

# --- Reconciling the map with the bus ---------------------------------------
#
# The road data says how long a leg takes with clear roads. The bus's GPS says
# how fast it's moving right now. Neither alone is enough:
#
#   trust only the roads   - and a bus stuck behind a garbage truck still
#                            promises to arrive on time
#   trust only the GPS     - and a bus at a red light reports zero, which makes
#                            every ETA "never"
#
# So the roads set the shape of the estimate and the GPS scales it. If the bus
# is doing half the speed this particular road expects, everything ahead of it
# gets stretched - but only so far, because one slow reading is a traffic light
# far more often than it's a genuinely slower trip.

MOVING_THRESHOLD_MPS = 2.0   # below this the bus is parked, not driving
PACE_MIN = 0.8               # never promise more than 20% faster than the roads say
PACE_MAX = 1.4               # never stretch more than 40% slower on one reading


# ===========================================================================
#  MEASURING THE LOOP
# ===========================================================================


def distance_between(lat1, lng1, lat2, lng2):
    """Straight-line distance in meters, accounting for the curve of the earth."""
    radius = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _measure(points):
    """How far along the line each point sits. points[i] is at along[i] meters."""
    along = [0.0]
    for i in range(len(points) - 1):
        along.append(along[-1] + distance_between(*points[i], *points[i + 1]))
    return along


def _build_from_roads(geometry):
    """Stitch the nine routed legs into one closed line and measure it.

    Returns (points, along, leg_starts, leg_durations) where leg_starts[i] is
    the index into points at which stop i sits.
    """
    points, leg_starts = [], []

    for leg in geometry["legs"]:
        leg_starts.append(len(points))
        # Each leg ENDS where the next one begins, so drop that last point or
        # every stop would appear in the line twice.
        points.extend(leg["points"][:-1])

    points.append(list(points[0]))   # close the ring back to Horseshoe Gate

    durations = [leg["duration"] for leg in geometry["legs"]]
    return points, _measure(points), leg_starts, durations


def _build_from_straight_lines():
    """The fallback: nine straight segments, inflated by ROAD_FACTOR.

    This is what the whole file used to do. It's kept because an app that shows
    rough numbers beats an app that won't start, and the first run on a machine
    with no network would otherwise be a crash.
    """
    points = [[s["lat"], s["lng"]] for s in STOPS]
    points.append(list(points[0]))
    leg_starts = list(range(len(STOPS)))

    along = [d * ROAD_FACTOR for d in _measure(points)]

    # With no per-road timings, fall back to one pace for the whole loop: the
    # one the timetable implies, once boarding time is taken out of it.
    budget = SCHEDULED_LOOP_MINUTES * 60 - (len(STOPS) - 1) * PICKUP_SECONDS
    pace = along[-1] / budget
    durations = [(along[i + 1] - along[i]) / pace for i in range(len(STOPS))]

    return points, along, leg_starts, durations


_geometry = road.get(STOPS)
USING_ROAD_DATA = _geometry is not None

if USING_ROAD_DATA:
    LOOP_POINTS, POINT_ALONG, _leg_starts, _leg_durations = _build_from_roads(_geometry)
else:
    LOOP_POINTS, POINT_ALONG, _leg_starts, _leg_durations = _build_from_straight_lines()

LOOP_LENGTH = POINT_ALONG[-1]     # real driving meters all the way round

# Hang the numbers off each stop. `along` is how far round the loop it sits,
# `time_along` is how many seconds of driving that is - both measured from
# Horseshoe Gate, both going forward only, because the bus can't reverse.
_elapsed = 0.0
for _i, _stop in enumerate(STOPS):
    _next_start = _leg_starts[_i + 1] if _i + 1 < len(STOPS) else len(POINT_ALONG) - 1
    _stop["along"] = POINT_ALONG[_leg_starts[_i]]
    _stop["leg_to_next"] = POINT_ALONG[_next_start] - _stop["along"]
    _stop["leg_seconds"] = _leg_durations[_i]
    _stop["time_along"] = _elapsed
    _elapsed += _stop["leg_seconds"]

LOOP_SECONDS = _elapsed           # driving time for one full loop, no boarding

# Sorted list of where each leg starts, so we can binary-search a position into
# its leg instead of scanning all nine.
_LEG_STARTS_ALONG = [s["along"] for s in STOPS]


def _leg_index(along):
    """Which leg of the loop a point on it belongs to."""
    return max(0, bisect.bisect_right(_LEG_STARTS_ALONG, along % LOOP_LENGTH) - 1)


def where_on_the_loop(lat, lng):
    """Turn the bus's GPS position into a single number: how far along the loop.

    We walk every segment of the road line, work out the closest point on that
    segment to the bus, and keep whichever segment it's genuinely nearest to.
    That's what stops a bus on Klumac Road from being confused with one on
    Monroe Street - and with real road geometry it also stops a bus on Jake
    Alexander from being matched to a straight line drawn through the woods
    beside it.

    Returns (distance_along_loop, how_far_off_route).
    """
    # Flatten this small patch of earth into plain x/y meters so we can do
    # ordinary geometry on it. One scale factor for the whole loop is fine -
    # it spans about 4 km, over which the distortion is centimeters.
    scale = math.cos(math.radians(lat)) * 111320

    best_along, best_off = 0.0, float("inf")

    for i in range(len(LOOP_POINTS) - 1):
        alat, alng = LOOP_POINTS[i]
        blat, blng = LOOP_POINTS[i + 1]

        bx = (blng - alng) * scale
        by = (blat - alat) * 111320
        px = (lng - alng) * scale
        py = (lat - alat) * 111320

        segment_squared = bx * bx + by * by
        if segment_squared == 0:
            continue

        # How far along this segment the closest point falls, as a fraction 0..1.
        t = max(0.0, min(1.0, (px * bx + py * by) / segment_squared))

        off_route = math.hypot(px - t * bx, py - t * by)
        if off_route < best_off:
            best_off = off_route
            best_along = POINT_ALONG[i] + t * (POINT_ALONG[i + 1] - POINT_ALONG[i])

    return best_along, best_off


def point_at(along):
    """The opposite of where_on_the_loop: turn a distance back into (lat, lng).

    Only demo.py needs this - it's how a fake bus drives the real route - but it
    lives here because this is the file that knows what the route is shaped like.
    """
    along %= LOOP_LENGTH
    i = min(bisect.bisect_right(POINT_ALONG, along) - 1, len(LOOP_POINTS) - 2)
    i = max(i, 0)

    span = POINT_ALONG[i + 1] - POINT_ALONG[i]
    t = (along - POINT_ALONG[i]) / span if span else 0.0

    alat, alng = LOOP_POINTS[i]
    blat, blng = LOOP_POINTS[i + 1]
    return alat + t * (blat - alat), alng + t * (blng - alng)


def driving_distance(from_along, to_along):
    """Meters the bus still has to drive to get from one point to another.

    Because it's a loop, this always wraps forward - the bus can't reverse.
    """
    return (to_along - from_along) % LOOP_LENGTH


def _seconds_at(along):
    """Driving seconds from Horseshoe Gate to a point on the loop."""
    leg = STOPS[_leg_index(along)]
    if leg["leg_to_next"] <= 0:
        return leg["time_along"]
    into = (along % LOOP_LENGTH) - leg["along"]
    return leg["time_along"] + (into / leg["leg_to_next"]) * leg["leg_seconds"]


def driving_seconds(from_along, to_along):
    """Seconds of driving between two points, at each road's own speed.

    Same wrap-forward rule as driving_distance, but in time rather than
    distance - which is the whole point of having real road data. Half the
    remaining distance is not half the remaining time when one half is a
    boulevard and the other is a parking lot.
    """
    return (_seconds_at(to_along) - _seconds_at(from_along)) % LOOP_SECONDS


def road_speed_at(along):
    """How fast the road at this point of the loop is meant to be driven, in m/s.

    This is the leg's real distance over its real driving time, so it carries
    the speed limit and the junctions with it - 45 mph out on Jake Alexander,
    something much slower on the campus approach.
    """
    leg = STOPS[_leg_index(along)]
    if leg["leg_seconds"] <= 0:
        return 0.0
    return leg["leg_to_next"] / leg["leg_seconds"]


def pace_factor(measured_mps, bus_along):
    """How the bus is really doing against what this road predicts.

    1.0 means "exactly as the road data expects". Above 1.0 means it's running
    slow and everything ahead should be stretched by that much.

    A parked bus tells us nothing useful - it's at a stop, or a light, not on a
    slower road - so that case returns 1.0 and leaves the estimate alone.
    """
    if measured_mps < MOVING_THRESHOLD_MPS:
        return 1.0

    expected_mps = road_speed_at(bus_along)
    if expected_mps <= 0:
        return 1.0

    return max(PACE_MIN, min(PACE_MAX, expected_mps / measured_mps))


# ===========================================================================
#  THE TIMETABLE (only used for the layover)
# ===========================================================================


def next_departure(now=None):
    """When the bus is next scheduled to leave Horseshoe Gate."""
    now = now or datetime.now(TIMEZONE)
    for slot in DEPARTURES:
        hour, minute = (int(x) for x in slot.split(":"))
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate > now:
            return candidate
    # Past the last run of the day - the first one tomorrow.
    hour, minute = (int(x) for x in DEPARTURES[0].split(":"))
    return (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)


def clock(dt):
    """12-hour time without a leading zero, written out by hand because the
    strftime flag for that differs between Windows and Mac."""
    return f"{dt.hour % 12 or 12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


# ===========================================================================
#  THE ANSWER: how long until the bus reaches each stop
# ===========================================================================


def estimate_arrivals(lat, lng, speed_mps, now=None):
    """Work out an ETA for every stop on the route.

    Returns a list in route order, each entry carrying its own ETA and how many
    stops away the bus is.
    """
    now = now or datetime.now(TIMEZONE)
    bus_along, off_route = where_on_the_loop(lat, lng)
    pace = pace_factor(speed_mps, bus_along)

    # Is the bus sitting at Horseshoe Gate waiting for its departure time? If so
    # the timetable knows something GPS can't: when it intends to leave.
    layover = next(s for s in STOPS if s["id"] == LAYOVER_STOP)
    waiting_at_gate = distance_between(lat, lng, layover["lat"], layover["lng"]) <= AT_STOP_METERS
    departure = next_departure(now)
    layover_wait = (departure - now).total_seconds() if waiting_at_gate else 0.0

    # Everything is measured forward from where the bus will set off from. While
    # it's parked at the gate that's the gate itself, not its exact GPS position
    # - otherwise a bus stopped ten meters past the line would be told it has a
    # whole loop to drive before it gets back there.
    origin = layover["along"] if waiting_at_gate else bus_along

    # Which stop is it heading for next? The nearest one ahead of it.
    ordered = sorted(STOPS, key=lambda s: driving_distance(origin, s["along"]))
    next_stop = ordered[0]

    results = []
    for position, stop in enumerate(ordered):
        metres = driving_distance(origin, stop["along"])

        # Wait for the departure time, drive the roads, and stop to board at
        # every stop on the way there.
        seconds = layover_wait
        seconds += driving_seconds(origin, stop["along"]) * pace
        seconds += position * PICKUP_SECONDS

        results.append({
            "id": stop["id"],
            "name": stop["name"],
            "address": stop["address"],
            "note": stop["note"],
            "lat": stop["lat"],
            "lng": stop["lng"],
            "order": STOPS.index(stop) + 1,
            "eta_seconds": round(seconds),
            "eta_minutes": max(1, round(seconds / 60)),
            "distance_m": round(metres),
            "stops_away": position + 1,          # the next stop is "1 stop away"
            "is_next": stop["id"] == next_stop["id"],
            "at_stop": metres <= AT_STOP_METERS or (LOOP_LENGTH - metres) <= AT_STOP_METERS,
        })

    return {
        "arrivals": sorted(results, key=lambda r: r["order"]),
        "next_stop": next_stop["name"],
        "off_route_m": round(off_route),
        "waiting_at_gate": waiting_at_gate,
        "next_departure": clock(departure),
        "layover_minutes": max(0, round(layover_wait / 60)) if waiting_at_gate else 0,

        # Whether these numbers came from real roads or from straight lines, so
        # the map can be honest about it rather than presenting a guess and a
        # measurement in the same typeface.
        "eta_source": "road" if USING_ROAD_DATA else "straight-line",

        # How hard the live GPS is bending the road data right now. 1.0 means
        # the bus is doing exactly what the roads predict.
        "pace": round(pace, 2),
    }


def describe():
    """One-time summary for the console, so the numbers aren't a black box."""
    source = "real roads (OSRM)" if USING_ROAD_DATA else "straight lines x %.2f" % ROAD_FACTOR
    boarding = (len(STOPS) - 1) * PICKUP_SECONDS / 60

    lines = [
        f"Route: {LOOP_LENGTH / 1000:.2f} km loop from {source}, {len(LOOP_POINTS)} points",
        f"       {LOOP_SECONDS / 60:.1f} min driving + {boarding:.0f} min boarding"
        f" = {(LOOP_SECONDS / 60) + boarding:.1f} min per loop",
        f"       timetable allows {SCHEDULED_LOOP_MINUTES} min",
    ]

    # Worth flagging loudly. If the roads say the loop can't be driven in the
    # time the timetable allows, the timetable is wrong, and no amount of ETA
    # maths will fix a bus that is structurally late.
    slack = SCHEDULED_LOOP_MINUTES - ((LOOP_SECONDS / 60) + boarding)
    if USING_ROAD_DATA and slack < 0:
        lines.append(f"       WARNING: the schedule is {abs(slack):.1f} min short of what the"
                     f" roads need - every loop starts behind")
    return "\n".join(lines)
