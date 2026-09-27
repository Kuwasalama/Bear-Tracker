"""Bear Tracker - Step 2: live position + driver announcements.

The whole system in one sentence:

    The driver's phone sends its location to this server, and the student's map
    asks this server "where is it, and is there anything I should know?"
    every few seconds.

There are only two kinds of thing in here:
  - the shuttle's POSITION  (comes from the phone's GPS automatically)
  - ANNOUNCEMENTS           (the driver taps a button to post one)

Run it:   python app.py
Student:  http://localhost:5000
Driver:   http://localhost:5000/driver
"""

import json
import math
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request

import route

app = Flask(__name__)


# ===========================================================================
#  POSITION
# ===========================================================================

# The shuttle's last known position. Just a variable held in memory. When the
# server restarts this is forgotten, and the next ping from the driver's phone
# fills it back in a few seconds later.
shuttle = {"lat": None, "lng": None, "heading": None, "updated_at": None, "speed": 0.0}

# No ping for this long means the shuttle isn't running (driver went off shift,
# closed the tab, lost signal).
SILENT_SECONDS = 60

# --- How fast is it going? -------------------------------------------------
#
# We work this out ourselves instead of trusting the phone's speed reading,
# which is unreliable when a vehicle is crawling or stopped.
#
# Take two positions and the time between them: distance / time = speed. But a
# single reading is jumpy - GPS wobble alone can look like 10 mph - so we blend
# each new reading into a running average instead of replacing it. That's the
# SMOOTHING number: 0.3 means "the new reading counts for 30%, history 70%."

SMOOTHING = 0.3
MAX_BELIEVABLE_SPEED = 31.0   # m/s (~70 mph). Faster than this is a bad GPS fix.

# A shuttle waiting at a light has a real speed of zero, and dividing by zero
# gives an ETA of "never" - useless to a student. So predictions never use a
# speed below this floor.
MIN_SPEED_FOR_ETA = 3.0       # m/s, about 7 mph


def distance_between(lat1, lng1, lat2, lng2):
    """Straight-line distance in meters between two points on the earth.

    This is the haversine formula. You don't have to follow the trigonometry -
    what matters is that it accounts for the curve of the earth, which plain
    Pythagoras does not.
    """
    radius = 6371000  # earth's radius in meters
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


# ===========================================================================
#  ANNOUNCEMENTS
# ===========================================================================

# The presets the driver sees as buttons. Defined once, here, so the driver's
# page and the student's map can't disagree about what an announcement means.
#
#   text     what students actually read
#   level    how loud it looks:  urgent (red) / warn (amber) / info (blue)
#   minutes  how long it stays up before it disappears on its own
#
# Note the health wording. It tells students what they need in order to decide
# whether to board, without describing a passenger. Nobody gets identified.
PRESETS = {
    "health": {
        "button": "Health advisory",
        "text": "Health advisory - please take precautions on this shuttle.",
        "level": "urgent",
        "minutes": 240,
    },
    "full": {
        "button": "Bus is full",
        "text": "This shuttle is full and may not be able to pick up at the next stop.",
        "level": "warn",
        "minutes": 15,
    },
    "late": {
        "button": "Running late",
        "text": "This shuttle is running behind schedule.",
        "level": "warn",
        "minutes": 45,
    },
    "detour": {
        "button": "Detour",
        "text": "This shuttle is on a detour and may skip some stops.",
        "level": "warn",
        "minutes": 120,
    },
    "break": {
        "button": "Short break",
        "text": "This shuttle is on a short break and will be moving again soon.",
        "level": "info",
        "minutes": 30,
    },
    "resuming": {
        "button": "Back in service",
        "text": "This shuttle is back in service and running normally.",
        "level": "info",
        "minutes": 20,
    },
}

CUSTOM_MINUTES = 60  # how long a typed message lasts
MAX_MESSAGE_LENGTH = 200

# ===========================================================================
#  STUDENT REPORTS
# ===========================================================================
#
# Announcements go one way: driver to students. This is the way back.
#
# Students tap, they don't type. That's the whole design, and it's deliberate:
#
#   nothing to moderate    - you can't write anything, so nobody can post abuse
#                            or "shuttle cancelled, go home" and have it look
#                            official
#   nothing to misread     - a driver's announcement says "From the driver".
#                            A tally of taps says "4 students waiting". Those
#                            can never be confused for one another
#   useful to the driver   - "four people at Lloyd" is something they can act
#                            on. "is the 4:15 running?" is not
#
#   kind      what the student taps
#   summary   how it reads once several people have tapped it
#   minutes   how long one tap counts for before it fades
#   level     how loud it looks on the driver's page

