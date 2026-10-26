from __future__ import annotations

import json
from pathlib import Path

import pytest

from follow_the_duck.cli import main
from follow_the_duck.config import Config
from follow_the_duck.runtime import build_runtime, load_vision_script

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FTD_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("FTD_SERVO_SETTLE_S", "0.01")
    monkeypatch.setenv("FTD_WATCHDOG_INTERVAL_S", "0.005")
    monkeypatch.setenv("FTD_HARDWARE_BACKEND", "mock")
    monkeypatch.setenv("FTD_CAMERA_BACKEND", "mock")
    return tmp_path


def test_replay_mission_arrives(env, capsys):
    rc = main(["replay", str(FIXTURES / "replay_center.json"), "yellow duck"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ARRIVED" in out
    assert "Found it" in out


def test_replay_mission_not_found_or_budget(env, capsys, monkeypatch):
    monkeypatch.setenv("FTD_MAX_REPOSITION_STEPS", "3")
    rc = main(["replay", str(FIXTURES / "replay_not_found.json"), "yellow duck"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "NOT_FOUND" in out


def test_find_dry_run_with_script(env, capsys):
    rc = main([
        "find", "red cup", "--dry-run",
        "--script", str(FIXTURES / "replay_center.json"),
    ])
    assert rc == 0
    assert "ARRIVED" in capsys.readouterr().out


def test_status_after_replay(env, capsys):
    main(["replay", str(FIXTURES / "replay_center.json"), "yellow duck"])
    capsys.readouterr()
    rc = main(["status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ARRIVED" in out
    assert "yellow duck" in out


def test_status_without_runs(env, capsys):
    assert main(["status"]) == 1


def test_doctor_runs_offline(env, capsys):
    rc = main(["doctor"])
    out = capsys.readouterr().out
    assert "python" in out
    assert "cloudflare-credentials" in out
    assert "safety-note" in out
    assert rc in (0, 1)


def test_config_error_rejected(env, capsys, monkeypatch):
    monkeypatch.setenv("FTD_MAX_STEER_DEG", "85")
    rc = main(["find", "duck", "--dry-run",
               "--script", str(FIXTURES / "replay_center.json")])
    assert rc == 2
    assert "config error" in capsys.readouterr().err


def test_load_vision_script_validates(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"not": "a list"}))
    with pytest.raises(ValueError):
        load_vision_script(bad)
    errors = tmp_path / "err.json"
    errors.write_text(json.dumps([{"error": "simulated timeout"}]))
    script = load_vision_script(errors)
    assert isinstance(script[0], Exception)


def test_build_runtime_dry_run_needs_no_credentials(tmp_path):
    cfg = Config()
    cfg.runtime_dir = str(tmp_path / "rt")
    runtime = build_runtime(
        cfg, dry_run=True, vision_script=FIXTURES / "replay_not_found.json"
    )
    try:
        assert runtime.speech is None
        assert runtime.supervisor.latched_reason() == "startup"
    finally:
        runtime.shutdown()


def test_mission_exit_latches_motors(env):
    """After any mission the supervisor is latched: nothing restarts on its
    own (covers 'restart remains idle' at the process level)."""
    cfg = Config()
    cfg.runtime_dir = str(env / "rt2")
    runtime = build_runtime(
        cfg, dry_run=True, vision_script=FIXTURES / "replay_not_found.json"
    )
    try:
        runtime.mission.run("duck")
        assert runtime.supervisor.latched_reason() is not None
        assert not runtime.chassis.moving
    finally:
        runtime.shutdown()
