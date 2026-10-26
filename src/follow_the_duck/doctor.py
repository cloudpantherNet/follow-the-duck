"""`ftd doctor`: health checks that never command wheel movement.

Checks Python, package, configuration, credentials presence, camera,
microphone, speaker, PiCar-X libraries, and (read-only) the ultrasonic and
grayscale sensors. `--online` additionally runs provider smoke tests:
a 1-frame clef-flash call and a 1-second whisper call, reporting latency.
"""

from __future__ import annotations

import importlib.util
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from .config import Config
from .hardware.base import RangeReading

OK, WARN, FAIL = "OK", "WARN", "FAIL"


@dataclass
class CheckResult:
    name: str
    level: str
    detail: str


def _run(cmd: List[str], timeout: float = 10) -> Optional[str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return proc.stdout.decode(errors="replace")
    except Exception:
        return None


def run_checks(cfg: Config, online: bool = False, record: bool = False) -> List[CheckResult]:
    results: List[CheckResult] = []
    add = results.append

    # --- Python / platform -------------------------------------------------
    py = "%d.%d.%d" % sys.version_info[:3]
    add(CheckResult("python", OK if sys.version_info >= (3, 9) else FAIL,
                    "Python %s on %s (%s)" % (py, platform.system(), platform.machine())))

    problems = cfg.validate()
    add(CheckResult("config", FAIL if problems else OK,
                    "; ".join(problems) or "configuration values in range"))

    # --- Credentials (presence only; never print the token) -----------------
    has_creds = bool(cfg.cloudflare_account_id and cfg.cloudflare_api_token)
    add(CheckResult(
        "cloudflare-credentials", OK if has_creds else WARN,
        "account+token configured (token hidden)" if has_creds
        else "CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN not set — cloud "
             "vision and speech unavailable; mock/replay still works",
    ))

    # --- SunFounder stack ----------------------------------------------------
    if importlib.util.find_spec("picarx") is not None:
        version = "unknown"
        try:
            import robot_hat  # type: ignore

            version = getattr(robot_hat, "__version__", "unknown")
        except Exception:
            pass
        add(CheckResult("picarx", OK, "picarx importable; robot_hat %s" % version))

        # Read-only sensor checks (no wheel or servo movement).
        try:
            from picarx import Picarx  # type: ignore

            px = Picarx()
            readings = []
            for _ in range(3):
                readings.append(px.ultrasonic.read())
                time.sleep(0.1)
            classified = [
                RangeReading.classify(r, 0.0, cfg.range_min_valid_cm,
                                      cfg.range_max_valid_cm).cm
                for r in readings
            ]
            valid = [c for c in classified if c is not None]
            level = OK if valid else WARN
            add(CheckResult(
                "ultrasonic", level,
                "readings=%s (valid: %d/3). REMINDER: physically verify the "
                "sensor is chassis-mounted and does not pan with the camera."
                % (readings, len(valid)),
            ))
            try:
                gray = px.get_grayscale_data()
                add(CheckResult("grayscale", OK,
                                "values=%s (cliff checks stay DISABLED until "
                                "calibrated: FTD_GRAYSCALE_CLIFF_ENABLED)" % (gray,)))
            except Exception as exc:
                add(CheckResult("grayscale", WARN, "read failed: %s" % exc))
            try:
                px.stop()  # ensure motors are off; never a movement command
            except Exception:
                pass
        except Exception as exc:
            add(CheckResult("ultrasonic", WARN, "Picarx init failed: %s" % exc))
    else:
        add(CheckResult("picarx", WARN,
                        "picarx not importable (expected off-robot); mock "
                        "hardware will be used"))

    # --- Camera ---------------------------------------------------------------
    if importlib.util.find_spec("picamera2") is not None:
        add(CheckResult("camera", OK, "picamera2 importable (preferred backend)"))
    elif shutil.which("rpicam-still"):
        add(CheckResult("camera", OK, "rpicam-still found (fallback backend; slow)"))
    else:
        add(CheckResult("camera", WARN,
                        "no picamera2 / rpicam-still — only mock camera available"))

    # --- Audio ------------------------------------------------------------------
    arecord_list = _run(["arecord", "-l"])
    if arecord_list is None:
        add(CheckResult("microphone", WARN, "arecord not found (ALSA utils)"))
    elif "card" not in arecord_list:
        add(CheckResult("microphone", WARN,
                        "no ALSA capture devices found (arecord -l)"))
    else:
        add(CheckResult("microphone", OK,
                        "capture devices present; FTD_MIC_DEVICE=%s" % cfg.mic_device))
        if record:
            from .audio.alsa import ArecordMicrophone
            from .audio.base import AudioError

            try:
                wav = ArecordMicrophone(cfg.mic_device, cfg.record_rate).record_wav(1.0)
                from .audio.level import wav_rms

                level = wav_rms(wav)
                add(CheckResult(
                    "mic-capture", OK,
                    "recorded %d bytes, RMS=%.0f (hands-free gate "
                    "FTD_VOICE_RMS_THRESHOLD=%.0f; speak during the capture "
                    "to calibrate)" % (len(wav), level, cfg.voice_rms_threshold)))
            except AudioError as exc:
                add(CheckResult("mic-capture", FAIL, str(exc)))

    aplay_list = _run(["aplay", "-l"])
    if aplay_list is None:
        add(CheckResult("speaker", WARN, "aplay not found"))
    elif "card" not in aplay_list:
        add(CheckResult("speaker", WARN, "no ALSA playback devices found"))
    else:
        add(CheckResult("speaker", OK,
                        "playback devices present; FTD_SPEAKER_DEVICE=%s" % cfg.speaker_device))

    # --- Online provider smoke tests (image + audio, still no motion) -----------
    if online:
        results.extend(_online_checks(cfg))
    else:
        add(CheckResult("providers", WARN,
                        "skipped live API smoke test (run `ftd doctor --online`)"))

    add(CheckResult(
        "safety-note", WARN,
        "Software watchdogs cannot stop motors if this process, the OS, or "
        "the HAT fails. Keep the physical power switch reachable.",
    ))
    return results


def _online_checks(cfg: Config) -> List[CheckResult]:
    from .camera.mock import SyntheticCamera
    from .providers.base import ProviderError
    from .providers.cloudflare import CloudflareSpeech, CloudflareVision

    results: List[CheckResult] = []
    try:
        vision = CloudflareVision(cfg)
        started = time.monotonic()
        answers = vision.ask(
            [SyntheticCamera().capture_jpeg()],
            {"task": "connectivity smoke test"},
            {"smoke": {"type": "noul",
                       "instructions": "Is this image mostly a single flat color?"}},
        )
        elapsed = time.monotonic() - started
        ok = isinstance(answers.get("smoke"), dict)
        results.append(CheckResult(
            "clef-flash", OK if ok else FAIL,
            "answered in %.2fs: %s" % (elapsed, answers.get("smoke"))))
    except ProviderError as exc:
        results.append(CheckResult("clef-flash", FAIL, str(exc)))

    try:
        from .audio.mock import silence_wav

        speech = CloudflareSpeech(cfg)
        started = time.monotonic()
        transcription = speech.transcribe(silence_wav(1.0), cfg.stt_language)
        elapsed = time.monotonic() - started
        results.append(CheckResult(
            "whisper", OK,
            "answered in %.2fs (1s silence -> %r)" % (elapsed, transcription.text)))
    except ProviderError as exc:
        results.append(CheckResult("whisper", FAIL, str(exc)))
    return results


def format_results(results: List[CheckResult]) -> str:
    width = max(len(r.name) for r in results)
    lines = []
    for r in results:
        lines.append("[%-4s] %-*s %s" % (r.level, width, r.name, r.detail))
    worst = FAIL if any(r.level == FAIL for r in results) else OK
    lines.append("")
    lines.append("doctor: %s" % ("problems found" if worst == FAIL else "no blocking problems"))
    return "\n".join(lines)
