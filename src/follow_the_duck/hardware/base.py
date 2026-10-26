"""Hardware interfaces.

The rest of the project depends only on these small protocols. Real PiCar-X
implementations live in picarx_hw.py (lazy imports, Pi-only); mocks in mock.py
run anywhere. Exactly one component — the MotorSupervisor — is allowed to call
Chassis methods that move the wheels.
"""

from __future__ import annotations

import abc
import math
from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class RangeReading:
    """One ultrasonic reading. ``cm`` is None when the reading is invalid
    (no echo, zero, negative, non-finite, or out of the sensor's range).
    Invalid readings mean UNKNOWN, never 'clear space'."""

    cm: Optional[float]
    at: float  # monotonic timestamp of the read

    @staticmethod
    def classify(raw: object, at: float, min_cm: float, max_cm: float) -> "RangeReading":
        if not isinstance(raw, (int, float)) or isinstance(raw, bool):
            return RangeReading(None, at)
        value = float(raw)
        if math.isnan(value) or math.isinf(value):
            return RangeReading(None, at)
        if value < min_cm or value > max_cm:
            return RangeReading(None, at)
        return RangeReading(value, at)


class Chassis(abc.ABC):
    """Drive motors + steering + camera gimbal. Wheel motion methods must only
    ever be called by the MotorSupervisor."""

    @abc.abstractmethod
    def set_steering(self, angle_deg: float) -> None: ...

    @abc.abstractmethod
    def drive(self, speed: int, reverse: bool = False) -> None:
        """Start the drive motors at `speed` (0..100). Runs until stop()."""

    @abc.abstractmethod
    def stop(self) -> None: ...

    @abc.abstractmethod
    def set_cam_pan(self, angle_deg: float) -> None: ...

    @abc.abstractmethod
    def set_cam_tilt(self, angle_deg: float) -> None: ...

    def close(self) -> None:  # pragma: no cover - trivial default
        self.stop()


class DistanceSensor(abc.ABC):
    """Forward-facing ultrasonic sensor. On the stock PiCar-X the module is
    mounted on the chassis front and does NOT move with the camera pan; verify
    on the actual robot (`ftd doctor` prints a reminder). Pan angles therefore
    never produce side range readings in this codebase."""

    #: True only if physical inspection confirmed the sensor moves with the
    #: camera. Left False, conservatively, until proven otherwise.
    moves_with_camera: bool = False

    @abc.abstractmethod
    def read_cm(self) -> object:
        """Raw reading; may be garbage. Callers classify via RangeReading."""


class GrayscaleSensor(abc.ABC):
    """Downward grayscale line/cliff sensors. Cliff detection stays disabled
    until calibrated (a black floor marking is not a cliff)."""

    @abc.abstractmethod
    def read(self) -> List[int]: ...


class SpeakerOut(abc.ABC):
    """Optional audio feedback (beeps / spoken status)."""

    @abc.abstractmethod
    def say(self, text: str) -> None: ...

    @abc.abstractmethod
    def beep(self) -> None: ...