REPORT_KINDS = {
    "waiting": {
        "button": "I'm waiting here",
        "summary": "waiting",
        "minutes": 25,
        "level": "info",
    },
    "crowded": {
        "button": "It's crowded here",
        "summary": "say it's crowded",
        "minutes": 25,
        "level": "info",
    },
    "passed": {
        "button": "Bus passed without stopping",
        "summary": "say the bus passed without stopping",
        "minutes": 30,
        "level": "warn",
    },
    "no_show": {
        "button": "Bus never came",
        "summary": "say the bus never came",
        "minutes": 45,
        "level": "warn",
    },
}

# One phone can only shout so loud. Without this, one bored student taps forty
# times and the driver diverts to a stop with nobody at it.
MAX_REPORTS_PER_MINUTE = 6

# Reports are held in memory and never written to disk - the opposite choice to
# announcements, and for the opposite reason. A driver TYPED their announcement
# and would be misled if a restart silently dropped it. A report is a tap that's
# worthless in twenty minutes anyway; if the server restarts, students who are
# still waiting tap again and we're current within a minute.
reports = []


def live_reports():
    """Drop reports that have aged out, and return what's left."""
    now = time.time()
    reports[:] = [r for r in reports if r["expires_at"] > now]
    return reports


def summarise_reports():
    """Counts per stop: {"lloyd": {"waiting": 4, "crowded": 1}}.

    Only ever counts, never who. There's no student identity in here to leak -
    the device id exists to stop one phone counting as four, and it never
    leaves the server.
    """
    tally = {}
    for report in live_reports():
        tally.setdefault(report["stop"], {}).setdefault(report["kind"], 0)
        tally[report["stop"]][report["kind"]] += 1
    return tally


def clear_reports_at(stop_id):
    """The bus reached this stop, so anyone waiting there has been picked up.

    Without this, a student taps "I'm waiting", boards thirty seconds later,
    and the driver's page still claims somebody's standing there for the next
    twenty minutes. The arrival IS the resolution.
    """
    resolved = ("waiting", "crowded")
    reports[:] = [
        r for r in live_reports()
        if not (r["stop"] == stop_id and r["kind"] in resolved)
    ]

announcements = []  # the ones currently posted
next_id = 1  # so each announcement has a number we can use to delete it

# Announcements are saved to a small file, unlike the shuttle's position.
#
# The position doesn't need saving - if the server restarts, the phone sends a
# fresh one three seconds later and nothing is lost. An announcement is
# different: the driver TYPED it, and if a restart silently deleted it, they'd
# believe students were being warned when they weren't. So it goes on disk.
#
# This is a plain text file, not a database. A database is what you'd want if
# you ever needed to search or report on announcements. Right now we only ever
# need "what's showing at this moment", and a file does that fine.
ANNOUNCEMENTS_FILE = Path(__file__).parent / "announcements.json"


def save_announcements():
    """Write the current list to disk after anything changes."""
    try:
        ANNOUNCEMENTS_FILE.write_text(json.dumps(announcements, indent=2))
    except OSError as err:
        # Never let a disk problem take down the shuttle map.
        print(f"Couldn't save announcements: {err}")


def load_announcements():
    """Read the list back when the server starts."""
    global next_id
    if not ANNOUNCEMENTS_FILE.exists():
        return
    try:
        saved = json.loads(ANNOUNCEMENTS_FILE.read_text())
    except (json.JSONDecodeError, OSError) as err:
        print(f"Couldn't read announcements, starting empty: {err}")
        return

    now = time.time()
    announcements.extend(a for a in saved if a.get("expires_at", 0) > now)
    if saved:
        # Carry on numbering from where we left off, so a restart can't hand
        # out an id that an older announcement is already using.
        next_id = max(a["id"] for a in saved) + 1


def live_announcements():
    """Drop anything that has aged out, and return what's left.

    Announcements expire on their own so a driver who forgets to clear
    "bus is full" doesn't leave it on screen for the rest of the day.
    """
    now = time.time()
    still_live = [a for a in announcements if a["expires_at"] > now]
    if len(still_live) != len(announcements):
        announcements[:] = still_live
        save_announcements()
    return announcements


load_announcements()


# ===========================================================================
#  THE TWO PAGES
# ===========================================================================


@app.get("/")
def choose_role():
    """The front door: student or driver?

    Everyone opens the same link - there's one app, not two - and picks which
    side of it they're on. The choice is remembered in the browser, so this
    page is something you see once and then never again.

    The server deliberately doesn't remember it. It has no accounts and no
    sessions, and the role only decides which page you get, so the browser is
    the right place to keep it. That also means a shared phone in the bus depot
    can hand the same link to a driver and a student and they each get their
    own answer.
    """
    return render_template("choose.html")


