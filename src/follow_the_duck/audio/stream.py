"""Streaming microphone input.

Wake-word detection needs a continuous stream of small PCM frames (80 ms),
not fixed-length clips, so this wraps a long-running
``arecord -t raw`` process. After a wake word fires, the *same* stream
records the command utterance (no device re-open race). MockAudioStream
serves tests and dry-runs.
"""

from __future__ import annotations

import io
import struct
import subprocess
import wave
from typing import Optional

from .base import AudioError

#: openWakeWord's native frame: 80 ms at 16 kHz mono 16-bit.
FRAME_SAMPLES = 1280
SAMPLE_RATE = 16000


def pcm_to_wav(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    """Wrap raw S16_LE mono PCM into a WAV container (for Whisper upload)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return buf.getvalue()


class AudioStream:
    """Interface: read exact-size PCM frames; record a WAV span on demand."""

    def read_frame(self, samples: int = FRAME_SAMPLES) -> bytes:
        raise NotImplementedError

    def record_wav(self, seconds: float) -> bytes:
        total = int(SAMPLE_RATE * seconds)
        chunks = []
        collected = 0
        while collected < total:
            take = min(FRAME_SAMPLES, total - collected)
            chunks.append(self.read_frame(take))
            collected += take
        return pcm_to_wav(b"".join(chunks))

    def close(self) -> None:  # pragma: no cover - trivial default
        pass


class ArecordStream(AudioStream):
    """Continuous capture via a long-running arecord subprocess."""

    def __init__(self, device: str = "default", rate: int = SAMPLE_RATE) -> None:
        self._device = device
        self._rate = rate
        self._proc: Optional[subprocess.Popen] = None
        self._open()

    def _open(self) -> None:
        try:
            self._proc = subprocess.Popen(
                [
                    "arecord", "-D", self._device,
                    "-f", "S16_LE", "-c", "1", "-r", str(self._rate),
                    "-t", "raw", "-q", "-",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            raise AudioError("arecord not found (ALSA utils missing?)")

    def read_frame(self, samples: int = FRAME_SAMPLES) -> bytes:
        if self._proc is None or self._proc.stdout is None:
            raise AudioError("audio stream not open")
        need = samples * 2
        data = b""
        while len(data) < need:
            chunk = self._proc.stdout.read(need - len(data))
            if not chunk:
                code = self._proc.poll()
                self.close()
                raise AudioError("arecord stream ended (rc=%s)" % code)
            data += chunk
        return data

    def close(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None


class MockAudioStream(AudioStream):
    """Returns scripted PCM frames; raises AudioError when exhausted (so a
    runaway test loop fails instead of hanging)."""

    def __init__(self, frames=None, default_amplitude: int = 0,
                 max_reads: int = 10_000) -> None:
        self._frames = list(frames or [])
        self._amplitude = default_amplitude
        self._max_reads = max_reads
        self.reads = 0
        self.closed = False

    def read_frame(self, samples: int = FRAME_SAMPLES) -> bytes:
        self.reads += 1
        if self.reads > self._max_reads:
            raise AudioError("mock stream exhausted")
        if self._frames:
            frame = self._frames.pop(0)
            if isinstance(frame, Exception):
                raise frame
            return frame
        return struct.pack("<h", self._amplitude) * samples

    def close(self) -> None:
        self.closed = True
