"""Local audio level measurement (no cloud, no dependencies).

Used by hands-free mode to decide whether a recorded chunk contains speech
before spending a Whisper call on it. Pure stdlib: `audioop` was removed in
Python 3.12+, so RMS is computed manually.
"""

from __future__ import annotations

import array
import io
import math
import wave


def wav_rms(wav_bytes: bytes) -> float:
    """RMS amplitude of a 16-bit mono/stereo WAV. Returns 0.0 for anything
    unreadable or non-16-bit, which callers must treat as 'no speech heard'
    (fail quiet, never fail loud)."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
            if reader.getsampwidth() != 2:
                return 0.0
            frames = reader.readframes(reader.getnframes())
    except (wave.Error, EOFError, OSError):
        return 0.0
    samples = array.array("h")
    usable = len(frames) - (len(frames) % 2)
    if usable == 0:
        return 0.0
    samples.frombytes(frames[:usable])
    return math.sqrt(sum(s * s for s in samples) / len(samples))
