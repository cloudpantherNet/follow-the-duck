"""Provider interfaces.

VisionProvider is deliberately *transport only*: it carries typed questions
and returns typed answers. All interpretation (what to ask about a target,
how to map answers into a validated Observation) lives in perception.py, so
swapping Cloudflare for another provider never touches mission logic.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Dict, List


class ProviderError(RuntimeError):
    """Any provider failure (network, HTTP error, malformed response)."""


class ProviderTimeout(ProviderError):
    pass


@dataclass(frozen=True)
class Transcription:
    text: str
    language: str = ""
    duration_s: float = 0.0


class VisionProvider(abc.ABC):
    @abc.abstractmethod
    def ask(
        self,
        images: List[bytes],
        state: object,
        questions: Dict[str, dict],
    ) -> Dict[str, dict]:
        """Submit images + typed questions; return the raw answers mapping
        (question id -> typed answer dict). Raises ProviderError."""


class SpeechProvider(abc.ABC):
    @abc.abstractmethod
    def transcribe(self, wav_bytes: bytes, language: str = "en") -> Transcription:
        """Transcribe a short WAV recording. Raises ProviderError."""
