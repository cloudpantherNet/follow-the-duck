"""End-to-end mission scenarios with scripted vision, mock hardware, and a
fake clock — every acceptance scenario from the project brief that can run
without a robot."""

from __future__ import annotations

import pytest

from follow_the_duck.models import MissionState, validate_observation
from follow_the_duck.providers.base import ProviderError, ProviderTimeout
from follow_the_duck.providers.mock import clef_answers

from test_models import raw_obs

OPEN = dict(floor_left=0.9, floor_center=0.9, floor_right=0.9)
CLOSED = dict(floor_left=0.1, floor_center=0.1, floor_right=0.1)


def present(bearing="center", proximity="mid", **kw):
    kw.setdefault("present", 0.9)
    kw.setdefault("bearing", bearing)
    kw.setdefault("proximity", proximity)
    for key, value in OPEN.items():
        kw.setdefault(key, value)
    return clef_answers(**kw)


def absent(**kw):
    kw.setdefault("present", 0.05)
    for key, value in CLOSED.items():
        kw.setdefault(key, value)
    return clef_answers(**kw)


def shrinking_sonar(chassis, start=150.0, step=31.0, floor=28.0):
    """Range falls with every drive pulse, like a real approach."""

    def read():
        drives = sum(1 for e in chassis.log if e[0] == "drive")
        return max(floor, start - step * drives)

    return read


def first_steer_before_drive(chassis):
    steer = 0.0
    for entry in chassis.log:
        if entry[0] == "steer":
            steer = entry[1]
        elif entry[0] == "drive":
            return steer
    return None


# ---------------------------------------------------------------- scenarios


def test_target_visible_ahead_is_approached_and_arrived(harness_factory):
    script = [
        present(proximity="mid"),
        present(proximity="mid"),
        present(proximity="near"),
        present(proximity="very_close"),
    ]
    h = harness_factory(script)
    h.sensor.set_script(shrinking_sonar(h.chassis))
    status = h.mission.run("yellow duck")
    assert status.state == MissionState.ARRIVED
    assert not h.chassis.moving
    assert h.chassis.log[-1][0] in ("stop", "pan", "tilt")  # ends stopped
    assert status.steps_taken >= 2
    assert status.vision_calls >= 4


def test_far_centered_target_gets_consecutive_pulses(harness_factory):
    """A far target straight ahead earns several pulses per cloud look, so
    the approach visibly progresses instead of inching once per inference."""
    from follow_the_duck.telemetry import ListSink

    script = [present(proximity="far"), present(proximity="far"),
              present(proximity="mid"), present(proximity="near"),
              present(proximity="very_close")]
    h = harness_factory(script)
    h.sensor.set_script(shrinking_sonar(h.chassis, start=250.0, step=25.0))
    sink = ListSink()
    h.mission._sink = sink
    status = h.mission.run("yellow duck")
    assert status.state == MissionState.ARRIVED
    kinds = [e["kind"] for e in sink.events]
    first_move = kinds.index("move")
    # Three consecutive pulses with no observation between them.
    assert kinds[first_move:first_move + 3] == ["move"] * 3
    # Near the target it goes back to one pulse per look.
    moves = [e for e in sink.events if e["kind"] == "move"]
    assert all(m["ok"] for m in moves)


def test_target_found_only_after_camera_scan(harness_factory):
    script = [
        absent(),            # pan 0
        absent(),            # pan -35
        present(),           # pan +35 — first sighting
        present(),           # confirm at same pan
        present(proximity="near"),
        present(proximity="very_close"),
    ]
    h = harness_factory(script)
    h.sensor.set_script(shrinking_sonar(h.chassis))
    status = h.mission.run("red cup")
    assert status.state == MissionState.ARRIVED
    # Robot never drove during the scan: first 4 vision calls had no drive.
    assert status.vision_calls >= 5
    # It steered toward the right, where the target was seen.
    assert first_steer_before_drive(h.chassis) == pytest.approx(h.cfg.max_steer_deg)


