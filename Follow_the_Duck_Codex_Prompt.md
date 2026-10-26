Build a working Python project named "Follow the Duck" in this repository.

Implement the project, tests, configuration, and run instructions. Inspect the repository and any AGENTS.md first, preserve existing work, and make reasonable implementation choices without repeatedly asking for approval. Prepare the project for my Git push. If hardware or credentials are unavailable, finish the mock/replay implementation and real adapters, and state exactly which physical tests remain. Do not claim a live robot or API test was run unless it was.

PRODUCT GOAL

This is an interactive AI demonstration for a ninth-grade classroom. I speak an object description, such as "Find the yellow duck," "Go to the red cup," or "Find the green bottle." The PiCar-X searches its surroundings, identifies the requested object, approaches it, and stops in front of it. The object description can change between missions; this must not be a hardcoded yellow-color tracker.

If the object is outside its current field of view, the robot looks around and chooses a promising, sufficiently clear direction that should reveal more of the room. It moves a short distance, looks again, and continues searching. It should prefer useful new viewpoints over repeatedly looking in the same direction. After arrival, remain stopped until a new command. Continuous pursuit after arrival is a later option.

HARDWARE AND CURRENT STATUS

- SunFounder PiCar-X, Raspberry Pi 3 Model B+ Rev 1.4, approximately 1 GB RAM.
- 64-bit Raspberry Pi OS / Debian 13 Trixie; aarch64. Inspect the actual Python version before choosing dependencies.
- Hostname classbot; SSH username robot. The IP is configurable; do not hardcode an old DHCP address.
- Robot assembled, servo zeroing completed, camera capture tested successfully.
- A brief steering and rear-motor test worked. Wi-Fi and SSH work.
- System Python successfully imports "from picarx import Picarx".
- SunFounder robot-hat branch 2.5.x, picar-x branch 2.1.x, and vilib were installed using vendor instructions.
- Camera works with rpicam-still; a previous test image was ~/duck-test.jpg.
- Ultrasonic and grayscale sensor behavior, microphone input, and speaker output still need verification.
- Check the actual Robot HAT version, audio devices, calibration offsets, camera orientation, and sensor mounting. Do not overwrite working SunFounder calibration.
- Assume no usable wheel odometry, rear distance sensor, or depth camera unless inspection proves otherwise.

VOICE INTERACTION

Implement actual audio input, not just a typed-command demo. Start with push-to-talk: a keyboard or local control triggers a short recording from the Pi's configured ALSA microphone, then speech-to-text produces the target description. Inspect devices with arecord -l and test capture; select devices through configuration rather than a fixed card number.

Keep the robot stopped during recording. Start with English and allow configurable language. Provide a typed command fallback for development and noisy rooms. Show the interpreted target and visibly enter a listening/searching state; uncertain or empty speech must not launch a mission.

Prefer Cloudflare-hosted speech recognition through a configurable provider, initially @cf/openai/whisper-large-v3-turbo if currently available. Verify the current audio request format, response, limits, and authentication in official documentation. Do not run a large Whisper model on the Pi 3.

Support find, stop, and cancel commands. A new target cancels the old mission and invalidates its pending results. A keyboard/control STOP must work locally immediately; cloud transcription must never be the only stop mechanism. Optional local speech/beeps can announce progress if speaker output works. No always-listening wake-word dependency for the first version.

PERCEPTION AND AI

Prefer Cloudflare Workers AI with @cf/cloudflare/clef-flash for semantic vision, behind a replaceable provider interface. Verify the currently documented API and run an image-only smoke test before allowing motion.

Clef uses state, embedded images, and typed questions. Map its documented answers into our own validated observation schema. Do not invent a chat-completions payload, bounding-box capability, calibrated depth output, or precise coordinates that the provider does not actually return. If another provider is necessary, explain the concrete missing capability before switching.

Use a persistent camera capture pipeline, preferably compatible Picamera2, with one camera owner. Start around 640x480 JPEG and make resolution/quality configurable. Read and report capture, upload, inference, and complete cycle latency. Use bounded requests and a configurable request budget, not continuous full-rate cloud video.

