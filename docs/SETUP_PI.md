# Pi setup guide (PiCar-X, Raspberry Pi 3B+, Raspberry Pi OS / Debian 13)

This guide **preserves the working SunFounder installation**. Do not reinstall
or upgrade `robot-hat`, `picar-x`, or `vilib` — they were installed per vendor
instructions and the servos are already zeroed/calibrated.

## 1. Inspect before touching anything

```bash
ssh robot@classbot.local        # use your actual hostname/IP
python3 --version               # note it; Debian 13 ships Python 3.13
python3 -c "from picarx import Picarx; print('picarx OK')"
python3 -c "import robot_hat; print(getattr(robot_hat,'__version__','?'))"
rpicam-still --version | head -1
python3 -c "import picamera2; print('picamera2 OK')"   # may be missing; rpicam fallback exists
arecord -l                      # microphone cards
aplay -l                        # speaker cards
```

Write down which ALSA card the mic/speaker are — you'll configure them, not
hardcode them.

## 2. Install this project (without disturbing system packages)

**Shortcut:** `./deploy/install.sh` performs steps 2–4 (and the systemd
service) in one go; the rest of this section is the manual equivalent.

```bash
cd ~ && git clone <your-repo-url> follow-the-duck   # or scp, see DEPLOY.md
cd follow-the-duck
python3 -m venv --system-site-packages .venv
```

`--system-site-packages` matters: `picarx`, `robot_hat`, and `picamera2` live
in the system Python; the venv must see them. The project itself has **zero
third-party runtime dependencies**, so `pip install -e .` adds only the `ftd`
command:

```bash
.venv/bin/pip install -e .
```

## 3. Configure

```bash
cp .env.example .env
nano .env     # CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN, FTD_MIC_DEVICE…
chmod 600 .env
```

## 4. Doctor — verify everything with zero wheel movement

```bash
.venv/bin/ftd doctor            # offline checks
.venv/bin/ftd doctor --record   # + 1 s microphone capture
.venv/bin/ftd doctor --online   # + live clef-flash and whisper smoke tests
```

Fix every `FAIL` before any motion test. The `WARN` about verifying the
ultrasonic mounting is important: **confirm by eye that the ultrasonic module
is fixed to the chassis and does not move when the camera pans.** The code
assumes chassis-fixed (the safe assumption); if your build differs, note it,
but the code will still never synthesize side readings from camera pan.

## 5. Staged bring-up (in order; keep the power switch reachable)

Each stage must pass before the next. Prop the robot on a box (wheels free)
for stage 3.

1. **Sensors stationary** — `ftd doctor`: ultrasonic returns plausible cm at a
   known distance (hold a book at ~30 cm); grayscale returns values.
2. **Camera** — `.venv/bin/ftd doctor` camera OK; optionally
   `rpicam-still -o test.jpg` and check orientation; set
   `FTD_CAMERA_HFLIP/VFLIP` if needed.
3. **Bounded motion (wheels off the floor)** — `ftd find "anything" --dry-run`
   proves the loop; then on the box with real hardware:
   `.venv/bin/ftd find "yellow duck"` and press **Enter** within a second —
   confirm the wheels stop instantly and the run ends `STOPPED`.
4. **Braking / minimum power** — on the floor, one mission with a clear 2 m
   runway. Observe pulse distance at speed 25. If pulses stall (too weak) or
   lunge, adjust `FTD_DRIVE_SPEED` / `FTD_PULSE_S`. Measure stopping distance;
   confirm `FTD_OBSTACLE_STOP_CM=20` leaves margin.
5. **Target acquisition** — place a large obvious object (the duck) 1–1.5 m
   ahead: `ftd find "yellow duck"`. Expect approach + stop ~30 cm. Tune
   `FTD_ARRIVAL_CM`: the duck is small — check whether the sonar beam actually
   hits it or passes over (see LIMITATIONS.md).
6. **Search** — put the duck out of view to one side with open floor toward
   it. Expect scan → reposition toward open floor → acquire → approach.
7. **Voice** — `ftd listen`: record "find the yellow duck", confirm the
   interpreted target is shown before any motion.

## 6. On-device acceptance checklist (report separately from simulation)

- [ ] Voice command identifies the requested object.
- [ ] Robot approaches and stops in front of it without touching it.
- [ ] An initially out-of-view target triggers scanning and bounded exploration.
- [ ] Changing the description changes the target (duck → cup).
- [ ] Occlusion of the target → robot stops and rescans.
- [ ] Blocked route → `BLOCKED`, motors stopped.
- [ ] Pull Wi-Fi mid-mission → motors stop (provider errors → `ERROR`, stopped).
- [ ] `ftd stop` from a second SSH session stops it immediately.
- [ ] Enter / Ctrl-C stop it immediately; restart leaves it idle until re-armed.
- [ ] Measure and record: capture latency, inference latency, full cycle time
      (printed per mission in `ftd status` as `last_latency`).

## 7. Calibration notes

* Never run SunFounder's calibration scripts unless steering/camera zero has
  physically drifted; this project only commands angles on top of the vendor
  calibration offsets.
* `FTD_CAMERA_FOV_DEG` (default 62) affects bearing math; if approach veers,
  measure the real horizontal FoV (objects at known angles) and set it.
* Grayscale cliff checks stay `FTD_GRAYSCALE_CLIFF_ENABLED=false` until you
  record readings for your actual floor and a real table edge.