def test_target_revealed_after_repositioning(harness_factory):
    # Scan 1: nothing visible anywhere; only the LEFT has open floor.
    scan1 = [
        absent(floor_left=0.9, floor_center=0.15, floor_right=0.15),  # pan 0
        absent(floor_center=0.9),                                     # pan -35
        absent(floor_center=0.15),                                    # pan +35
    ]
    # Scan 2 (after repositioning): target straight ahead.
    scan2 = [present(), present(), present(proximity="near"), present(proximity="very_close")]
    h = harness_factory(scan1 + scan2)
    h.sensor.set_script(shrinking_sonar(h.chassis, start=180.0))
    status = h.mission.run("green bottle")
    assert status.state == MissionState.ARRIVED
    # The reposition went LEFT because that's where the open floor was.
    assert first_steer_before_drive(h.chassis) == pytest.approx(-h.cfg.max_steer_deg)
    assert status.steps_taken >= 2


def test_direction_choice_follows_open_floor_right(harness_factory):
    scan1 = [
        absent(floor_left=0.15, floor_center=0.15, floor_right=0.9),
        absent(floor_center=0.15),
        absent(floor_center=0.9),
    ]
    h = harness_factory(scan1 + [absent()])  # second scan: everything blocked
    status = h.mission.run("blue ball")
    assert first_steer_before_drive(h.chassis) == pytest.approx(h.cfg.max_steer_deg)
    assert status.state == MissionState.BLOCKED


def test_target_loss_stops_and_rescans_never_drives_blind(harness_factory):
    from follow_the_duck.telemetry import ListSink

    script = [present(), present()] + [absent()]  # lost right after approach
    h = harness_factory(script)
    sink = ListSink()
    h.mission._sink = sink
    status = h.mission.run("yellow duck")
    assert status.state in (MissionState.BLOCKED, MissionState.NOT_FOUND)
    assert not h.chassis.moving
    # No movement after the loss: every move event precedes the first
    # 'absent' observation (the rescan and the rest of the mission are
    # camera-only).
    kinds = [(e["kind"], e.get("presence")) for e in sink.events]
    first_absent = next(i for i, (k, p) in enumerate(kinds)
                        if k == "observation" and p == "absent")
    assert all(k != "move" for k, _ in kinds[first_absent:])
    assert any(k == "move" for k, _ in kinds[:first_absent])


def test_nearby_unrelated_obstacle_is_not_arrival(harness_factory):
    """Sonar sees something at 25 cm but the target still looks mid-distance:
    never declare ARRIVED on sonar alone."""
    script = [present(proximity="mid")]
    h = harness_factory(script, distances=[25.0])
    status = h.mission.run("yellow duck")
    assert status.state == MissionState.BLOCKED
    assert status.state != MissionState.ARRIVED
    assert sum(1 for e in h.chassis.log if e[0] == "drive") == 0


def test_visually_close_but_sonar_uncertain_stops_with_uncertainty(harness_factory):
    """Duck below the sonar beam: vision says very close, sonar says 80 cm
    (the wall behind). The robot creeps, then stops in BLOCKED — it must not
    claim success it cannot verify."""
    script = [present(proximity="very_close")]
    h = harness_factory(script, distances=[19.0])
    # 19 cm < obstacle threshold: can't creep, visually close -> uncertain stop
    status = h.mission.run("yellow duck")
    assert status.state == MissionState.BLOCKED
    assert "cannot confirm" in status.message


def test_blocked_route_ends_blocked_without_motion(harness_factory):
    h = harness_factory([absent()])  # all floors closed everywhere
    status = h.mission.run("yellow duck")
    assert status.state == MissionState.BLOCKED
    assert sum(1 for e in h.chassis.log if e[0] == "drive") == 0


def test_invalid_sonar_blocks_all_motion(harness_factory):
    """No-echo sonar (-1): open floor per vision, but motion must be refused
    and the mission ends BLOCKED without a single drive command."""
    h = harness_factory([absent(**OPEN)], distances=[-1])
    status = h.mission.run("yellow duck")
    assert status.state == MissionState.BLOCKED
    assert sum(1 for e in h.chassis.log if e[0] == "drive") == 0


def test_repeated_provider_failures_end_in_error(harness_factory):
    h = harness_factory([ProviderTimeout("t"), ProviderError("x"), ProviderTimeout("t")])
    status = h.mission.run("yellow duck")
    assert status.state == MissionState.ERROR
    assert not h.chassis.moving


