# 🐾 Bear Tracker

**Live tracking for the Livingstone College Academic Shuttle.**

> We Drive. You Thrive.

No more standing outside wondering if you missed the bus.

---

## What it does

The driver starts their shift by tapping a button on their phone. From then on,
the phone quietly sends its location to Bear Tracker every few seconds.

Students open the app and see the bus moving on a map, along with how many
minutes until it reaches their stop. Tap your stop and you get one big number:
the wait.

It works both ways. The driver can send a message to everyone — *"bus is full"*,
*"running late"*, *"on a detour"* — and it shows up at the top of every
student's screen. Students can tap back to say they're waiting, so the driver
knows there are four people at Lloyd Street before they get there.

Everyone opens the same link. The first time, you pick whether you're a student
or the driver, and you get the right screen from then on.

---

## If you're a student

- See the bus on a map, moving in real time
- Get the minutes until it reaches **your** stop
- See any message the driver has posted
- Tap to let the driver know: **I'm waiting here**, **It's crowded here**,
  **Bus passed without stopping**, **Bus never came**

You can't type anything, only tap. That's on purpose — it means nobody can post
something rude or untrue, and a student's tap can never be mistaken for an
official message from the driver.

## If you're the driver

- One **START** button shares your location for the whole shift
- See how many students are waiting at each stop coming up
- Send a message with one tap — six common ones are ready to go
- Or type your own
- Messages clear themselves. "Bus is full" disappears after 15 minutes so you
  don't have to remember to take it down

---

## What it's built with

Everything here is free and open source. There are no accounts, no tracking of
individual people, and nothing to pay for.

| Tool | What it's for |
|---|---|
| **Python** | The programming language the whole thing is written in |
| **Flask** | A small, simple tool for running a website |
| **Leaflet** | Puts the interactive, draggable map on the screen |
| **OpenStreetMap** | The map itself — the streets and buildings you see |
| **OSRM** | Works out the real driving route between stops, following actual roads |
| **Your phone's GPS** | How the bus knows where it is. Standard, built into every phone |

The arrival times are the part worth knowing about. Most simple trackers draw a
straight line between two stops and guess. Bear Tracker asks OSRM for the route
a car would actually drive, so it knows the real distance and the real driving
time for every road on the loop.

That matters more than it sounds. The trip from the Culinary Arts Facility back
to Horseshoe Gate is **1.7 miles in a straight line but 3.5 miles of actual
driving** — the railroad forces a long way round. A guess gets that leg wrong by
double. The app also watches how fast the bus is really moving and adjusts, so
a bus stuck in traffic doesn't keep promising it'll be there on time.

---

## Trying it out

You'll need [Python](https://www.python.org/downloads/) installed. Then, in a
terminal, in this folder:

```
pip install -r requirements.txt
python app.py
```

Open **http://localhost:5000** in a browser and pick student or driver.

To see it work without an actual bus, open a second terminal and run:

```
python demo.py
```

That puts a pretend bus on the real route, driving at 6× speed, so you can watch
the map move and the countdowns tick down.

---

## Something we found out

While building this, the app worked out how long the loop really takes to drive.
The answer didn't match the printed timetable:

- The timetable allows **35 minutes** per loop
- The real roads need about **43 minutes** of driving, plus roughly **16
  minutes** of stopping to pick people up — about **59 minutes** total

So either the stops are quicker than we assumed, or the route is slower than the
schedule expects, and the bus is running behind on every single loop.

We left this showing rather than quietly adjusting the numbers to fit. The way
to settle it is to **ride one full loop with a stopwatch** and write down the
time at each stop.

---

## Not finished yet

- **The driver screen isn't locked.** Right now anyone could choose "I'm
  driving" and post a message to everyone. It needs a PIN code before this is
  used for real.
- **It only tracks one bus.** A second shuttle would need some changes.
- **It's set up for testing, not for a whole campus yet.** Fine for trying out
  on a phone on the same Wi-Fi; it needs proper hosting before it goes live.

---

## The files, briefly

| File | What's in it |
|---|---|
| `app.py` | The server — runs the website, handles messages and reports |
| `route.py` | The stops, the timetable, and the arrival-time maths |
| `road.py` | Fetches the real driving routes between stops, once, and saves them |
| `demo.py` | The pretend bus, for testing |
| `templates/` | The three screens: the role picker, the student map, the driver's controls |

Stops and departure times are all at the top of `route.py` and can be edited
there.
