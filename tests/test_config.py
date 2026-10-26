from __future__ import annotations

from follow_the_duck.config import Config, config_from_env, load_dotenv


def test_defaults_validate_clean():
    assert Config().validate() == []


def test_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("FTD_DRIVE_SPEED", "30")
    monkeypatch.setenv("FTD_MAX_STEER_DEG", "15.5")
    monkeypatch.setenv("FTD_ALLOW_REVERSE", "true")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct123")
    cfg = config_from_env(tmp_path / "nonexistent.env")
    assert cfg.drive_speed == 30
    assert cfg.max_steer_deg == 15.5
    assert cfg.allow_reverse is True
    assert cfg.cloudflare_account_id == "acct123"


def test_dotenv_parse_and_precedence(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "FTD_DRIVE_SPEED=22\n"
        'FTD_MIC_DEVICE="plughw:1,0"\n'
        "CLOUDFLARE_API_TOKEN=secret-from-file\n"
    )
    monkeypatch.setenv("FTD_DRIVE_SPEED", "28")  # real env wins
    values = load_dotenv(env_file)
    assert "FTD_DRIVE_SPEED" not in values
    cfg = config_from_env(env_file)
    assert cfg.drive_speed == 28
    assert cfg.mic_device == "plughw:1,0"
    assert cfg.cloudflare_api_token == "secret-from-file"


def test_validate_flags_bad_values():
    cfg = Config()
    cfg.max_steer_deg = 90.0
    cfg.pulse_s = 5.0
    cfg.present_threshold = 0.2
    cfg.absent_threshold = 0.4
    problems = cfg.validate()
    assert len(problems) >= 3


def test_scan_angles_parse():
    cfg = Config()
    cfg.scan_pan_angles_deg = " -30, 0 ,30 "
    assert cfg.scan_angles() == [-30.0, 0.0, 30.0]
