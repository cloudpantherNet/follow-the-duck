"""Wake-word cycle tests: detector + stream + STT wiring (no openWakeWord
needed — the real model backend is exercised on the Pi via `ftd doctor`)."""

from __future__ import annotations

import struct

import pytest

from follow_the_duck.audio.base import AudioError
from follow_the_duck.audio.stream import (
    FRAME_SAMPLES,
    MockAudioStream,
    pcm_to_wav,
)
from follow_the_duck.audio.level import wav_rms
from follow_the_duck.config import Config
from follow_the_duck.intent import CommandKind
from follow_the_duck.providers.mock import MockSpeech
from follow_the_duck.voiceloop import wake_word_command
from follow_the_duck.wakeword import (
    MockWakeWordDetector,
    OpenWakeWordDetector,
    WakeWordError,
)


def cfg() -> Config:
    c = Config()
    c.record_seconds = 0.2  # keep mock recordings tiny
    c.voice_rms_threshold = 300.0
    return c


def loud_stream(**kw):
    """A stream whose audio is loud enough to pass the post-wake RMS gate."""
    return MockAudioStream(default_amplitude=5000, **kw)


# ----------------------------------------------------------------- plumbing


def test_pcm_to_wav_roundtrip():
    pcm = struct.pack("<h", 5000) * FRAME_SAMPLES
    wav = pcm_to_wav(pcm)
    assert wav[:4] == b"RIFF"
    assert wav_rms(wav) == pytest.approx(5000.0)


def test_mock_stream_record_wav_length():
    wav = loud_stream().record_wav(0.2)
    assert wav_rms(wav) > 300


def test_openwakeword_backend_missing_raises_cleanly():
    """On machines without the optional extra, the detector must fail with a
    clear WakeWordError (the CLI then falls back to the loudness gate)."""
    try:
        import openwakeword  # noqa: F401

        pytest.skip("openwakeword installed; backend failure not simulated")
    except ImportError:
        pass
    with pytest.raises(WakeWordError):
        OpenWakeWordDetector("hey_jarvis_v0.1")


# -------------------------------------------------------------- wake cycle


def test_wake_then_command_starts_mission():
    detector = MockWakeWordDetector(fire_on={3})
    speech = MockSpeech(text="find the yellow duck")
    woke = []
    command = wake_word_command(
        loud_stream(), detector, speech, cfg(), on_wake=lambda: woke.append(1)
    )
    assert woke == [1]
    assert speech.calls == 1
    assert command is not None and command.kind == CommandKind.FIND
    assert command.target == "yellow duck"
    assert detector.fed == 3  # frames before the wake stayed on-device


def test_no_wake_no_cloud_call():
    """Without a detection, frames keep streaming and nothing is uploaded;
    the stream's read cap ends the test run via AudioError -> None."""
    detector = MockWakeWordDetector(fire_on=set())
    speech = MockSpeech(text="find the duck")
    command = wake_word_command(
        loud_stream(max_reads=50), detector, speech, cfg()
    )
    assert command is None
    assert speech.calls == 0


def test_silence_after_wake_is_not_transcribed():
    detector = MockWakeWordDetector(fire_on={1})
    speech = MockSpeech(text="should never be used")
    quiet = MockAudioStream(default_amplitude=0)
    command = wake_word_command(quiet, detector, speech, cfg())
    assert command is None
    assert speech.calls == 0  # RMS gate after the wake word still applies


def test_gibberish_after_wake_is_ignored():
    detector = MockWakeWordDetector(fire_on={1})
    speech = MockSpeech(text="nice weather we are having")
    assert wake_word_command(loud_stream(), detector, speech, cfg()) is None


def test_should_stop_exits_between_frames():
    detector = MockWakeWordDetector(fire_on=set())
    speech = MockSpeech(text="find the duck")
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 5

    command = wake_word_command(
        loud_stream(), detector, speech, cfg(), should_stop=should_stop
    )
    assert command is None
    assert detector.fed <= 5
    assert speech.calls == 0


def test_stream_failure_mid_listen_returns_none():
    stream = MockAudioStream(
        frames=[struct.pack("<h", 0) * FRAME_SAMPLES, AudioError("device gone")]
    )
    detector = MockWakeWordDetector(fire_on=set())
    command = wake_word_command(stream, detector, MockSpeech(text="x"), cfg())
    assert command is None


def test_stop_command_after_wake():
    detector = MockWakeWordDetector(fire_on={1})
    speech = MockSpeech(text="stop")
    command = wake_word_command(loud_stream(), detector, speech, cfg())
    assert command is not None and command.kind == CommandKind.STOP
