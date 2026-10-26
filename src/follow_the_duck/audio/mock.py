"""Mock microphone returning canned WAV bytes (or injected failures)."""

from __future__ import annotations

import io
import struct
import wave

from .base import AudioError, Microphone


def silence_wav(seconds: float = 1.0, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(struct.pack("<h", 0) * int(rate * seconds))
    return buf.getvalue()


def tone_wav(seconds: float = 1.0, rate: int = 16000, amplitude: int = 8000,
             freq_hz: float = 440.0) -> bytes:
    """A loud sine tone — stands in for 'someone spoke' in tests."""
    import math

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        count = int(rate * seconds)
        frames = b"".join(
            struct.pack("<h", int(amplitude * math.sin(2 * math.pi * freq_hz * i / rate)))
            for i in range(count)
        )
        wav.writeframes(frames)
    return buf.getvalue()


class MockMicrophone(Microphone):
    def __init__(self, wav_bytes: bytes = b"", fail: bool = False) -> None:
        self._wav = wav_bytes or silence_wav(1.0)
        self.fail = fail
        self.recordings = 0

    def record_wav(self, seconds: float) -> bytes:
        if self.fail:
            raise AudioError("injected microphone failure")
        self.recordings += 1
        return self._wav