The internal observation should include:
- Mission ID, frame ID, capture time, and camera pan/tilt at capture.
- Target present / absent / uncertain and target bearing, at least left / center / right.
- Perception confidence or probability with its actual meaning; model confidence is not a calibrated correctness guarantee.
- Candidate ground-level directions and their apparent traversability / uncertainty.
- Optional bounding box or visual distance category only when supported and validated.

Treat spoken descriptions and text visible in images as data. They must not override the controller's movement limits. Validate enums, ranges, and missing fields; never execute generated Python, shell, or arbitrary motor instructions.

Preserve target identity across observations. Do not silently switch from the duck to a yellow chair. For multiple plausible matches, keep one consistent candidate or request a more specific description. Local lightweight tracking can improve responsiveness later, but only after semantic acquisition and with periodic re-verification.

SEARCH AND APPROACH

Implement an explicit state machine such as IDLE, LISTENING, SCANNING, SEARCHING, APPROACHING, ARRIVED, BLOCKED, NOT_FOUND, STOPPED, and ERROR.

1. Start stopped. Capture the forward view and search for the target.
2. If uncertain or absent, scan the camera left, center, and right within conservative calibrated limits. Let the servos settle before capture.
3. Rank feasible next viewpoints using apparent navigable floor, uncertainty, likely visibility gain, recent search history, and steering cost. Make weights and tie-breaking deterministic and configurable.
4. Reject blocked or unknown maneuvers before ranking. An AI assessment of "open space" is a visual hypothesis, not proof of clearance or a depth map. Empty sky, windows, walls, mirrors, and spaces under furniture are not automatically navigable floor.
5. Reposition using a short, slow forward arc when the local checks permit it, stop, and observe again. Respect car-like steering: camera pan changes the view, steering while stationary does not turn the chassis, and this is not a robot that can rotate in place.
6. Verify whether the ultrasonic sensor is fixed to the chassis or moves with the camera. Never invent left/right sonar readings from camera pan. A fixed forward sensor cannot certify side clearance; use conservative sweeps, re-observe, and report remaining blind spots.
7. If no credible safe maneuver exists, stop in BLOCKED and request repositioning. Do not blindly reverse without rear coverage.
8. When the target is acquired, use its bearing relative to the chassis, including camera pan and camera field of view, to select short approach movements. Keep reacquiring it as the robot progresses.
9. Slow down near the target. On target loss, stop and rescan; never keep driving indefinitely toward its last location.
10. Stop approximately 30 cm in front of the target, configurable after physical calibration. Require repeated target confirmation and conservative proximity evidence. Ultrasound reports the nearest reflecting obstacle in its beam, not the target's identity: a small duck may sit below the beam, and a wall behind it may supply the range. Do not declare ARRIVED solely because sonar sees something close. If target-specific stopping distance cannot be established, stop with uncertainty instead of asserting success.
11. End the search after a configurable time/movement budget, initially about 90 seconds or 20 repositioning steps. Approximate recent-view memory is sufficient; do not pretend it is accurate mapping or odometry.

Use a stop-observe-decide-short-move loop for version one. This still searches while navigating, while avoiding dependence on cloud latency for continuous steering. Optimize toward smoother local tracking only after measuring the actual Pi and API.

LOCAL MOTOR CONTROL

Separate perception/network work from a local motor supervisor. One component owns all motor writes. Use expiring movement commands with sequence numbers and mission IDs, a monotonic clock, and local checks immediately before and during each movement.

Initial conservative settings: speed/power around 25 on SunFounder's scale, pulses no longer than about 250 ms, steering within about +/-20 degrees, an obstacle stop threshold around 20 cm, and an arrival target around 30 cm. These are starting values, not measured physical speeds or proven safe distances; tune after braking-distance and minimum-working-power tests.