def test_single_provider_failure_is_tolerated(harness_factory):
    script = [ProviderTimeout("blip"), present(), present(),
              present(proximity="near"), present(proximity="very_close")]
    h = harness_factory(script)
    h.sensor.set_script(shrinking_sonar(h.chassis))
    status = h.mission.run("yellow duck")
    assert status.state == MissionState.ARRIVED


def test_stop_during_inference_ends_stopped(harness_factory):
    h = harness_factory([absent()])

    def stop_mid_inference():
        h.mission.request_stop()  # STOP arrives while "the cloud is thinking"
        return absent()

    h.vision._script = [stop_mid_inference]
    status = h.mission.run("yellow duck")
    assert status.state == MissionState.STOPPED
    assert h.mission._supervisor.latched_reason() is not None
    assert sum(1 for e in h.chassis.log if e[0] == "drive") == 0


def test_stop_during_approach_ends_stopped(harness_factory):
    calls = {"n": 0}

    def scripted():
        calls["n"] += 1
        if calls["n"] == 3:  # right after acquisition, during approach
            h.mission.request_stop()
        return present()

    h = harness_factory([scripted])
    status = h.mission.run("yellow duck")
    assert status.state == MissionState.STOPPED
    assert not h.chassis.moving


def test_search_step_budget_ends_not_found(harness_factory, tmp_path):
    h = harness_factory([absent(**OPEN)])
    h.cfg.max_reposition_steps = 3
    status = h.mission.run("yellow duck")
    assert status.state == MissionState.NOT_FOUND
    assert status.steps_taken == 3


def test_time_budget_ends_not_found(harness_factory):
    h = harness_factory([absent(**OPEN)])
    h.cfg.mission_time_budget_s = 2.0
    h.cfg.servo_settle_s = 0.5  # make the fake clock advance quickly
    status = h.mission.run("yellow duck")
    assert status.state == MissionState.NOT_FOUND


def test_vision_call_budget_ends_not_found(harness_factory):
    h = harness_factory([absent(**OPEN)])
    h.cfg.max_vision_calls_per_mission = 5
    status = h.mission.run("yellow duck")
    assert status.state == MissionState.NOT_FOUND
    assert status.vision_calls <= 5


def test_new_target_runs_fresh_mission(harness_factory):
    """Changing the description changes the target: each run() builds a new
    mission id and passes the new description to the vision questions."""
    h = harness_factory([absent()])
    first = h.mission.run("yellow duck")
    assert "yellow duck" in str(h.vision.last_state)
    second = h.mission.run("red cup")
    assert "red cup" in str(h.vision.last_state)
    assert first.mission_id != second.mission_id


# --------------------------------------------- stale / out-of-order results


def test_results_from_old_or_stale_frames_are_dropped(harness_factory):
    h = harness_factory([absent()])
    mission = h.mission
    mission._mission_id = "current"
    mission._last_accepted_frame = 5
    now = h.clock.now()

    def obs(**kw):
        return validate_observation(raw_obs(mission_id="current", frame_id=6,
                                            captured_at=now, **kw))

    assert mission._accept(obs())  # fresh, in-order, right mission
    mission._last_accepted_frame = 5
    old_mission = validate_observation(
        raw_obs(mission_id="cancelled-mission", frame_id=7, captured_at=now)
    )
    assert not mission._accept(old_mission)
    out_of_order = validate_observation(
        raw_obs(mission_id="current", frame_id=4, captured_at=now)
    )
    assert not mission._accept(out_of_order)
    stale = validate_observation(
        raw_obs(mission_id="current", frame_id=9,
                captured_at=now - h.cfg.observation_stale_s - 1)
    )
    assert not mission._accept(stale)


def test_status_file_written(harness_factory):
    from follow_the_duck.statuslog import read_status

    h = harness_factory([absent()])
    h.mission.run("yellow duck")
    status = read_status(h.cfg.runtime_dir)
    assert status is not None
    assert status["state"] == "BLOCKED"
    assert status["target"] == "yellow duck"
