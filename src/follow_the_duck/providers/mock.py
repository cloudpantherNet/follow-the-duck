"""Scripted mock providers for tests, dry-runs, and replay.

ScriptedVision returns pre-authored *answer* dicts in the same typed format
clef-flash produces, so the full perception validation path runs in tests.
Fault injection: raise entries simulate timeouts/drops; `delay_s` simulates
slow inference against a fake clock.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Union

from .base import ProviderError, SpeechProvider, Transcription, VisionProvider


def clef_answers(
    present: float = 0.0,
    bearing: str = "not_visible",
    bearing_conf: float = 0.9,
    proximity: str = "not_visible",
    floor_left: float = 0.5,
    floor_center: float = 0.5,
    floor_right: float = 0.5,
    multiple: float = 0.0,
) -> Dict[str, dict]:
    """Helper to author clef-style typed answers concisely in tests."""

    def choice(option: str, conf: float, options: List[str]) -> dict:
        probs = {o: (conf if o == option else (1 - conf) / (len(options) - 1))
                 for o in options}
        return {
            "type": "choice",
            "choice": option,
            "probabilities": probs,
            "confidence": conf,
        }

    return {
        "present": {"type": "noul", "noul": present},
        "bearing": choice(
            bearing, bearing_conf, ["left", "center", "right", "not_visible"]
        ),
        "proximity": choice(
            proximity, 0.8, ["very_close", "near", "mid", "far", "not_visible"]
        ),
        "floor_left": {"type": "noul", "noul": floor_left},
        "floor_center": {"type": "noul", "noul": floor_center},
        "floor_right": {"type": "noul", "noul": floor_right},
        "multiple": {"type": "noul", "noul": multiple},
    }


ScriptEntry = Union[Dict[str, dict], Exception, Callable[[], Dict[str, dict]]]


class ScriptedVision(VisionProvider):
    """Plays back a list of answer dicts; the last entry repeats. Exception
    entries are raised (simulating timeouts/network failures). A callable
    entry is invoked at ask() time (for pan-dependent scripts)."""

    def __init__(
        self,
        script: List[ScriptEntry],
        clock=None,
        delay_s: float = 0.0,
    ) -> None:
        if not script:
            raise ValueError("script must not be empty")
        self._script = script
        self._index = 0
        self._clock = clock
        self._delay_s = delay_s
        self.calls = 0
        self.last_questions: Optional[Dict[str, dict]] = None
        self.last_state: object = None

    def ask(
        self, images: List[bytes], state: object, questions: Dict[str, dict]
    ) -> Dict[str, dict]:
        self.calls += 1
        self.last_questions = questions
        self.last_state = state
        if self._clock is not None and self._delay_s:
            self._clock.sleep(self._delay_s)
        entry = self._script[min(self._index, len(self._script) - 1)]
        self._index += 1
        if isinstance(entry, Exception):
            raise entry
        if callable(entry):
            return entry()
        return entry


class MockSpeech(SpeechProvider):
    def __init__(self, text: str = "", fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.calls = 0

    def transcribe(self, wav_bytes: bytes, language: str = "en") -> Transcription:
        self.calls += 1
        if self.fail:
            raise ProviderError("injected STT failure")
        return Transcription(text=self.text, language=language)