@app.get("/student")
def student_map():
    """What students open: the map, the ETAs, and the tap-to-tell buttons."""
    return render_template("map.html", reports=REPORT_KINDS)


@app.get("/driver")
def driver_page():
    """What the driver opens at the start of a shift."""
    return render_template("driver.html", presets=PRESETS)


# ===========================================================================
#  POSITION: in from the phone, out to the map
# ===========================================================================


@app.post("/api/location")
def receive_location():
    """IN: the driver's phone posts its GPS here every few seconds."""
    data = request.get_json()
    now = time.time()

    lat, lng = float(data["lat"]), float(data["lng"])

    # Work out the speed before overwriting the old position.
    if shuttle["updated_at"] is not None:
        seconds = now - shuttle["updated_at"]
        if seconds >= 1:   # too short a gap and GPS wobble dominates
            moved = distance_between(shuttle["lat"], shuttle["lng"], lat, lng)
            reading = moved / seconds
            if reading <= MAX_BELIEVABLE_SPEED:
                shuttle["speed"] = SMOOTHING * reading + (1 - SMOOTHING) * shuttle["speed"]

    shuttle["lat"] = lat
    shuttle["lng"] = lng
    shuttle["heading"] = data.get("heading")  # which way it's pointing, 0 = north
    shuttle["updated_at"] = now

    return jsonify({"ok": True})


@app.post("/api/off-duty")
def go_off_duty():
    """IN: the driver tapped STOP.

    Without this, the server can only tell the shuttle has stopped by noticing
    it went quiet - which takes SILENT_SECONDS. That's the right behaviour for a
    phone that loses signal in a dead spot, but it's wrong for a driver who
    deliberately ended their shift. This makes that case instant.
    """
    shuttle.update({"lat": None, "lng": None, "heading": None, "updated_at": None, "speed": 0.0})
    return jsonify({"ok": True})


@app.get("/api/shuttle")
def send_shuttle():
    """OUT: everything the student's map needs, in one request.

    Position and announcements travel together so the map only has to ask one
    question every few seconds instead of two.
    """
    posted = [
        {
            "id": a["id"],
            "text": a["text"],
            "level": a["level"],
            "minutes_ago": max(0, round((time.time() - a["posted_at"]) / 60)),
        }
        for a in live_announcements()
    ]

    if shuttle["updated_at"] is None:
        return jsonify({
            "running": False,
            "reason": "No shuttle is on duty right now.",
            "announcements": posted,
            "reports": summarise_reports(),
        })

    seconds_ago = time.time() - shuttle["updated_at"]

    if seconds_ago > SILENT_SECONDS:
        return jsonify({
            "running": False,
            "reason": "The shuttle stopped sending its location.",
            "announcements": posted,
            "reports": summarise_reports(),
        })

    arrivals = route.estimate_arrivals(shuttle["lat"], shuttle["lng"], shuttle["speed"])

    # Anyone waiting at a stop the bus has just reached has been picked up.
    for arrival in arrivals["arrivals"]:
        if arrival["at_stop"]:
            clear_reports_at(arrival["id"])

    return jsonify({
        "running": True,
        "lat": shuttle["lat"],
        "lng": shuttle["lng"],
        "heading": shuttle["heading"],
        "seconds_ago": round(seconds_ago),
        "announcements": posted,

        # How fast it's really moving right now, just to display.
        "speed_mph": round(shuttle["speed"] * 2.237),

        # What students have tapped, as counts per stop. Cleared above for any
        # stop the bus has just reached.
        "reports": summarise_reports(),

        # How long until the bus reaches each stop. All the work happens in
        # route.py - this just hands the answer over.
        **arrivals,
    })


@app.get("/api/stops")
def send_stops():
    """The route itself: where the stops are and what order they come in.

    This never changes while the server is running, so the map asks for it once
    when it loads rather than every few seconds.
    """
    return jsonify({
        "stops": [
            {
                "id": s["id"], "name": s["name"], "address": s["address"],
                "note": s["note"], "lat": s["lat"], "lng": s["lng"],
                "order": i + 1,
            }
            for i, s in enumerate(route.STOPS)
        ],
        "departures": route.DEPARTURES,
        "loop_minutes": route.SCHEDULED_LOOP_MINUTES,

        # The shape of the actual roads, so the map draws the route the bus
        # really drives instead of nine straight lines through the buildings
        # in between. It's a few thousand points, which is why it rides along
        # with this once-per-load request and not the one that polls.
        "geometry": route.LOOP_POINTS,
        "eta_source": "road" if route.USING_ROAD_DATA else "straight-line",
        "loop_drive_minutes": round(route.LOOP_SECONDS / 60),
    })


