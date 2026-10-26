# Known limitations (honest list)

## Verified only in simulation so far
Everything in this repo ran against mock hardware and scripted vision on
2026-10-26. **No live robot test and no live Cloudflare API call has been run
yet.** Latency numbers are therefore *measured and reported per mission at
runtime* (`ftd status` → `last_latency`), not promised in advance.

## Perception
* clef-flash returns **probabilities for typed questions only** — no bounding
  boxes, no pixel coordinates, no depth. Bearing is coarse (left/center/right,
  ≈ FoV thirds); "proximity" is an apparent-size guess, not a measurement.
* Model confidence is **not calibrated correctness**. The mission compensates
  with repeated confirmations, but a convincing decoy (a yellow chair when
  asked for a yellow duck) can still fool it; the tracker only resists
  *switching* targets mid-mission, not acquiring the wrong one initially.
* "Open floor" answers are visual hypotheses. Mirrors, glass, dark carpets,
  photos of floors, and spaces under furniture can be misjudged. The sonar
  gate catches frontal obstacles above beam height only.

## Sensing & motion
* The ultrasonic sensor is assumed **chassis-fixed and forward-facing** (stock
  PiCar-X). It cannot certify side clearance; turning pulses rely on short
  duration + low speed + the next observation. Side blind spots remain.
* A small target (a duck!) may sit **below the sonar beam**, so the "arrival"
  range may actually come from the wall behind it. The mission then stops in
  `BLOCKED` ("can't confirm the distance") rather than faking success —
  expect to tune `FTD_ARRIVAL_CM` and duck placement physically.
* No wheel odometry, no rear sensor, no depth camera, no mapping. "Recent
  view memory" is a short list of chosen directions — it prevents immediate
  repetition, not loops in large spaces. Reversing is disabled.
* The car cannot rotate in place (Ackermann steering); exploring "left" means
  a short forward arc, which needs free floor ahead.

## Voice
* Push-to-talk, plus a hands-free mode (systemd) that gates 4 s chunks on a
  *local loudness threshold* — it is not a wake word. In a noisy classroom the
  gate will pass non-command noise to Whisper (bounded cost, but quota is
  spent), and a command spoken across a chunk boundary can be cut in half —
  repeat it. The threshold needs per-room calibration (`ftd doctor --record`).
* While a mission runs, the robot is NOT listening; voice cannot stop it.
  `ftd stop`, Enter, or the power switch are the stop paths.
* Recording quality in a noisy classroom is untested; typed commands are the
  fallback.
* Whisper may mishear; the interpreted target is displayed before any motion,
  and empty/uncertain speech starts nothing.

## Safety
* All watchdogs are **software**. A frozen kernel, a shorted motor driver, or
  a crashed Python process mid-PWM cannot be caught — keep the physical power
  switch reachable. There is no verified hardware fail-safe.
* Grayscale cliff detection ships disabled; until calibrated, test only on
  flat floors away from stairs (a black floor marking is not a cliff, and a
  cliff is worse than a false positive).
* `STOP` latching, command expiry, and heartbeat stops are tested in CI with
  real threads, but their end-to-end timing on the actual Pi 3 (loaded CPU,
  GPIO latency) must be confirmed on-device.

## Cloud dependency
* No internet → no new missions (voice + vision are cloud-side). The robot
  fails safe (stops), but the demo needs working Wi-Fi and Cloudflare quota.
* Request budgets (`FTD_MAX_VISION_CALLS_*`) bound spending, and images/audio
  are sent to Cloudflare for inference — don't point the camera at anything
  that shouldn't leave the room.
