# 🐾 Bear Tracker

Live shuttle tracking for the **Livingstone College Academic Shuttle**.

> We Drive. You Thrive.

The driver's phone sends its location to the server. Students open a map, see
where the bus is, and get a real arrival time for their own stop. The driver
can post announcements; students can tap to say they're waiting.

---

## Running it

```bash
pip install -r requirements.txt
python app.py
```

Then open **http://localhost:5000** and pick student or driver.

To watch it work without a real phone, run the fake bus in a second terminal:

```bash
python demo.py
```

It drives the actual route at 6× speed, so the map moves and the ETAs count
down the way they would on a real shift.

`host="0.0.0.0"` is already set, so a phone on the same Wi-Fi can reach the
server at `http://<your-computer-ip>:5000`. That's how you'd test with a real
driver.

Python 3.9+ (it uses `zoneinfo`). Flask is the only dependency — everything
else is standard library.

---

## The two sides

Everyone opens the same link and picks a role on first launch. The choice is
remembered in the browser, so the picker is a once-only screen, and a pill
button on either page switches back.

**Students** (`/student`) get the map, the route drawn on real roads, and a
per-stop arrival time. Tapping a stop opens its detail view with four report
buttons: *I'm waiting here*, *It's crowded here*, *Bus passed without
stopping*, *Bus never came*.

**Drivers** (`/driver`) get a START button that shares the phone's GPS, a panel
showing how many students are waiting at each stop ahead, six one-tap preset
announcements, and a box for typing a custom message.

---

## How the ETAs work

This is the part worth understanding, because it's where most of the thinking
went.

The route is a loop, so lay it out as a line and measure along it. Every stop
gets a number — how far along the loop it sits. Match the bus's GPS to the same
line, and the distance it still has to drive is just a subtraction. If that
comes out negative the bus is past the stop and has to go all the way round,
which is exactly "you missed it."

The line itself is **real roads**. [`road.py`](road.py) asks
[OSRM](https://project-osrm.org/) — a free, public routing engine built on
OpenStreetMap — how you actually drive between each pair of stops, and gets
back the road shape, the real distance, and the real driving time including
speed limits and junctions.

That happens **once**. The answer is cached to `route_geometry.json` and
committed to this repo, so a fresh clone works with no network and no API key.
Nothing is fetched per request. If you change a stop's coordinates the cache
invalidates itself and refetches on the next start; you can also force it:

```bash
python road.py
```

Two numbers come out of this that a single fudge factor could never produce:

- **Distance is measured per leg.** Culinary Arts back to Horseshoe Gate is
  2.8 km in a straight line but **5.6 km of actual driving** — Salisbury's rail
  lines force a long detour. A blanket multiplier gets that leg wrong by 100%.
- **Time is per-road.** Each leg carries its own duration, so Jake Alexander at
  45 mph and a campus side street at 25 don't get averaged into one meaningless
  middle number.

Live GPS then corrects the estimate. `pace_factor()` compares the bus's actual
speed against what its current road predicts and stretches everything ahead by
that ratio, clamped to −20%/+40% so one red light doesn't wreck the numbers.
A parked bus is ignored rather than treated as infinitely slow.

The API reports `eta_source` as `"road"` or `"straight-line"` so the map never
presents a guess as a measurement. If OSRM can't be reached on a first run with
no cache, everything falls back to straight lines × 1.35 and still works.

### ⚠️ The schedule doesn't match the road

Turning on real road data surfaced this, and it hasn't been resolved:

```
Route: 25.23 km loop from real roads (OSRM), 1160 points
       43.3 min driving + 16 min boarding = 59.3 min per loop
       timetable allows 35 min
```

By the road data a loop takes ~59 minutes against a 35-minute timetable with
hourly departures. Three possible explanations, and the code can't tell which:

1. `PICKUP_SECONDS = 120` is too generous — 2 min × 8 stops is 16 of those 59.
2. OSRM is pessimistic. It estimates a 21 mph average across the loop.
3. The 35 minutes was never real and the bus runs late every loop.

**Riding one full loop with a stopwatch** would settle it. The warning prints
at startup rather than being quietly scaled away, because a wrong number you
can see beats a right-looking one you can't check.

---

## Student reports

Students **tap, they never type**. That's the whole design:

- **Nothing to moderate** — the server only accepts a stop id and one of four
  fixed kinds, both checked against its own list. There is no path by which a
  student can put a sentence on another student's screen.
- **Nothing to misread** — a driver's announcement is labelled *From the
  driver*. A tally of taps reads *4 people waiting here*. The two can't be
  confused, which is what keeps the driver's voice worth trusting.
- **Useful to the driver** — "four people at Lloyd" is actionable. "is the 4:15
  running?" is not.

One phone counts once: each browser keeps a random id in `localStorage`, and
re-tapping refreshes your report instead of adding another. On top of that,
6 reports per minute per device. Reaching a stop clears the waiting count
there — the arrival *is* the resolution.

Reports are held in memory and never written to disk, unlike announcements. A
driver *typed* their announcement and would be misled if a restart dropped it
silently; a tap is worthless in twenty minutes anyway.

---

## The files

| | |
|---|---|
| [`app.py`](app.py) | Flask server. Routes, announcements, reports, the JSON API. |
| [`route.py`](route.py) | The route and all the ETA maths. |
| [`road.py`](road.py) | Fetches and caches real road geometry from OSRM. |
| [`demo.py`](demo.py) | A fake bus that drives the real loop. Not part of the app. |
| [`templates/choose.html`](templates/choose.html) | The role picker. |
| [`templates/map.html`](templates/map.html) | The student map. |
| [`templates/driver.html`](templates/driver.html) | The driver's controls. |

### API

| | |
|---|---|
| `POST /api/location` | Driver's phone posts its GPS here. |
| `POST /api/off-duty` | Driver tapped STOP. |
| `GET /api/shuttle` | Position, ETAs, announcements and report counts, in one call. |
| `GET /api/stops` | The route and its road geometry. Fetched once on load. |
| `GET POST /api/announcements` | List and post. `DELETE /api/announcements/<id>` to clear. |
| `GET POST /api/reports` | Student taps in, driver reads out. |

---

## Editing the route

Stops, departure times and the timing assumptions all live at the top of
[`route.py`](route.py). Change a stop's coordinates and the road geometry
refetches itself on the next start.

Coordinates came from geocoding the published addresses — **verify them on the
map**, because a stop 30 m out is a stop on the wrong side of the road.

---

## Before this runs for real

- **`/driver` has no authentication.** Anyone who opens the app can pick "I'm
  driving" and post announcements to every student on the route. A 4-digit PIN
  on the driver card is the obvious next step.
- **Flask's development server is not a production server.** Put it behind
  gunicorn or waitress.
- **Position is held in memory**, so a restart forgets where the bus is until
  the next ping arrives a few seconds later. That's intentional. Announcements
  are on disk because losing those silently would actually mislead someone.
- **One shuttle only.** The server tracks a single position. A second bus needs
  a vehicle id threading through `shuttle`, `/api/location` and the map.
