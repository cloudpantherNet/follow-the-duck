from __future__ import annotations

import threading
import time

import pytest

from follow_the_duck.clock import Clock, FakeClock
from follow_the_duck.hardware.mock import MockChassis, MockDistanceSensor
from follow_the_duck.models import MoveCommand
from follow_the_duck.supervisor import MotorSupervisor, SensorPoller

from conftest import AutoPoller, fast_config


def make_supervisor(tmp_path, distances=None, cfg=None, clock=None):
    cfg = cfg or fast_config(tmp_path)
    clock = clock or FakeClock()
    chassis = MockChassis()
    sensor = MockDistanceSensor(distances if distances is not None else [120.0])
    poller = AutoPoller(sensor, cfg, clock)
    poller.poll_once()
    supervisor = MotorSupervisor(chassis, poller, cfg, clock)
    return supervisor, chassis, sensor, clock, cfg


def cmd(clock, seq=1, mission="m1", speed=25, steer=0.0, duration=0.25, ttl=1.5,
        reverse=False):
    return MoveCommand(mission, seq, speed, steer, duration, clock.now(), ttl, reverse)


def test_boot_state_is_latched_until_arm(tmp_path):
    supervisor, chassis, _, clock, _ = make_supervisor(tmp_path)
    accepted, reason = supervisor.submit(cmd(clock))
    assert not accepted and "latched" in reason
    supervisor.arm("m1")
    accepted, _ = supervisor.submit(cmd(clock))
    assert accepted
    assert chassis.moving


def test_move_completes_after_duration(tmp_path):
    supervisor, chassis, _, clock, cfg = make_supervisor(tmp_path)
    supervisor.arm("m1")
    ok, reason = supervisor.execute(cmd(clock, duration=0.25))
    assert ok, reason
    assert not chassis.moving
    assert chassis.log[-1] == ("stop",)
    assert supervisor.moves_completed == 1


def test_speed_steer_duration_are_clamped(tmp_path):
    supervisor, chassis, _, clock, cfg = make_supervisor(tmp_path)
    supervisor.arm("m1")
    accepted, _ = supervisor.submit(cmd(clock, speed=99, steer=70.0, duration=9.0))
    assert accepted
    assert chassis.speed == cfg.max_speed
    assert chassis.steering_deg == cfg.max_steer_deg
    # duration clamp: move must end within pulse_s
    clock.advance(cfg.pulse_s + 0.01)
    supervisor.tick()
    assert not chassis.moving


def test_expired_command_rejected(tmp_path):
    supervisor, _, _, clock, _ = make_supervisor(tmp_path)
    supervisor.arm("m1")
    stale = cmd(clock, ttl=0.5)
    clock.advance(1.0)
    accepted, reason = supervisor.submit(stale)
    assert not accepted and "expired" in reason


def test_out_of_order_and_wrong_mission_rejected(tmp_path):
    supervisor, _, _, clock, _ = make_supervisor(tmp_path)
    supervisor.arm("m1")
    ok, _ = supervisor.execute(cmd(clock, seq=5))
    assert ok
    accepted, reason = supervisor.submit(cmd(clock, seq=5))
    assert not accepted and "stale sequence" in reason
    accepted, reason = supervisor.submit(cmd(clock, seq=6, mission="old-mission"))
    assert not accepted and "wrong mission" in reason


def test_reverse_rejected_without_rear_coverage(tmp_path):
    supervisor, _, _, clock, _ = make_supervisor(tmp_path)
    supervisor.arm("m1")
    accepted, reason = supervisor.submit(cmd(clock, reverse=True))
    assert not accepted and "reverse" in reason


@pytest.mark.parametrize("readings", [[None], [0], [-1], [float("nan")], [900.0]])
def test_invalid_range_blocks_motion(tmp_path, readings):
    supervisor, chassis, _, clock, _ = make_supervisor(tmp_path, distances=readings)
    supervisor.arm("m1")
    accepted, reason = supervisor.submit(cmd(clock))
    assert not accepted
    assert "invalid" in reason or "no range" in reason
    assert not chassis.moving


def test_obstacle_blocks_motion(tmp_path):
    supervisor, chassis, _, clock, _ = make_supervisor(tmp_path, distances=[15.0])
    supervisor.arm("m1")
    accepted, reason = supervisor.submit(cmd(clock))
    assert not accepted and "obstacle" in reason
    assert not chassis.moving


