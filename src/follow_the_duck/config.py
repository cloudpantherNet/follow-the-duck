"""Configuration.

All settings come from environment variables (``FTD_*`` and ``CLOUDFLARE_*``),
optionally loaded from a ``.env`` file. Nothing is hardcoded to a DHCP
address, ALSA card number, or model name. See .env.example for the full list.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Dict, Optional


def load_dotenv(path: Path) -> Dict[str, str]:
    """Minimal .env parser (KEY=VALUE, '#' comments). Does not override
    variables already present in the real environment."""
    values: Dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Drop an unquoted inline comment ("TOKEN=abc  # note") — but only
        # when preceded by whitespace, so values containing '#' still work.
        if not (value.startswith("'") or value.startswith('"')):
            for i, ch in enumerate(value):
                if ch == "#" and i > 0 and value[i - 1] in " \t":
                    value = value[:i]
                    break
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            values[key] = value
    return values


def _env(env: Dict[str, str], key: str, default: str) -> str:
    return env.get(key, os.environ.get(key, default))


@dataclass
class Config:
    # --- Cloudflare Workers AI -------------------------------------------
    cloudflare_account_id: str = ""
    cloudflare_api_token: str = ""  # secret: never logged, never committed
    vision_model: str = "@cf/cloudflare/clef-flash"
    stt_model: str = "@cf/openai/whisper-large-v3-turbo"
    api_base: str = "https://api.cloudflare.com/client/v4"
    http_timeout_s: float = 20.0
    stt_language: str = "en"

    # --- Request budgets (bounded cloud usage, not continuous video) ------
    max_vision_calls_per_mission: int = 40
    max_vision_calls_per_minute: int = 30

    # --- Camera -----------------------------------------------------------
    camera_backend: str = "auto"  # auto | picamera2 | rpicam | mock
    camera_width: int = 640
    camera_height: int = 480
    camera_jpeg_quality: int = 85
    camera_hflip: bool = False
    camera_vflip: bool = False
    camera_fov_deg: float = 62.0  # Pi Camera v2 horizontal FoV; calibrate

    # --- Audio (push-to-talk) ----------------------------------------------
    mic_device: str = "default"  # ALSA device string, e.g. plughw:1,0
    speaker_device: str = "default"
    record_seconds: float = 4.0
    record_rate: int = 16000

    # --- Hands-free mode (listening loop at boot, no terminal) --------------
    hands_free_chunk_s: float = 4.0
    # Local RMS gate: chunks quieter than this are discarded without any
    # cloud call. Calibrate per room/microphone (ftd doctor --record prints
    # the measured level of a test capture).
    voice_rms_threshold: float = 300.0

    # --- On-device wake word (optional; openWakeWord, runs locally) ----------
    # Requires the 'wakeword' extra: pip install -e '.[wakeword]'
    # Only pretrained openWakeWord phrases are available without training a
    # custom model (e.g. hey_jarvis_v0.1, alexa_v0.1, hey_mycroft_v0.1).
    wake_word_enabled: bool = False
    wake_word_model: str = "hey_jarvis_v0.1"  # model name or .onnx file path
    wake_word_threshold: float = 0.5          # detection score 0..1
    wake_word_cooldown_s: float = 2.0         # ignore re-triggers this long

    # --- Camera gimbal scan -------------------------------------------------
    scan_pan_angles_deg: str = "-35,0,35"  # conservative; servo limit is wider
    tilt_deg: float = -10.0  # look slightly down toward the floor
    servo_settle_s: float = 0.35

    # --- Motion (conservative starting values; tune after physical tests) ---
    drive_speed: int = 25           # SunFounder 0..100 power scale
    approach_speed_near: int = 15   # slower when close to target
    max_speed: int = 35             # hard clamp in the supervisor
    pulse_s: float = 0.25           # max single movement duration
    max_steer_deg: float = 20.0
    allow_reverse: bool = False     # no rear sensor coverage -> no reversing

    # --- Safety thresholds ---------------------------------------------------
    obstacle_stop_cm: float = 20.0
    arrival_cm: float = 30.0
    range_min_valid_cm: float = 2.0
    range_max_valid_cm: float = 400.0
    range_stale_s: float = 1.0
    heartbeat_timeout_s: float = 2.0
    command_ttl_s: float = 1.5
    observation_stale_s: float = 10.0
    watchdog_interval_s: float = 0.05
    grayscale_cliff_enabled: bool = False  # enable only after calibration

    # --- Perception thresholds ----------------------------------------------
    present_threshold: float = 0.65
    absent_threshold: float = 0.35
    floor_clear_threshold: float = 0.60
    confirmations_to_acquire: int = 2
    confirmations_to_arrive: int = 2
    ambiguity_threshold: float = 0.70

    # --- Search budgets -------------------------------------------------------
    mission_time_budget_s: float = 90.0
    max_reposition_steps: int = 20
    rescan_attempts_on_loss: int = 2
    history_size: int = 8

    # --- Planner weights (deterministic ranking) ------------------------------
    w_floor: float = 1.0
    w_uncertainty: float = 0.5
    w_novelty: float = 0.6
    w_steer_cost: float = 0.15

    # --- Mission telemetry dashboard (optional; see deploy/worker/) ----------
    telemetry_url: str = ""    # e.g. https://follow-the-duck.<you>.workers.dev
    telemetry_token: str = ""  # shared secret; same as the Worker's FTD_TOKEN
    telemetry_send_images: bool = True

    # --- Hardware backends -----------------------------------------------------
    hardware_backend: str = "auto"  # auto | picarx | mock
    audio_backend: str = "auto"     # auto | alsa | mock

    # --- Runtime files -----------------------------------------------------------
    runtime_dir: str = field(default_factory=lambda: str(Path.home() / ".ftd"))

    def scan_angles(self) -> list:
        out = []
        for part in self.scan_pan_angles_deg.split(","):
            part = part.strip()
            if part:
                out.append(float(part))
        return out or [0.0]

    def validate(self) -> list:
        """Return a list of human-readable problems (empty = OK)."""
        problems = []
        if self.max_speed < 1 or self.max_speed > 100:
            problems.append("max_speed must be within 1..100")
        if not (0 < self.pulse_s <= 1.0):
            problems.append("pulse_s must be within (0, 1.0] seconds")
        if not (0 < self.max_steer_deg <= 40):
            problems.append("max_steer_deg must be within (0, 40] degrees")
        if self.obstacle_stop_cm <= self.range_min_valid_cm:
            problems.append("obstacle_stop_cm must exceed range_min_valid_cm")
        if self.arrival_cm < self.obstacle_stop_cm:
            problems.append("arrival_cm should be >= obstacle_stop_cm")
        if not (0 <= self.absent_threshold < self.present_threshold <= 1):
            problems.append("need 0 <= absent_threshold < present_threshold <= 1")
        for angle in self.scan_angles():
            if abs(angle) > 80:
                problems.append("scan pan angle beyond +/-80 deg: %s" % angle)
        return problems


_BOOL_TRUE = {"1", "true", "yes", "on"}


def config_from_env(dotenv_path: Optional[Path] = None) -> Config:
    """Build a Config from environment variables (FTD_<FIELD> upper-case),
    plus CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN conveniences."""
    env = load_dotenv(dotenv_path or Path(".env"))
    cfg = Config()
    for f in fields(Config):
        key = "FTD_" + f.name.upper()
        raw = _env(env, key, "")
        if raw == "":
            continue
        current = getattr(cfg, f.name)
        if isinstance(current, bool):
            setattr(cfg, f.name, raw.lower() in _BOOL_TRUE)
        elif isinstance(current, int):
            setattr(cfg, f.name, int(raw))
        elif isinstance(current, float):
            setattr(cfg, f.name, float(raw))
        else:
            setattr(cfg, f.name, raw)
    cfg.cloudflare_account_id = _env(
        env, "CLOUDFLARE_ACCOUNT_ID", cfg.cloudflare_account_id
    )
    cfg.cloudflare_api_token = _env(
        env, "CLOUDFLARE_API_TOKEN", cfg.cloudflare_api_token
    )
    return cfg
