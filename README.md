# Follow the Duck 🦆

Voice-commanded object search for the SunFounder **PiCar-X** (Raspberry Pi 3B+).
Say *"Find the yellow duck"* — the robot scans the room with its camera, asks
**Cloudflare Workers AI** (`@cf/cloudflare/clef-flash`) what it sees, explores
toward open floor, approaches the object, and stops about 30 cm in front of it.
Speech-to-text uses `@cf/openai/whisper-large-v3-turbo`. The target is free
text — *"the red cup"*, *"a green bottle"* — never a hardcoded color tracker.

Built as an interactive AI demonstration for a ninth-grade classroom.

## How it works

```
 push-to-talk ──► whisper STT ──► intent parser ──► MissionController
                                                        │  (state machine:
 camera frame ──► clef-flash typed questions ──►  Observation   SCANNING /
      ▲                (validated schema)               │       SEARCHING /
      │                                                 ▼       APPROACHING …)
 pan/tilt scan ◄──────────────────────────────── MotorSupervisor
                                                 (sole motor owner: expiring
                                                  commands, watchdog, sonar
                                                  gate, latching STOP)
```

* **Stop–observe–decide–move**: the robot is always stationary while looking;
  motion happens in short supervised pulses (~250 ms), so cloud latency never
  steers the wheels.
* **One motor owner**: every wheel write goes through the `MotorSupervisor`.
  Commands carry mission ID + sequence number and expire locally; stale sonar,
  missing heartbeats, obstacles, STOP, signals, and cloud failures all stop
  the motors. Stops latch until a new mission explicitly re-arms.
* **Honest arrival**: `ARRIVED` requires repeated *visual* "very close"
  confirmation **and** a fresh valid sonar reading at the arrival distance.
  Sonar alone never counts — it ranges the nearest reflector, not the target.
* **Everything mockable**: the whole mission loop runs on a laptop with mock
  hardware, a scripted vision provider, and a fake clock. No GPIO required.

## Repository layout

```
src/follow_the_duck/
  cli.py          ftd command-line interface
  mission.py      state machine (IDLE…SCANNING…APPROACHING…ARRIVED/BLOCKED/…)
  supervisor.py   motor supervisor + watchdog + sensor poller (safety core)
  planner.py      viewpoint ranking (open floor, novelty, steering cost)
  tracker.py      target identity across observations
  perception.py   clef question building + answer validation
  intent.py       "find the …" / stop / cancel parsing
  models.py       Observation schema, MoveCommand, states (validated enums)
  config.py       all settings from FTD_* env vars / .env
  providers/      cloudflare.py (verified API contracts), mock.py (scripted)
  hardware/       base interfaces, picarx_hw.py (real), mock.py
  camera/         picamera2 / rpicam-still / mock + replay
  audio/          arecord push-to-talk / mock
  doctor.py       health checks (never moves the wheels)
tests/            138 deterministic tests incl. fault injection
deploy/ftd.service  optional systemd unit (starts IDLE)
docs/             Pi setup, deployment, teacher guide, limitations
```

## Quick start on a laptop (no robot, no credentials)

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest                                   # run all tests
.venv/bin/ftd replay tests/fixtures/replay_center.json "yellow duck"
.venv/bin/ftd find "red cup" --dry-run --script tests/fixtures/replay_center.json
.venv/bin/ftd status
```

The replay drives the full state machine with mock hardware and a scripted
vision provider — you'll see it scan, pick open floor, reposition, acquire the
target, approach, and declare `ARRIVED`.

## Quick start on the robot

See **docs/SETUP_PI.md** for the full guide. Summary:

```bash
ssh robot@classbot.local            # IP/hostname is yours; nothing hardcoded
cd ~/follow-the-duck
python3 -m venv --system-site-packages .venv   # keeps SunFounder packages!
.venv/bin/pip install -e .
cp .env.example .env && nano .env   # Cloudflare account ID + API token
.venv/bin/ftd doctor                # checks everything, moves nothing
.venv/bin/ftd doctor --online       # + live clef/whisper smoke test
.venv/bin/ftd find "yellow duck"    # typed mission (Enter = STOP)
.venv/bin/ftd listen --loop         # push-to-talk missions
```

`ftd stop` (from any SSH session), pressing **Enter**, and `Ctrl-C` all stop
the robot locally and immediately — the cloud is never the stop path.

## CLI

| Command | Purpose |
|---|---|
| `ftd find "yellow duck"` | run a mission for a typed target |
| `ftd listen [--loop]` | push-to-talk: Enter records, speech picks the target |
| `ftd stop` | stop a running ftd process (local SIGTERM, latches motors) |
| `ftd status` | last mission state/message/latencies |
| `ftd doctor [--online] [--record]` | health checks; never commands wheels |
| `ftd replay SCRIPT [TARGET]` | deterministic mock mission from a JSON script |
| `--dry-run` | mock hardware/camera anywhere |

## Configuration & credentials

Everything is configured through `FTD_*` environment variables or a local
`.env` file (template: `.env.example`). Cloudflare credentials
(`CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`) are read from the
environment only — never committed, never logged, never echoed by `doctor`.
Vision calls are budgeted per mission and per minute
(`FTD_MAX_VISION_CALLS_PER_MISSION`, `..._PER_MINUTE`), and no recordings or
captured frames are retained on disk by default.

## Tests

```bash
.venv/bin/python -m pytest        # 138 tests, ~10 s, no network, no GPIO
```

Covered with deterministic fixtures and fault injection: target ahead / found
by scanning / found after repositioning; left-vs-right choice driven by open
floor; target loss → stop & rescan; ambiguous matches; unrelated close
obstacle (no fake arrival); blocked routes; invalid/no-echo sonar; delayed &
dropped AI responses; stale/out-of-order/cancelled-mission results; watchdog
stop on heartbeat expiry while inference is blocked; STOP during inference,
approach, and recording paths; restart stays idle; Cloudflare payloads match
the documented schemas.

## What still needs the physical robot

Simulation can't verify hardware. See **docs/LIMITATIONS.md** for the honest
list; the on-device acceptance checklist lives in **docs/SETUP_PI.md**.

## Safety

The supervisor is a *software* watchdog: it cannot stop the motors if the
process, OS, or HAT itself fails. Keep the PiCar-X power switch within reach
during live runs; test on a flat floor away from stairs. Cliff detection via
the grayscale sensors ships **disabled** until calibrated on your floor.