def test_obstacle_appearing_mid_move_stops_and_latches(tmp_path):
    supervisor, chassis, sensor, clock, cfg = make_supervisor(tmp_path, distances=[120.0])
    supervisor.arm("m1")
    accepted, _ = supervisor.submit(cmd(clock, duration=0.25))
    assert accepted and chassis.moving
    sensor.set_script([10.0])  # obstacle appears mid-move
    clock.advance(0.05)
    supervisor.tick()
    assert not chassis.moving
    assert "obstacle" in supervisor.latched_reason()
    accepted, reason = supervisor.submit(cmd(clock, seq=2))
    assert not accepted and "latched" in reason


def test_stale_range_blocks_motion(tmp_path):
    cfg = fast_config(tmp_path)
    clock = FakeClock()
    chassis = MockChassis()
    sensor = MockDistanceSensor([120.0])
    poller = SensorPoller(sensor, cfg, clock)  # NOT auto-refreshing
    poller.poll_once()
    supervisor = MotorSupervisor(chassis, poller, cfg, clock)
    supervisor.arm("m1")
    clock.advance(cfg.range_stale_s + 0.5)
    supervisor.heartbeat()
    accepted, reason = supervisor.submit(cmd(clock))
    assert not accepted and "stale" in reason


def test_heartbeat_expiry_stops_active_move(tmp_path):
    supervisor, chassis, _, clock, cfg = make_supervisor(tmp_path)
    supervisor.arm("m1")
    accepted, _ = supervisor.submit(cmd(clock, duration=0.25))
    assert accepted and chassis.moving
    clock.advance(cfg.heartbeat_timeout_s + 0.1)  # mission loop went silent
    supervisor.tick()
    assert not chassis.moving
    assert "heartbeat" in supervisor.latched_reason()


def test_stop_latches_until_rearm(tmp_path):
    supervisor, chassis, _, clock, _ = make_supervisor(tmp_path)
    supervisor.arm("m1")
    supervisor.submit(cmd(clock))
    supervisor.stop("STOP requested")
    assert not chassis.moving
    accepted, reason = supervisor.submit(cmd(clock, seq=2))
    assert not accepted and "latched" in reason
    supervisor.arm("m2")
    accepted, _ = supervisor.submit(cmd(clock, mission="m2"))
    assert accepted


def test_restart_is_idle_no_automatic_motion(tmp_path):
    """A freshly constructed supervisor (service restart) must not move."""
    supervisor, chassis, _, clock, _ = make_supervisor(tmp_path)
    assert supervisor.latched_reason() == "startup"
    assert chassis.stop_count() >= 1  # explicit stop at boot
    accepted, _ = supervisor.submit(cmd(clock))
    assert not accepted


def test_watchdog_thread_stops_motors_while_inference_blocked(tmp_path):
    """Real-clock integration test: the mission thread is 'stuck in a network
    call' (never heartbeats, never ticks); the watchdog thread must stop the
    motors on its own."""
    cfg = fast_config(tmp_path)
    cfg.heartbeat_timeout_s = 0.15
    cfg.watchdog_interval_s = 0.02
    cfg.pulse_s = 1.0
    clock = Clock()
    chassis = MockChassis()
    poller = AutoPoller(MockDistanceSensor([120.0]), cfg, clock)
    poller.poll_once()
    supervisor = MotorSupervisor(chassis, poller, cfg, clock)
    supervisor.arm("m1")
    supervisor.start_watchdog()
    try:
        accepted, reason = supervisor.submit(
            MoveCommand("m1", 1, 25, 0.0, 1.0, clock.now(), 1.5)
        )
        assert accepted, reason
        assert chassis.moving
        deadline = time.time() + 2.0  # simulate blocked inference: no heartbeats
        while chassis.moving and time.time() < deadline:
            time.sleep(0.02)
        assert not chassis.moving
        assert "heartbeat" in supervisor.latched_reason()
    finally:
        supervisor.shutdown()


def test_stop_is_threadsafe_during_move(tmp_path):
    supervisor, chassis, _, clock, _ = make_supervisor(tmp_path)
    supervisor.arm("m1")
    supervisor.submit(cmd(clock, duration=0.25))
    thread = threading.Thread(target=supervisor.stop, args=("STOP requested",))
    thread.start()
    thread.join()
    assert not chassis.moving
    assert supervisor.latched_reason() == "STOP requested"
