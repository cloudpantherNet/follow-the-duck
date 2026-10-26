"""Motor supervisor: the single owner of all wheel-motor writes.

Safety model
------------
* Commands carry mission id + sequence number and expire locally (ttl), so a
  stalled planner, cloud call, or SSH session can never leave motors running.
* A sensor poller thread caches ultrasonic readings with timestamps; the
  watchdog only reads the cache, so a blocking sensor read can never block
  the watchdog. Stale or invalid cache means UNKNOWN -> stop, never "clear".
* Zero, negative, NaN, inf, missing, and out-of-range readings are invalid.
* Heartbeats from the mission loop must arrive within heartbeat_timeout_s;
  otherwise motors stop even while inference is blocked (watchdog thread).
* Every safety stop LATCHES: no further movement until an explicit arm().
  STOP latches the same way and survives process restart (restart starts
  disarmed and idle; nothing moves without a new mission).
* Reverse is rejected unless explicitly enabled — there is no rear sensor.
* Grayscale cliff checks run only when enabled after calibration.

Honest limitation: this is a *software* watchdog. If this process, the OS, or
the motor HAT itself fails, it cannot guarantee a stop. Keep the physical
power switch within reach during live tests.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .clock import Clock
from .config import Config
from .hardware.base import Chassis, DistanceSensor, GrayscaleSensor, RangeReading
from .models import MoveCommand


@dataclass
class _ActiveMove:
    command: MoveCommand
    started_at: float
    ends_at: float


class SensorPoller:
    """Polls the distance (and optionally grayscale) sensors on its own
    thread and caches classified readings. poll_once() is exposed for
    deterministic tests."""

    def __init__(
        self,
        sensor: Optional[DistanceSensor],
        cfg: Config,
        clock: Clock,
        grayscale: Optional[GrayscaleSensor] = None,
    ) -> None:
        self._sensor = sensor
        self._grayscale = grayscale
        self._cfg = cfg
        self._clock = clock
        self._lock = threading.Lock()
        self._latest: Optional[RangeReading] = None
        self._latest_gray: Optional[List[int]] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def poll_once(self) -> None:
        now = self._clock.now()
        if self._sensor is not None:
            try:
                raw = self._sensor.read_cm()
            except Exception:
                raw = None
            reading = RangeReading.classify(
                raw, now, self._cfg.range_min_valid_cm, self._cfg.range_max_valid_cm
            )
            with self._lock:
                self._latest = reading
        if self._grayscale is not None and self._cfg.grayscale_cliff_enabled:
            try:
                values = self._grayscale.read()
            except Exception:
                values = []
            with self._lock:
                self._latest_gray = values

    def latest(self) -> Optional[RangeReading]:
        with self._lock:
            return self._latest

    def latest_grayscale(self) -> Optional[List[int]]:
        with self._lock:
            return self._latest_gray

    def start(self, interval_s: float = 0.1) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()

        def run() -> None:
            while not self._stop_event.is_set():
                self.poll_once()
                self._stop_event.wait(interval_s)

        self._thread = threading.Thread(target=run, name="ftd-sensors", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


class MotorSupervisor:
    """Thread-safe supervisor core plus an optional watchdog thread."""

    def __init__(
        self,
        chassis: Chassis,
        poller: SensorPoller,
        cfg: Config,
        clock: Clock,
    ) -> None:
        self._chassis = chassis
        self._poller = poller
        self._cfg = cfg
        self._clock = clock
        self._lock = threading.RLock()
        self._armed_mission: Optional[str] = None
        self._last_seq = -1
        self._latched_reason: Optional[str] = None  # latched on boot: disarmed
        self._last_heartbeat: Optional[float] = None
        self._active: Optional[_ActiveMove] = None
        self._watchdog: Optional[threading.Thread] = None
        self._watchdog_stop = threading.Event()
        self.moves_completed = 0
        # Boot state: no mission armed, motors assumed stopped. Explicitly
        # stop once so a crashed previous run cannot leave wheels spinning.
        self._safe_stop("startup")

    # ------------------------------------------------------------------ API

    def arm(self, mission_id: str) -> None:
        """Explicitly re-arm for a new mission. The only way to clear a latch."""
        with self._lock:
            self._halt_motors()
            self._armed_mission = mission_id
            self._last_seq = -1
            self._latched_reason = None
            self._last_heartbeat = self._clock.now()

    def heartbeat(self) -> None:
        with self._lock:
            self._last_heartbeat = self._clock.now()

    def stop(self, reason: str) -> None:
        """Latching stop. Safe to call from any thread / signal handler."""
        with self._lock:
            self._safe_stop(reason)

    def submit(self, cmd: MoveCommand) -> Tuple[bool, str]:
        """Validate and start a movement. Returns (accepted, reason)."""
        with self._lock:
            now = self._clock.now()
            if self._latched_reason is not None:
                return False, "latched: %s" % self._latched_reason
            if cmd.mission_id != self._armed_mission:
                return False, "wrong mission (armed=%s)" % self._armed_mission
            if cmd.seq <= self._last_seq:
                return False, "stale sequence %d <= %d" % (cmd.seq, self._last_seq)
            if cmd.expired(now):
                return False, "command expired"
            if cmd.reverse and not self._cfg.allow_reverse:
                return False, "reverse disabled (no rear sensor coverage)"
            if self._active is not None:
                return False, "move already in progress"
            if self._heartbeat_expired(now):
                self._safe_stop("heartbeat expired")
                return False, "heartbeat expired"
            ok, why = self._range_permits(now, forward=not cmd.reverse)
            if not ok:
                return False, why

            speed = int(max(0, min(self._cfg.max_speed, cmd.speed)))
            steer = max(-self._cfg.max_steer_deg,
                        min(self._cfg.max_steer_deg, cmd.steer_deg))
            duration = max(0.02, min(self._cfg.pulse_s, cmd.duration_s))

            self._last_seq = cmd.seq
            self._chassis.set_steering(steer)
            self._chassis.drive(speed, reverse=cmd.reverse)
            self._active = _ActiveMove(cmd, started_at=now, ends_at=now + duration)
            return True, "started"

    def tick(self) -> None:
        """One watchdog pass. Called by the watchdog thread and by execute();
        idempotent and cheap (reads only cached sensor values)."""
        with self._lock:
            now = self._clock.now()
            if self._active is None:
                # Idle with motors stopped is already safe; submit() refuses
                # to start anything without a fresh heartbeat.
                return
            if self._heartbeat_expired(now):
                self._safe_stop("heartbeat expired")
                return
            ok, why = self._range_permits(now, forward=not self._active.command.reverse)
            if not ok:
                self._safe_stop(why)
                return
            if self._cliff_detected():
                self._safe_stop("cliff suspected (grayscale)")
                return
            if now >= self._active.ends_at:
                self._chassis.stop()
                self._active = None
                self.moves_completed += 1

    def execute(self, cmd: MoveCommand, tick_interval_s: Optional[float] = None) -> Tuple[bool, str]:
        """Submit a command and block (via clock.sleep) until it finishes or
        is safety-stopped. Convenience for the mission loop."""
        accepted, reason = self.submit(cmd)
        if not accepted:
            return False, reason
        interval = tick_interval_s or self._cfg.watchdog_interval_s
        while True:
            self._clock.sleep(interval)
            self.heartbeat()  # the mission loop is alive while it waits here
            self.tick()
            if self.active_move() is None:
                reason = self.latched_reason()
                if reason is not None:
                    return False, "stopped: %s" % reason
                return True, "completed"

    # -------------------------------------------------------------- queries

    def latched_reason(self) -> Optional[str]:
        with self._lock:
            return self._latched_reason

    def active_move(self) -> Optional[MoveCommand]:
        with self._lock:
            return self._active.command if self._active else None

    def latest_range(self) -> Optional[RangeReading]:
        return self._poller.latest()

    def fresh_valid_range_cm(self) -> Optional[float]:
        """Current range in cm iff the cached reading is valid and fresh."""
        reading = self._poller.latest()
        if reading is None or reading.cm is None:
            return None
        if self._clock.now() - reading.at > self._cfg.range_stale_s:
            return None
        return reading.cm

    # ------------------------------------------------------------ internals

    def _heartbeat_expired(self, now: float) -> bool:
        if self._last_heartbeat is None:
            return True
        return now - self._last_heartbeat > self._cfg.heartbeat_timeout_s

    def _range_permits(self, now: float, forward: bool) -> Tuple[bool, str]:
        if not forward:
            return False, "reverse not permitted"
        reading = self._poller.latest()
        if reading is None:
            return False, "no range reading"
        if now - reading.at > self._cfg.range_stale_s:
            return False, "range reading stale"
        if reading.cm is None:
            return False, "invalid range reading"
        if reading.cm < self._cfg.obstacle_stop_cm:
            return False, "obstacle at %.0f cm" % reading.cm
        return True, "ok"

    def _cliff_detected(self) -> bool:
        if not self._cfg.grayscale_cliff_enabled:
            return False
        values = self._poller.latest_grayscale()
        if not values:
            return False
        # Calibrated threshold required before enabling; very low reflectance
        # on all sensors is treated as a suspected drop-off.
        return all(v < 100 for v in values)

    def _halt_motors(self) -> None:
        try:
            self._chassis.stop()
        except Exception:
            pass
        self._active = None

    def _safe_stop(self, reason: str) -> None:
        self._halt_motors()
        if self._latched_reason is None:
            self._latched_reason = reason

    # ------------------------------------------------------------- watchdog

    def start_watchdog(self) -> None:
        """Run tick() on a dedicated thread so motors stop on heartbeat
        expiry even if the mission loop is stuck inside a network call."""
        if self._watchdog is not None:
            return
        self._watchdog_stop.clear()

        def run() -> None:
            while not self._watchdog_stop.is_set():
                self.tick()
                self._watchdog_stop.wait(self._cfg.watchdog_interval_s)

        self._watchdog = threading.Thread(target=run, name="ftd-watchdog", daemon=True)
        self._watchdog.start()

    def shutdown(self) -> None:
        self.stop("shutdown")
        self._watchdog_stop.set()
        if self._watchdog is not None:
            self._watchdog.join(timeout=2.0)
            self._watchdog = None
