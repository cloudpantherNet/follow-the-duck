"""On-device wake-word detection (optional).

Backend: openWakeWord (https://github.com/dscripka/openWakeWord) — small
models that run in real time on a Raspberry Pi 3, fully local, no audio
leaves the device until *after* the wake word fires. Install on the Pi with:

    .venv/bin/pip install -e '.[wakeword]'
    .venv/bin/python -m follow_the_duck.wakeword --download   # fetch models

Only pretrained phrases are available without training (hey_jarvis, alexa,
hey_mycroft, ...). A custom "hey duck" model would need openWakeWord's
training pipeline — documented as future work, not pretended here.

The detector consumes 80 ms PCM frames (16 kHz mono S16_LE) and returns True
once per detection, with a cooldown so one utterance can't double-trigger.
"""

from __future__ import annotations

import abc
import time
from typing import Optional


class WakeWordError(RuntimeError):
    pass


class WakeWordDetector(abc.ABC):
    @abc.abstractmethod
    def feed(self, frame_pcm: bytes) -> bool:
        """Feed one 80 ms S16_LE frame; True iff the wake word just fired."""

    def reset(self) -> None:  # pragma: no cover - trivial default
        pass


class OpenWakeWordDetector(WakeWordDetector):
    def __init__(
        self,
        model: str = "hey_jarvis_v0.1",
        threshold: float = 0.5,
        cooldown_s: float = 2.0,
        now_fn=time.monotonic,
    ) -> None:
        try:
            import numpy  # noqa: F401  (openwakeword dependency)
            from openwakeword.model import Model
        except ImportError as exc:
            raise WakeWordError(
                "openwakeword not installed — run ./deploy/install.sh (on "
                "Python >= 3.13 the plain pip extra fails on tflite-runtime; "
                "the installer falls back to an ONNX-only install) (%s)" % exc
            )
        self._np = numpy
        try:
            self._model = Model(
                wakeword_models=[model], inference_framework="onnx"
            )
        except Exception as exc:
            raise WakeWordError(
                "could not load wake-word model %r: %s. If models are not "
                "downloaded yet, run: python -m follow_the_duck.wakeword "
                "--download" % (model, exc)
            )
        self._threshold = threshold
        self._cooldown_s = cooldown_s
        self._now = now_fn
        self._last_fired: Optional[float] = None

    def feed(self, frame_pcm: bytes) -> bool:
        samples = self._np.frombuffer(frame_pcm, dtype=self._np.int16)
        scores = self._model.predict(samples)
        score = max(scores.values()) if scores else 0.0
        if score < self._threshold:
            return False
        now = self._now()
        if self._last_fired is not None and now - self._last_fired < self._cooldown_s:
            return False
        self._last_fired = now
        self.reset()
        return True

    def reset(self) -> None:
        try:
            self._model.reset()
        except Exception:
            pass


class MockWakeWordDetector(WakeWordDetector):
    """Fires on scripted frame indices (1-based count of feed() calls)."""

    def __init__(self, fire_on=()) -> None:
        self.fire_on = set(fire_on)
        self.fed = 0
        self.resets = 0

    def feed(self, frame_pcm: bytes) -> bool:
        self.fed += 1
        return self.fed in self.fire_on

    def reset(self) -> None:
        self.resets += 1


def _download_models() -> None:  # pragma: no cover - network/Pi utility
    try:
        import openwakeword.utils as utils
    except ImportError as exc:
        raise SystemExit("openwakeword not installed: %s" % exc)
    utils.download_models()
    print("openWakeWord models downloaded.")


if __name__ == "__main__":  # pragma: no cover
    import sys

    if "--download" in sys.argv:
        _download_models()
    else:
        print(__doc__)
