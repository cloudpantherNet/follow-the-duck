"""ALSA microphone via the `arecord` subprocess.

The device comes from configuration (FTD_MIC_DEVICE, e.g. "plughw:1,0"),
never a hardcoded card number — inspect with `arecord -l` / `ftd doctor`.
16 kHz mono 16-bit WAV keeps uploads small and is what Whisper expects.
"""

from __future__ import annotations

import subprocess

from .base import AudioError, Microphone


class ArecordMicrophone(Microphone):
    def __init__(self, device: str = "default", rate: int = 16000) -> None:
        self._device = device
        self._rate = rate

    def record_wav(self, seconds: float) -> bytes:
        seconds = max(0.5, min(15.0, float(seconds)))
        cmd = [
            "arecord",
            "-D", self._device,
            "-f", "S16_LE",
            "-c", "1",
            "-r", str(self._rate),
            "-d", str(int(round(seconds))),
            "-t", "wav",
            "-q", "-",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=seconds + 10)
        except FileNotFoundError:
            raise AudioError("arecord not found (ALSA utils missing?)")
        except subprocess.TimeoutExpired:
            raise AudioError("recording timed out")
        if proc.returncode != 0 or len(proc.stdout) < 100:
            raise AudioError(
                "arecord failed rc=%s: %s"
                % (proc.returncode, proc.stderr.decode(errors="replace")[-200:])
            )
        return proc.stdout


def list_capture_devices() -> str:
    """`arecord -l` output for doctor/diagnostics (empty string on failure)."""
    try:
        proc = subprocess.run(["arecord", "-l"], capture_output=True, timeout=10)
        return proc.stdout.decode(errors="replace")
    except Exception:
        return ""