# ===========================================================================
#  ANNOUNCEMENTS: posted by the driver, cleared by the driver
# ===========================================================================


@app.post("/api/announcements")
def post_announcement():
    """The driver tapped a preset button, or typed a message.

    Body is either  {"preset": "health"}  or  {"message": "..."}
    """
    global next_id
    data = request.get_json() or {}

    if data.get("preset") in PRESETS:
        preset = PRESETS[data["preset"]]
        text, level, minutes = preset["text"], preset["level"], preset["minutes"]
    elif data.get("message", "").strip():
        text = data["message"].strip()[:MAX_MESSAGE_LENGTH]
        level, minutes = "info", CUSTOM_MINUTES
    else:
        return jsonify({"error": "Send either a known preset or a message."}), 400

    announcement = {
        "id": next_id,
        "text": text,
        "level": level,
        "posted_at": time.time(),
        "expires_at": time.time() + minutes * 60,
    }
    announcements.append(announcement)
    next_id += 1
    save_announcements()

    return jsonify({"ok": True, "announcements": live_announcements()})


@app.delete("/api/announcements/<int:announcement_id>")
def clear_announcement(announcement_id):
    """The driver tapped the X on one of their announcements."""
    announcements[:] = [a for a in live_announcements() if a["id"] != announcement_id]
    save_announcements()
    return jsonify({"ok": True, "announcements": announcements})


@app.get("/api/announcements")
def list_announcements():
    """What the driver's own page shows them - the ones they've posted."""
    return jsonify({"announcements": live_announcements()})


# ===========================================================================
#  REPORTS: tapped by students, read by the driver
# ===========================================================================


@app.post("/api/reports")
def add_report():
    """A student tapped one of the buttons on their stop.

    Body:  {"stop": "lloyd", "kind": "waiting", "device": "..."}

    The device id is a random string the student's browser made up and keeps in
    local storage. It is not a login and it identifies nobody - it exists only
    so one phone counts once.
    """
    data = request.get_json() or {}
    kind = data.get("kind")
    stop_id = data.get("stop")
    device = str(data.get("device") or "").strip()[:64]

    if kind not in REPORT_KINDS:
        return jsonify({"error": "Unknown report."}), 400
    if not any(s["id"] == stop_id for s in route.STOPS):
        return jsonify({"error": "Unknown stop."}), 400
    if not device:
        return jsonify({"error": "Missing device id."}), 400

    now = time.time()
    live = live_reports()

    recent = [r for r in live if r["device"] == device and now - r["at"] < 60]
    if len(recent) >= MAX_REPORTS_PER_MINUTE:
        return jsonify({"error": "That's a lot of taps - give it a minute."}), 429

    # Tapping again refreshes your own report rather than adding a second one,
    # so an impatient student can't turn themselves into a crowd.
    for report in live:
        if report["device"] == device and report["stop"] == stop_id and report["kind"] == kind:
            report["at"] = now
            report["expires_at"] = now + REPORT_KINDS[kind]["minutes"] * 60
            break
    else:
        reports.append({
            "stop": stop_id,
            "kind": kind,
            "device": device,
            "at": now,
            "expires_at": now + REPORT_KINDS[kind]["minutes"] * 60,
        })

    return jsonify({"ok": True, "reports": summarise_reports()})


@app.get("/api/reports")
def list_reports():
    """What the driver's page reads: who's waiting where, and any problems.

    Sorted by how soon the bus gets there, because a driver glancing at this
    between stops needs the next one first, not the alphabetical one.
    """
    tally = summarise_reports()
    order = {stop["id"]: i for i, stop in enumerate(route.STOPS)}

    waiting = [
        {
            "stop": stop_id,
            "name": next(s["name"] for s in route.STOPS if s["id"] == stop_id),
            "counts": counts,
        }
        for stop_id, counts in sorted(tally.items(), key=lambda kv: order.get(kv[0], 99))
    ]

    return jsonify({"stops": waiting, "kinds": REPORT_KINDS})


if __name__ == "__main__":
    # Say out loud what the ETAs are built on. A number on a screen looks the
    # same whether it came from measured roads or a guess, so the one place to
    # tell them apart is here, at startup, in front of whoever runs it.
    print(route.describe())

    # host="0.0.0.0" lets a phone on the same Wi-Fi reach this server.
    app.run(host="0.0.0.0", port=5000, debug=True)
