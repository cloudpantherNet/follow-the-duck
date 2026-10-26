"""Hands-free voice loop: local loudness gate + STT + intent, no cloud waste."""

from __future__ import annotations

from follow_the_duck.audio.level import wav_rms
from follow_the_duck.audio.mock import MockMicrophone, silence_wav, tone_wav
from follow_the_duck.config import Config
from follow_the_duck.intent import CommandKind
from follow_the_duck.providers.mock import MockSpeech
from follow_the_duck.voiceloop import next_voice_command


def cfg() -> Config:
    c = Config()
    c.hands_free_chunk_s = 1.0
    c.voice_rms_threshold = 300.0
    return c


# ----------------------------------------------------------------- wav_rms


def test_rms_silence_is_zero():
    assert wav_rms(silence_wav(0.5)) == 0.0


def test_rms_tone_is_loud():
    assert wav_rms(tone_wav(0.5, amplitude=8000)) > 3000


def test_rms_garbage_is_quiet_not_crash():
    assert wav_rms(b"") == 0.0
    assert wav_rms(b"not a wav at all") == 0.0


# ------------------------------------------------------------- voice cycle


def test_silent_chunk_never_calls_stt():
    speech = MockSpeech(text="find the yellow duck")
    command = next_voice_command(MockMicrophone(silence_wav(1.0)), speech, cfg())
    assert command is None
    assert speech.calls == 0  # the local gate saved a cloud call


def test_loud_find_command_starts_mission():
    speech = MockSpeech(text="Find the yellow duck")
    command = next_voice_command(MockMicrophone(tone_wav(1.0)), speech, cfg())
    assert speech.calls == 1
    assert command is not None
    assert command.kind == CommandKind.FIND
    assert command.target == "yellow duck"


def test_loud_gibberish_is_ignored():
    speech = MockSpeech(text="the weather is nice today")
    assert next_voice_command(MockMicrophone(tone_wav(1.0)), speech, cfg()) is None


def test_empty_transcription_is_ignored():
    speech = MockSpeech(text="   ")
    assert next_voice_command(MockMicrophone(tone_wav(1.0)), speech, cfg()) is None


def test_stt_failure_is_tolerated():
    speech = MockSpeech(fail=True)
    assert next_voice_command(MockMicrophone(tone_wav(1.0)), speech, cfg()) is None


def test_mic_failure_is_tolerated():
    speech = MockSpeech(text="find the duck")
    mic = MockMicrophone(fail=True)
    assert next_voice_command(mic, speech, cfg()) is None
    assert speech.calls == 0


def test_stop_command_parsed_when_loud():
    speech = MockSpeech(text="stop")
    command = next_voice_command(MockMicrophone(tone_wav(1.0)), speech, cfg())
    assert command is not None and command.kind == CommandKind.STOP
