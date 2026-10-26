"""Mock hardware for development, replay, and tests. Runs on any machine.

MockDistanceSensor supports scripted sequences and fault injection (no-echo,
negative, NaN readings) so safety paths are testable without a robot.
"""

from __future__ import annotations

import threading
from typing import Callable, List, Optional, Union

from .base import Chassis, DistanceSensor, GrayscaleSensor, SpeakerOut


class MockChassis(Chassis):
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.steering_deg = 0.0
        self.pan_deg = 0.0
        self.tilt_deg = 0.0
        self.speed = 0
        self.reverse = False
        self.moving = False
        self.log: List[tuple] = []  # every actuator call, in order

    def set_steering(self, angle_deg: float) -> None:
        with self.lock:
            self.steering_deg = angle_deg
            self.log.append(("steer", angle_deg))

    def drive(self, speed: int, reverse: bool = False) -> None:
        with self.lock:
            self.speed = speed
            self.reverse = reverse
            self.moving = speed > 0
            self.log.append(("drive", speed, reverse))

    def stop(self) -> None:
        with self.lock:
            self.speed = 0
            self.moving = False
            self.log.append(("stop",))

    def set_cam_pan(self, angle_deg: float) -> None:
        with self.lock:
            self.pan_deg = angle_deg
            self.log.append(("pan", angle_deg))

    def set_cam_tilt(self, angle_deg: float) -> None:
        with self.lock:
            self.tilt_deg = angle_deg
            self.log.append(("tilt", angle_deg))

    def stop_count(self) -> int:
        return sum(1 for entry in self.log if entry[0] == "stop")


ReadingScript = Union[List[object], Callable[[], object]]


class MockDistanceSensor(DistanceSensor):
    """Returns scripted readings; the last value repeats. Use None/NaN/-1/0
    entries to inject invalid readings, or a callable for dynamic behavior."""

    def __init__(self, script: Optional[ReadingScript] = None) -> None:
        self._script = script if script is not None else [120.0]
        self._index = 0
        self._lock = threading.Lock()
        self.reads = 0

    def read_cm(self) -> object:
        with self._lock:
            self.reads += 1
            if callable(self._script):
                return self._script()
            seq = self._script
            value = seq[min(self._index, len(seq) - 1)]
            self._index += 1
            return value

    def set_script(self, script: ReadingScript) -> None:
        with self._lock:
            self._script = script
            self._index = 0


class MockGrayscale(GrayscaleSensor):
    def __init__(self, values: Optional[List[int]] = None) -> None:
        self.values = values or [500, 500, 500]

    def read(self) -> List[int]:
        return list(self.values)


class MockSpeaker(SpeakerOut):
    def __init__(self) -> None:
        self.spoken: List[str] = []
        self.beeps = 0

    def say(self, text: str) -> None:
        self.spoken.append(text)

    def beep(self) -> None:
        self.beeps += 1
