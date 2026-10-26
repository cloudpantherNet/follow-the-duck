"""Core data models: observations, movement commands, mission states.

Everything that crosses a trust boundary (cloud model answers, transcribed
speech) is validated here into closed enums and bounded numbers before any
part of the controller may act on it. Free text never reaches motor code.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional


class MissionState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    SCANNING = "SCANNING"
    SEARCHING = "SEARCHING"
    APPROACHING = "APPROACHING"
    ARRIVED = "ARRIVED"
    BLOCKED = "BLOCKED"
    NOT_FOUND = "NOT_FOUND"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


TERMINAL_STATES = {
    MissionState.ARRIVED,
    MissionState.BLOCKED,
    MissionState.NOT_FOUND,
    MissionState.STOPPED,
    MissionState.ERROR,
}


class Presence(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"
    UNCERTAIN = "uncertain"


class Bearing(str, Enum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    UNKNOWN = "unknown"


class Proximity(str, Enum):
    VERY_CLOSE = "very_close"  # roughly within arm's reach of the camera
    NEAR = "near"
    MID = "mid"
    FAR = "far"
    UNKNOWN = "unknown"


class ObservationError(ValueError):
    """Raised when a provider result cannot be validated into an Observation."""


def _check_prob(name: str, value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ObservationError("%s is not a number: %r" % (name, value))
    v = float(value)
    if math.isnan(v) or math.isinf(v) or not (0.0 <= v <= 1.0):
        raise ObservationError("%s out of range [0,1]: %r" % (name, value))
    return v


@dataclass(frozen=True)
class Observation:
    """One validated semantic look at the world.

    ``confidence`` is the vision model's own probability. It is NOT a
    calibrated correctness guarantee — treat it as a hint, which is why the
    mission requires repeated confirmations and sonar evidence before acting
    on it decisively.
    """

    mission_id: str
    frame_id: int
    captured_at: float          # monotonic time of capture
    pan_deg: float              # camera pan at capture (chassis-relative)
    tilt_deg: float
    presence: Presence
    confidence: float           # P(target visible) from the model, 0..1
    bearing: Bearing            # within the image
    bearing_confidence: float
    proximity: Proximity        # visual size category; a hypothesis, not depth
    floor_clear: Dict[str, float]  # 'left'/'center'/'right' -> P(clear floor)
    ambiguity: float            # P(multiple plausible matches)

    def chassis_bearing_deg(self, fov_deg: float) -> Optional[float]:
        """Approximate target bearing relative to the chassis, combining the
        camera pan with the coarse in-image bearing (about a third of FoV)."""
        if self.presence is Presence.ABSENT or self.bearing is Bearing.UNKNOWN:
            return None
        offset = {
            Bearing.LEFT: -fov_deg / 3.0,
            Bearing.CENTER: 0.0,
            Bearing.RIGHT: fov_deg / 3.0,
        }[self.bearing]
        return self.pan_deg + offset


def validate_observation(raw: Dict[str, object]) -> Observation:
    """Validate a raw observation dict (as produced by a provider adapter)
    into an Observation. Rejects missing fields, bad enums, bad ranges."""
    try:
        mission_id = str(raw["mission_id"])
        frame_id = int(raw["frame_id"])  # type: ignore[arg-type]
        captured_at = float(raw["captured_at"])  # type: ignore[arg-type]
        pan_deg = float(raw["pan_deg"])  # type: ignore[arg-type]
        tilt_deg = float(raw.get("tilt_deg", 0.0))  # type: ignore[arg-type]
    except (KeyError, TypeError, ValueError) as exc:
        raise ObservationError("bad observation header: %s" % exc)
    if frame_id < 0 or not math.isfinite(captured_at) or not math.isfinite(pan_deg):
        raise ObservationError("non-finite or negative observation header")
    if abs(pan_deg) > 90 or abs(tilt_deg) > 90:
        raise ObservationError("camera angle out of range")

    try:
        presence = Presence(str(raw["presence"]))
        bearing = Bearing(str(raw.get("bearing", "unknown")))
        proximity = Proximity(str(raw.get("proximity", "unknown")))
    except (KeyError, ValueError) as exc:
        raise ObservationError("bad enum: %s" % exc)

    confidence = _check_prob("confidence", raw.get("confidence", 0.0))
    bearing_confidence = _check_prob(
        "bearing_confidence", raw.get("bearing_confidence", 0.0)
    )
    ambiguity = _check_prob("ambiguity", raw.get("ambiguity", 0.0))

    floor_raw = raw.get("floor_clear", {})
    if not isinstance(floor_raw, dict):
        raise ObservationError("floor_clear must be a mapping")
    floor_clear: Dict[str, float] = {}
    for key in ("left", "center", "right"):
        if key in floor_raw:
            floor_clear[key] = _check_prob("floor_clear.%s" % key, floor_raw[key])

    return Observation(
        mission_id=mission_id,
        frame_id=frame_id,
        captured_at=captured_at,
        pan_deg=pan_deg,
        tilt_deg=tilt_deg,
        presence=presence,
        confidence=confidence,
        bearing=bearing,
        bearing_confidence=bearing_confidence,
        proximity=proximity,
        floor_clear=floor_clear,
        ambiguity=ambiguity,
    )


@dataclass(frozen=True)
class MoveCommand:
    """A single short movement request for the motor supervisor.

    Commands expire (ttl) and carry mission id + sequence number so the
    supervisor can drop stale, out-of-order, or cancelled-mission requests
    even if the planner or the network stalls.
    """

    mission_id: str
    seq: int
    speed: int            # requested power, SunFounder 0..100 scale
    steer_deg: float
    duration_s: float
    issued_at: float      # monotonic
    ttl_s: float = 1.5
    reverse: bool = False

    def expired(self, now: float) -> bool:
        return now > self.issued_at + self.ttl_s


@dataclass(frozen=True)
class Frame:
    """A captured camera frame plus the gimbal pose at capture time."""

    frame_id: int
    jpeg: bytes
    captured_at: float   # monotonic
    pan_deg: float
    tilt_deg: float
    content_type: str = "image/jpeg"


@dataclass
class LatencyReport:
    """Measured timings for one perceive cycle (reported, never guessed)."""

    capture_s: float = 0.0
    inference_s: float = 0.0  # upload + model time, measured around the HTTP call
    total_s: float = 0.0

    def as_dict(self) -> Dict[str, float]:
        return {
            "capture_s": round(self.capture_s, 3),
            "inference_s": round(self.inference_s, 3),
            "total_s": round(self.total_s, 3),
        }


@dataclass
class MissionStatus:
    """Snapshot written for `ftd status` and the student-facing narration."""

    mission_id: str = ""
    target: str = ""
    state: MissionState = MissionState.IDLE
    message: str = "Idle"
    steps_taken: int = 0
    vision_calls: int = 0
    elapsed_s: float = 0.0
    last_latency: Dict[str, float] = field(default_factory=dict)
    stop_reason: str = ""
