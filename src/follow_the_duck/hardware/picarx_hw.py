"""Real SunFounder PiCar-X adapters.

Imports of ``picarx`` / ``robot_hat`` happen lazily inside the constructor so
this module can be *imported* anywhere (tests, Mac) and only fails when a real
robot object is actually requested. API per SunFounder picar-x 2.x docs:
Picarx().forward/backward/stop, set_dir_servo_angle, set_cam_pan_angle,
set_cam_tilt_angle, ultrasonic.read(), get_grayscale_data().

This adapter never changes SunFounder calibration offsets; it only commands
angles/speeds on top of the vendor's own calibration.
"""

from __future__ import annotations

import subprocess
from typing import List, Optional

from .base import Chassis, DistanceSensor, GrayscaleSensor, SpeakerOut


class PicarxChassis(Chassis):
    # Conservative hard clamps regardless of configuration.
    STEER_LIMIT_DEG = 30.0
    PAN_LIMIT_DEG = 80.0
    TILT_LIMIT_DEG = 30.0

    def __init__(self, px: Optional[object] = None) -> None:
        if px is None:
            from picarx import Picarx  # Pi-only; lazy on purpose

            px = Picarx()
        self._px = px

    @staticmethod
    def _clamp(value: float, limit: float) -> float:
        return max(-limit, min(limit, value))

    def set_steering(self, angle_deg: float) -> None:
        self._px.set_dir_servo_angle(self._clamp(angle_deg, self.STEER_LIMIT_DEG))

    def drive(self, speed: int, reverse: bool = False) -> None:
        speed = int(max(0, min(100, speed)))
        if reverse:
            self._px.backward(speed)
        else:
            self._px.forward(speed)

    def stop(self) -> None:
        self._px.stop()

    def set_cam_pan(self, angle_deg: float) -> None:
        self._px.set_cam_pan_angle(self._clamp(angle_deg, self.PAN_LIMIT_DEG))

    def set_cam_tilt(self, angle_deg: float) -> None:
        self._px.set_cam_tilt_angle(self._clamp(angle_deg, self.TILT_LIMIT_DEG))

    def close(self) -> None:
        try:
            self.stop()
        finally:
            self.set_steering(0.0)


class PicarxUltrasonic(DistanceSensor):
    """SunFounder's ultrasonic.read() returns centimeters, or -1/-2 on
    failure. The module is chassis-mounted on the stock build (verify on the
    robot); it does not pan with the camera, so it can never certify side
    clearance."""

    moves_with_camera = False

    def __init__(self, px: object) -> None:
        self._px = px

    def read_cm(self) -> object:
        try:
            return self._px.ultrasonic.read()
        except Exception:
            return None


class PicarxGrayscale(GrayscaleSensor):
    def __init__(self, px: object) -> None:
        self._px = px

    def read(self) -> List[int]:
        try:
            data = self._px.get_grayscale_data()
            return [int(v) for v in data]
        except Exception:
            return []


class EspeakSpeaker(SpeakerOut):
    """Spoken status via espeak if installed; silently no-ops otherwise so
    audio problems can never take a mission down."""

    def __init__(self, device: str = "default") -> None:
        self._device = device

    def say(self, text: str) -> None:
        try:
            subprocess.run(
                ["espeak", text], timeout=10,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    def beep(self) -> None:
        try:
            subprocess.run(
                ["aplay", "-D", self._device, "-q", "/usr/share/sounds/alsa/Front_Center.wav"],
                timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass


def make_picarx_bundle():
    """Create one shared Picarx() plus the adapters around it."""
    from picarx import Picarx

    px = Picarx()
    return PicarxChassis(px), PicarxUltrasonic(px), PicarxGrayscale(px)
