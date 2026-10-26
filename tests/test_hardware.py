"""Adapter tests against a fake Picarx object (no GPIO, runs anywhere)."""

from __future__ import annotations

import pytest

from follow_the_duck.camera.mock import ReplayCamera, SyntheticCamera
from follow_the_duck.camera.base import CameraError
from follow_the_duck.hardware.picarx_hw import (
    PicarxChassis,
    PicarxGrayscale,
    PicarxUltrasonic,
)


class FakeUltrasonic:
    def __init__(self):
        self.value = 42.0

    def read(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class FakePicarx:
    def __init__(self):
        self.calls = []
        self.ultrasonic = FakeUltrasonic()

    def set_dir_servo_angle(self, a):
        self.calls.append(("dir", a))

    def forward(self, s):
        self.calls.append(("forward", s))

    def backward(self, s):
        self.calls.append(("backward", s))

    def stop(self):
        self.calls.append(("stop",))

    def set_cam_pan_angle(self, a):
        self.calls.append(("pan", a))

    def set_cam_tilt_angle(self, a):
        self.calls.append(("tilt", a))

    def get_grayscale_data(self):
        return [123, 456, 789]


def test_chassis_clamps_angles_and_speed():
    px = FakePicarx()
    chassis = PicarxChassis(px)
    chassis.set_steering(60.0)
    chassis.set_steering(-60.0)
    chassis.set_cam_pan(120.0)
    chassis.set_cam_tilt(-80.0)
    chassis.drive(150)
    chassis.drive(30, reverse=True)
    chassis.stop()
    assert ("dir", 30.0) in px.calls and ("dir", -30.0) in px.calls
    assert ("pan", 80.0) in px.calls
    assert ("tilt", -30.0) in px.calls
    assert ("forward", 100) in px.calls
    assert ("backward", 30) in px.calls
    assert ("stop",) in px.calls


def test_chassis_close_stops_and_centers():
    px = FakePicarx()
    PicarxChassis(px).close()
    assert ("stop",) in px.calls
    assert ("dir", 0.0) in px.calls


def test_ultrasonic_adapter_survives_exceptions():
    px = FakePicarx()
    sensor = PicarxUltrasonic(px)
    assert sensor.read_cm() == 42.0
    px.ultrasonic.value = RuntimeError("bus error")
    assert sensor.read_cm() is None
    assert sensor.moves_with_camera is False


def test_grayscale_adapter():
    assert PicarxGrayscale(FakePicarx()).read() == [123, 456, 789]


def test_synthetic_camera_returns_valid_jpeg():
    jpeg = SyntheticCamera().capture_jpeg()
    assert jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")


def test_replay_camera(tmp_path):
    with pytest.raises(CameraError):
        ReplayCamera(tmp_path)  # empty dir
    (tmp_path / "a.jpg").write_bytes(b"frame-a")
    (tmp_path / "b.jpg").write_bytes(b"frame-b")
    camera = ReplayCamera(tmp_path)
    assert camera.capture_jpeg() == b"frame-a"
    assert camera.capture_jpeg() == b"frame-b"
    assert camera.capture_jpeg() == b"frame-b"  # loops on last
