"""Mission controller: the stop-observe-decide-short-move state machine.

States: IDLE -> SCANNING -> (APPROACHING | SEARCHING) -> ... ->
ARRIVED | BLOCKED | NOT_FOUND | STOPPED | ERROR. The robot is always
stationary while capturing and while waiting on the cloud; motion happens only
in short supervised pulses between observations.

Arrival requires BOTH repeated visual confirmation that the *target* looks
very close AND a fresh valid sonar range at/below the arrival distance.
Sonar alone is never enough: ultrasound reports the nearest reflector in its
beam, not the target's identity — a small duck can sit below the beam while a
wall behind it supplies the range. When sonar and vision cannot agree within
the budget, the mission ends in BLOCKED ("stopped with uncertainty") instead
of claiming success.
"""

from __future__ import annotations

import copy
import logging
import threading
import uuid
from collections import deque
from typing import Deque, List, Optional, Tuple

from .camera.base import Camera, CameraError
from .clock import Clock
from .config import Config
from .hardware.base import Chassis
from .models import (
    Bearing,
    Frame,
    MissionState,
    MissionStatus,
    MoveCommand,
    Observation,
    ObservationError,
    Presence,
    Proximity,
)
from .perception import Perceiver
from .planner import best_feasible, rank_viewpoints
from .providers.base import ProviderError
from .statuslog import StatusBoard
from .supervisor import MotorSupervisor
from .telemetry import EventSink
from .tracker import TargetTracker

log = logging.getLogger("ftd.mission")

_MAX_CONSECUTIVE_PROVIDER_FAILURES = 3
_MAX_BLOCKED_RETRIES = 3


