"""Microphone interface for push-to-talk recording."""

from __future__ import annotations

import abc


class AudioError(RuntimeError):
    pass


class Microphone(abc.ABC):
    @abc.abstractmethod
    def record_wav(self, seconds: float) -> bytes:
        """Record for `seconds` and return WAV bytes. Raises AudioError."""
