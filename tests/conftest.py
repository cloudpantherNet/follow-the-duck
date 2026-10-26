from __future__ import annotations

import pytest

from follow_the_duck.camera.mock import SyntheticCamera
from follow_the_duck.clock import FakeClock
from follow_the_duck.config import Config
from follow_the_duck.hardware.mock import (
    MockChassis,
    MockDistanceSensor,
    MockGrayscale,
    MockSpeaker,
)
from follow_the_duck.mission import MissionController
from follow_the_duck.perception import Perceiver
from follow_the_duck.providers.mock import ScriptedVision
from follow_the_duck.statuslog import StatusBoard
from follow_the_duck.supervisor import MotorSupervisor, SensorPoller


class AutoPoller(SensorPoller):
    """Polls on every read so cached readings stay fresh under FakeClock."""

    def latest(self):
        self.poll_once()
        return super().latest()


def fast_config(tmp_path) -> Config:
    cfg = Config()
    cfg.runtime_dir = str(tmp_path / "ftd-runtime")
    cfg.servo_settle_s = 0.01
    cfg.watchdog_interval_s = 0.05
    cfg.pulse_s = 0.25
    cfg.mission_time_budget_s = 300.0  # generous; tests use step budgets
    cfg.max_reposition_steps = 20
    cfg.max_vision_calls_per_mission = 60
    cfg.max_vision_calls_per_minute = 10_000  # not under test here
    return cfg


class Harness:
    def __init__(self, cfg, clock, chassis, sensor, vision, mission, status):
        self.cfg = cfg
        self.clock = clock
        self.chassis = chassis
        self.sensor = sensor
        self.vision = vision
        self.mission = mission
        self.status = status


def make_harness(tmp_path, script, distances=None, cfg=None) -> Harness:
    cfg = cfg or fast_config(tmp_path)
    clock = FakeClock()
    chassis = MockChassis()
    sensor = MockDistanceSensor(distances if distances is not None else [120.0])
    poller = AutoPoller(sensor, cfg, clock, MockGrayscale())
    poller.poll_once()
    supervisor = MotorSupervisor(chassis, poller, cfg, clock)
    vision = ScriptedVision(script, clock=clock)
    perceiver = Perceiver(vision, cfg, clock)
    status = StatusBoard(cfg.runtime_dir, MockSpeaker())
    camera = SyntheticCamera()
    mission = MissionController(cfg, clock, chassis, supervisor, camera, perceiver, status)
    return Harness(cfg, clock, chassis, sensor, vision, mission, status)


@pytest.fixture
def harness_factory(tmp_path):
    def factory(script, distances=None, cfg=None):
        return make_harness(tmp_path, script, distances=distances, cfg=cfg)

    return factory


@pytest.fixture
def cfg(tmp_path):
    return fast_config(tmp_path)