class MissionController:
    def __init__(
        self,
        cfg: Config,
        clock: Clock,
        chassis: Chassis,
        supervisor: MotorSupervisor,
        camera: Camera,
        perceiver: Perceiver,
        status: StatusBoard,
        sink: Optional[EventSink] = None,
    ) -> None:
        self._cfg = cfg
        self._clock = clock
        self._chassis = chassis
        self._supervisor = supervisor
        self._camera = camera
        self._perceiver = perceiver
        self._status = status
        self._sink = sink
        self._stop_requested = threading.Event()
        self._mission_id = ""
        self._frame_counter = 0
        self._seq = 0
        self._last_accepted_frame = -1
        self._cur_pan = 0.0
        self._pending_scan: List[Observation] = []

    # ------------------------------------------------------------- controls

    def request_stop(self) -> None:
        """Local STOP: immediate motor stop + latch; the mission loop then
        exits in STOPPED. Never depends on the cloud."""
        self._stop_requested.set()
        self._supervisor.stop("STOP requested")

    def cancel_current(self) -> None:
        """A new target invalidates the old mission and its pending results."""
        self._stop_requested.set()
        self._supervisor.stop("mission cancelled")

    # ------------------------------------------------------------------ run

    def run(self, target: str) -> MissionStatus:
        self._mission_id = uuid.uuid4().hex[:12]
        self._stop_requested.clear()
        self._last_accepted_frame = -1
        self._seq = 0
        started = self._clock.now()
        vision_calls = 0
        steps = 0
        provider_failures = 0
        blocked_retries = 0
        history: Deque[str] = deque(maxlen=self._cfg.history_size)
        tracker = TargetTracker(self._cfg)
        arrival_confirms = 0

        self._supervisor.arm(self._mission_id)
        self._emit("mission_start", target=target)
        self._status.update(
            MissionState.SCANNING,
            "Searching for: %s" % target,
            mission_id=self._mission_id,
            target=target,
            steps_taken=0,
            vision_calls=0,
            stop_reason="",
        )
        state = MissionState.SCANNING
        approach_obs: Optional[Observation] = None

        def finish(final: MissionState, message: str) -> MissionStatus:
            self._supervisor.stop("mission finished: %s" % final.value)
            self._center_gimbal()
            self._emit("mission_end", state=final.value, message=message,
                       steps=steps, vision_calls=vision_calls,
                       elapsed_s=round(self._clock.now() - started, 1))
            self._status.update(
                final,
                message,
                steps_taken=steps,
                vision_calls=vision_calls,
                elapsed_s=round(self._clock.now() - started, 1),
            )
            return copy.deepcopy(self._status.status)

        while True:
            if self._stop_requested.is_set():
                return finish(MissionState.STOPPED, "Stopped.")
            if self._clock.now() - started > self._cfg.mission_time_budget_s:
                return finish(MissionState.NOT_FOUND, "Time is up — I could not find it.")
            if steps >= self._cfg.max_reposition_steps:
                return finish(MissionState.NOT_FOUND, "Search limit reached — not found.")
            if vision_calls >= self._cfg.max_vision_calls_per_mission:
                return finish(MissionState.NOT_FOUND, "Looked as much as allowed — not found.")

            if state is MissionState.SCANNING:
                self._status.update(MissionState.SCANNING, "Scanning for the target...")
                observations: List[Observation] = []
                found: Optional[Observation] = None
                for pan in self._scan_order():
                    if self._stop_requested.is_set():
                        return finish(MissionState.STOPPED, "Stopped.")
                    if vision_calls >= self._cfg.max_vision_calls_per_mission:
                        break
                    self._status.update(message="Scanning %s" % _pan_name(pan))
                    try:
                        obs = self._observe_at(pan, target)
                        vision_calls += 1
                        provider_failures = 0
                    except ProviderError as exc:
                        vision_calls += 1
                        provider_failures += 1
                        log.warning("vision failed: %s", exc)
                        if provider_failures >= _MAX_CONSECUTIVE_PROVIDER_FAILURES:
                            return finish(
                                MissionState.ERROR,
                                "I cannot reach my vision service. Stopping.",
                            )
                        continue
                    except (ObservationError, CameraError) as exc:
                        log.warning("observation discarded: %s", exc)
                        continue
                    if not self._accept(obs):
                        continue
                    observations.append(obs)
                    verdict = tracker.update(obs)
                    # First sighting: confirm at the same pan before moving on,
                    # so unrelated empty pans don't reset the streak.
                    confirm_attempts = 0
                    while (
                        verdict in ("hit", "uncertain")
                        and not tracker.acquired
                        and confirm_attempts < self._cfg.confirmations_to_acquire + 1
                        and tracker.hits > 0
                        and vision_calls < self._cfg.max_vision_calls_per_mission
                    ):
                        confirm_attempts += 1
                        try:
                            obs = self._observe_at(pan, target)
                            vision_calls += 1
                        except (ProviderError, ObservationError, CameraError) as exc:
                            vision_calls += 1
                            log.warning("confirm failed: %s", exc)
                            break
                        if not self._accept(obs):
                            break
                        observations.append(obs)
                        verdict = tracker.update(obs)
                    if tracker.acquired:
                        found = tracker.last_good
                        break
                self._status.update(vision_calls=vision_calls)
                if found is not None:
                    approach_obs = found
                    arrival_confirms = 0
                    self._emit("state", state="APPROACHING",
                               message="target acquired",
                               bearing=found.bearing.value, pan_deg=found.pan_deg)
                    self._status.update(
                        MissionState.APPROACHING, "Target seen! Moving closer.", say=True
                    )
                    state = MissionState.APPROACHING
                    continue
                if tracker.ambiguous_seen and not tracker.acquired:
                    self._status.update(
                        message="I may see more than one match — picking one and checking."
                    )
                # Not acquired: pick a new viewpoint.
                state = MissionState.SEARCHING
                self._pending_scan = observations
                continue

            if state is MissionState.SEARCHING:
                candidates = rank_viewpoints(self._pending_scan, list(history), self._cfg)
                choice = best_feasible(candidates)
                self._emit("decision", decision="reposition_ranking",
                           candidates=[{
                               "direction": c.direction, "score": c.score,
                               "floor_p": round(c.floor_p, 3),
                               "feasible": c.feasible, "reason": c.reason,
                           } for c in candidates],
                           chosen=choice.direction if choice else None)
                if choice is None:
                    message = "Blocked — no clear floor to explore. Please reposition me."
                    if blocked_retries:
                        message += (" (Note: earlier moves were refused for "
                                    "invalid distance readings — also check the "
                                    "ultrasonic sensor with: ftd doctor.)")
                    return finish(MissionState.BLOCKED, message)
                self._status.update(
                    MissionState.SEARCHING,
                    "Moving toward open floor (%s)." % choice.direction,
                )
                ok, reason = self._move_pulse(choice.steer_deg, self._cfg.drive_speed)
                if ok:
                    steps += 1
                    blocked_retries = 0
                    history.append(choice.direction)
                    self._status.update(steps_taken=steps)
                    state = MissionState.SCANNING
                    continue
                # Move refused or safety-stopped (obstacle, invalid sonar...).
                if self._stop_requested.is_set():
                    return finish(MissionState.STOPPED, "Stopped.")
                blocked_retries += 1
                log.info("move rejected (%s); retry %d", reason, blocked_retries)
                if blocked_retries >= _MAX_BLOCKED_RETRIES:
                    if "range" in reason:
                        return finish(
                            MissionState.BLOCKED,
                            "Blocked — my distance sensor is not giving valid "
                            "readings, so I will not move. Check the ultrasonic "
                            "sensor (run: ftd doctor).",
                        )
                    return finish(
                        MissionState.BLOCKED,
                        "Blocked — something is too close in front. Please reposition me.",
                    )
                self._supervisor.arm(self._mission_id)  # clear safety latch, retry
                history.append(choice.direction)  # don't keep picking it
                state = MissionState.SCANNING
                continue

            if state is MissionState.APPROACHING:
                assert approach_obs is not None
                # Arrival check: repeated *visual* closeness + sonar agreement.
                sonar_cm = self._supervisor.fresh_valid_range_cm()
                visually_close = (
                    approach_obs.presence is Presence.PRESENT
                    and approach_obs.proximity is Proximity.VERY_CLOSE
                )
                if visually_close and sonar_cm is not None and sonar_cm <= self._cfg.arrival_cm:
                    arrival_confirms += 1
                    if arrival_confirms >= self._cfg.confirmations_to_arrive:
                        return finish(MissionState.ARRIVED, "Found it! I am at the target.")
                elif visually_close and sonar_cm is not None and sonar_cm > self._cfg.arrival_cm:
                    # Target looks close but sonar may be ranging past a small
                    # object below the beam. Creep carefully; if we run out of
                    # budget this ends as uncertain, not as fake success.
                    arrival_confirms = 0
                else:
                    arrival_confirms = 0

                # If sonar says something is at/below arrival range but the
                # target does NOT look close, that's an unrelated obstacle.
                if (
                    sonar_cm is not None
                    and sonar_cm <= self._cfg.arrival_cm
                    and not visually_close
                ):
                    return finish(
                        MissionState.BLOCKED,
                        "Something is close ahead, but it does not look like the "
                        "target. Stopping to be safe.",
                    )

                bearing = approach_obs.chassis_bearing_deg(self._cfg.camera_fov_deg)
                steer = 0.0 if bearing is None else max(
                    -self._cfg.max_steer_deg, min(self._cfg.max_steer_deg, bearing)
                )
                near = approach_obs.proximity in (Proximity.NEAR, Proximity.VERY_CLOSE)
                speed = self._cfg.approach_speed_near if near else self._cfg.drive_speed
                # A far, centered target gets several consecutive pulses per
                # (slow) cloud look so approach visibly progresses; every
                # pulse is still individually sonar-gated by the supervisor.
                pulses = 1
                if not near and approach_obs.bearing is Bearing.CENTER:
                    pulses = max(1, self._cfg.approach_pulses_far)
                ok, reason = True, ""
                for _ in range(pulses):
                    ok, reason = self._move_pulse(steer, speed)
                    if not ok:
                        break
                    steps += 1
                    blocked_retries = 0
                    self._status.update(steps_taken=steps,
                                        message="Moving toward the target.")
                if not ok:
                    if self._stop_requested.is_set():
                        return finish(MissionState.STOPPED, "Stopped.")
                    if visually_close:
                        # Can't creep closer (sonar floor reached) but vision
                        # hasn't double-confirmed: stop with uncertainty.
                        return finish(
                            MissionState.BLOCKED,
                            "I am close, but I cannot confirm the exact distance. "
                            "Stopping here.",
                        )
                    blocked_retries += 1
                    if blocked_retries >= _MAX_BLOCKED_RETRIES:
                        return finish(
                            MissionState.BLOCKED,
                            "Blocked on the way to the target. Please help me.",
                        )
                    self._supervisor.arm(self._mission_id)

                # Re-acquire after the pulse: look where we last saw it.
                look_pan = 0.0
                if bearing is not None:
                    limit = max(abs(a) for a in self._cfg.scan_angles())
                    look_pan = max(-limit, min(limit, bearing - steer))
                try:
                    obs = self._observe_at(look_pan, target)
                    vision_calls += 1
                    provider_failures = 0
                except ProviderError as exc:
                    vision_calls += 1
                    provider_failures += 1
                    log.warning("vision failed during approach: %s", exc)
                    if provider_failures >= _MAX_CONSECUTIVE_PROVIDER_FAILURES:
                        return finish(
                            MissionState.ERROR,
                            "I cannot reach my vision service. Stopping.",
                        )
                    continue
                except (ObservationError, CameraError) as exc:
                    log.warning("observation discarded during approach: %s", exc)
                    continue
                finally:
                    self._status.update(vision_calls=vision_calls)
                if not self._accept(obs):
                    continue
                verdict = tracker.update(obs)
                if verdict in ("hit", "acquired"):
                    approach_obs = tracker.last_good
                    continue
                if verdict == "suspect":
                    self._status.update(
                        message="I see a lookalike — staying with my first pick."
                    )
                    continue
                if verdict in ("miss", "lost", "uncertain"):
                    # Target not confirmed: stop and rescan before moving on.
                    self._status.update(message="I lost sight of it. Rescanning.")
                    reacquired = False
                    for _ in range(self._cfg.rescan_attempts_on_loss):
                        if self._stop_requested.is_set():
                            return finish(MissionState.STOPPED, "Stopped.")
                        for pan in self._scan_order():
                            if vision_calls >= self._cfg.max_vision_calls_per_mission:
                                break
                            try:
                                obs = self._observe_at(pan, target)
                                vision_calls += 1
                            except (ProviderError, ObservationError, CameraError):
                                vision_calls += 1
                                continue
                            if not self._accept(obs):
                                continue
                            if tracker.update(obs) in ("hit", "acquired") and tracker.acquired:
                                approach_obs = tracker.last_good
                                reacquired = True
                                break
                        if reacquired:
                            break
                    self._status.update(vision_calls=vision_calls)
                    if reacquired:
                        arrival_confirms = 0
                        continue
                    tracker.reset()
                    state = MissionState.SCANNING
                    continue

    # -------------------------------------------------------------- helpers

    def _scan_order(self) -> List[float]:
        # Center first (cheapest, most likely useful), then sides.
        return sorted(self._cfg.scan_angles(), key=lambda a: (abs(a), a))

    def _center_gimbal(self) -> None:
        try:
            self._chassis.set_cam_pan(0.0)
            self._chassis.set_cam_tilt(self._cfg.tilt_deg)
            self._cur_pan = 0.0
        except Exception:
            pass

    def _observe_at(self, pan_deg: float, target: str) -> Observation:
        """Move the gimbal, settle, capture, perceive. Robot stays stopped."""
        self._supervisor.heartbeat()
        if abs(pan_deg - self._cur_pan) > 0.5:
            self._chassis.set_cam_pan(pan_deg)
            self._chassis.set_cam_tilt(self._cfg.tilt_deg)
            self._cur_pan = pan_deg
            self._clock.sleep(self._cfg.servo_settle_s)
        self._frame_counter += 1
        frame = Frame(
            frame_id=self._frame_counter,
            jpeg=self._camera.capture_jpeg(),
            captured_at=self._clock.now(),
            pan_deg=pan_deg,
            tilt_deg=self._cfg.tilt_deg,
        )
        observation, latency = self._perceiver.perceive(frame, target, self._mission_id)
        self._supervisor.heartbeat()
        self._status.update(last_latency=latency.as_dict())
        self._emit(
            "observation",
            frame_id=frame.frame_id,
            pan_deg=pan_deg,
            target=target,
            presence=observation.presence.value,
            confidence=round(observation.confidence, 3),
            bearing=observation.bearing.value,
            bearing_confidence=round(observation.bearing_confidence, 3),
            proximity=observation.proximity.value,
            floor_clear={k: round(v, 3) for k, v in observation.floor_clear.items()},
            ambiguity=round(observation.ambiguity, 3),
            latency=latency.as_dict(),
            jpeg=frame.jpeg,
        )
        return observation

    def _accept(self, obs: Observation) -> bool:
        """Drop results from other/cancelled missions, out-of-order frames,
        and stale observations."""
        if obs.mission_id != self._mission_id:
            log.info("dropping observation from mission %s", obs.mission_id)
            return False
        if obs.frame_id <= self._last_accepted_frame:
            log.info("dropping out-of-order frame %d", obs.frame_id)
            return False
        if self._clock.now() - obs.captured_at > self._cfg.observation_stale_s:
            log.info("dropping stale frame %d", obs.frame_id)
            return False
        self._last_accepted_frame = obs.frame_id
        return True

    def _move_pulse(self, steer_deg: float, speed: int) -> Tuple[bool, str]:
        # Prove liveness right before asking for motion: anything slow since
        # the last observation (telemetry, status writes, speech) must never
        # surface as a spurious "heartbeat expired" refusal.
        self._supervisor.heartbeat()
        self._seq += 1
        command = MoveCommand(
            mission_id=self._mission_id,
            seq=self._seq,
            speed=speed,
            steer_deg=steer_deg,
            duration_s=self._cfg.pulse_s,
            issued_at=self._clock.now(),
            ttl_s=self._cfg.command_ttl_s,
        )
        ok, reason = self._supervisor.execute(command)
        self._emit("move", seq=self._seq, steer_deg=round(steer_deg, 1),
                   speed=speed, duration_s=self._cfg.pulse_s, ok=ok, reason=reason,
                   range_cm=self._supervisor.fresh_valid_range_cm())
        return ok, reason

    def _emit(self, kind: str, **fields) -> None:
        """Telemetry is strictly best-effort; it can never break a mission."""
        if self._sink is None:
            return
        try:
            event: dict = {
                "kind": kind,
                "mission_id": self._mission_id,
                "t": round(self._clock.now(), 3),
            }
            event.update(fields)
            self._sink.emit(event)
        except Exception:
            pass


def _pan_name(pan_deg: float) -> str:
    if pan_deg < -10:
        return "left"
    if pan_deg > 10:
        return "right"
    return "ahead"
