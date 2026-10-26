from __future__ import annotations

import math

import pytest

from follow_the_duck.hardware.base import RangeReading
from follow_the_duck.models import (
    Bearing,
    MoveCommand,
    ObservationError,
    Presence,
    Proximity,
    validate_observation,
)


def raw_obs(**overrides):
    base = {
        "mission_id": "m1",
        "frame_id": 1,
        "captured_at": 100.0,
        "pan_deg": 0.0,
        "tilt_deg": -10.0,
        "presence": "present",
        "confidence": 0.9,
        "bearing": "center",
        "bearing_confidence": 0.8,
        "proximity": "mid",
        "floor_clear": {"left": 0.5, "center": 0.9, "right": 0.2},
        "ambiguity": 0.1,
    }
    base.update(overrides)
    return base


def test_validate_happy_path():
    obs = validate_observation(raw_obs())
    assert obs.presence is Presence.PRESENT
    assert obs.bearing is Bearing.CENTER
    assert obs.proximity is Proximity.MID
    assert obs.floor_clear["center"] == 0.9


@pytest.mark.parametrize(
    "overrides",
    [
        {"presence": "maybe"},
        {"bearing": "behind"},
        {"proximity": "touching"},
        {"confidence": 1.5},
        {"confidence": -0.1},
        {"confidence": float("nan")},
        {"confidence": None},
        {"ambiguity": float("inf")},
        {"pan_deg": 200.0},
        {"frame_id": -1},
        {"floor_clear": {"left": 2.0}},
        {"floor_clear": "open"},
        {"captured_at": float("nan")},
    ],
)
def test_validate_rejects_bad_values(overrides):
    with pytest.raises(ObservationError):
        validate_observation(raw_obs(**overrides))


def test_validate_rejects_missing_header():
    bad = raw_obs()
    del bad["mission_id"]
    with pytest.raises(ObservationError):
        validate_observation(bad)


def test_chassis_bearing_combines_pan_and_image_third():
    obs = validate_observation(raw_obs(pan_deg=30.0, bearing="left"))
    bearing = obs.chassis_bearing_deg(fov_deg=60.0)
    assert bearing == pytest.approx(30.0 - 20.0)
    absent = validate_observation(
        raw_obs(presence="absent", bearing="unknown", proximity="unknown")
    )
    assert absent.chassis_bearing_deg(60.0) is None


@pytest.mark.parametrize("value", [0, -1, -2, None, float("nan"), float("inf"), 900, "far", True])
def test_range_classify_invalid(value):
    assert RangeReading.classify(value, 0.0, 2.0, 400.0).cm is None


def test_range_classify_valid():
    assert RangeReading.classify(123.4, 0.0, 2.0, 400.0).cm == pytest.approx(123.4)


def test_move_command_expiry():
    cmd = MoveCommand("m", 1, 25, 0.0, 0.25, issued_at=100.0, ttl_s=1.0)
    assert not cmd.expired(100.5)
    assert cmd.expired(101.6)