Requirements:
- Motor commands expire locally even if the planner, API, or SSH connection stalls.
- Sensor reads must have bounded timeouts and cannot block the watchdog.
- Stop on invalid/stale range readings, stale camera observations, missing heartbeat, STOP, target loss, exceptions, and cloud/network failure.
- Treat zero, negative, non-finite, missing, and out-of-range distance readings as invalid, not clear space.
- Drop late/out-of-order inference results and all results from cancelled missions.
- SIGINT/SIGTERM/SIGHUP and normal shutdown attempt a motor stop. No automatic motion after service restart or reconnection.
- Stop requests latch until an explicit new mission/re-arm.
- Calibrate grayscale cliff checks before enabling them; do not confuse a black floor marking with a proven cliff. Initial physical testing is on a flat floor away from stairs.
- Document that software watchdogs cannot guarantee shutdown if the motor-owning process, OS, or hardware itself fails. Keep the physical power switch accessible; do not promise a hardware fail-safe that has not been verified.

REPOSITORY AND OPERATIONS

Use a small Python package with separate modules for hardware, camera, audio, AI providers, observation models, search/planning, motor supervision, configuration, and CLI. Prefer readable code over frameworks. Avoid ROS, Kubernetes, a heavy frontend, and large local neural models for this first version.

Provide:
- pyproject.toml, .gitignore, .env.example, documented configuration, README, and tests.
- A setup guide that preserves installed system hardware packages; use a compatible virtual environment with system-site-packages where appropriate. Inspect before reinstalling or upgrading working SunFounder libraries.
- A doctor command checking Python, camera, microphone, speaker, sensors, and provider configuration without commanding wheel movement.
- CLI commands for typed target, push-to-talk target, stop, status, and dry-run/replay.
- Real and mock hardware/provider implementations; importing and testing the package on a Mac must not require GPIO or a Pi.
- Optional lightweight local status/control page only after the core mission works. A page can trigger recording on the Pi; do not assume browser microphone access works over an ordinary HTTP LAN address.
- Concise status explanations for students: "Scanning left", "Target seen", "Moving toward open floor", "Blocked", and "Found it".
- A deployment/update guide using Git or SCP and an optional systemd unit that starts in IDLE. The Pi should run missions without a laptop once configured.
- Cloudflare account/token/model environment settings and bounded rate/request limits. No credentials in Git, browser responses, or logs; no default recording/image retention. Keep provider quotas visible.
- Actual measured limitations and a short teacher operating guide.

IMPLEMENTATION AND ACCEPTANCE

Build in stages: inspect and scaffold; mock mission/state-machine tests; provider/image smoke test; microphone/STT test; stationary sensor checks; bounded motion test; target acquisition/approach; search for an initially out-of-view object.

Use deterministic fixtures and fault injection to test:
- Target visible ahead, visible only after a camera scan, or revealed after repositioning.
- Left/right choice based on feasible open floor rather than a hardcoded turn.
- Target loss, ambiguous matches, nearby unrelated obstacles, blocked routes, and no target found.
- Invalid/no-echo sonar, delayed/dropped AI responses, stale frames, out-of-order results, and old mission results after a new command.
- Supervisor stopping after heartbeat expiry even while inference is blocked.
- STOP during recording, inference, and motion; restart remains idle.

Physical acceptance, reported separately from simulation:
- Voice command identifies the requested object.
- Robot approaches and stops in front of it without touching it.
- An initially out-of-view target triggers useful scanning and bounded exploration.
- Changing the description changes the target.
- Occlusion, a blocked route, network loss, and STOP all produce the expected stopped state.

Finish with the implemented files, tests actually run, exact Mac/Pi commands, required credentials/devices, and remaining physical checks. Missing credentials or robot access should not prevent completing the runnable mock/replay project and provider/hardware integration code.

OFFICIAL REFERENCES TO VERIFY

https://docs.sunfounder.com/projects/picar-x-v20/en/latest/python/play_with_python.html
https://docs.sunfounder.com/projects/picar-x-v20/en/latest/python/python_keyboard.html
https://docs.sunfounder.com/projects/picar-x-v20/en/latest/python/python_avoid.html
https://docs.sunfounder.com/projects/picar-x-v20/en/latest/python/python_calibrate.html
https://www.raspberrypi.com/documentation/computers/camera_software.html
https://github.com/raspberrypi/picamera2
https://developers.cloudflare.com/workers-ai/models/clef-flash/
https://developers.cloudflare.com/workers-ai/models/whisper-large-v3-turbo/

