"""Hands-free voice loop: the laptop-free listening cycle.

Runs only while the robot is IDLE (it is always stopped while recording).
Each cycle records a short chunk, measures loudness locally, and only sends
chunks that plausibly contain speech to the cloud for transcription — a quiet
room costs zero API calls. A parsed FIND command starts a mission; everything
else is discarded. Cloud transcription is never a stop path: during a mission
the loop is not even running (use `ftd stop`, Enter, or the power switch).
"""

from __future__ import annotations

import logging
from typing import Optional

from .audio.base import AudioError, Microphone
from .audio.level import wav_rms
from .audio.stream import AudioStream
from .config import Config
from .intent import Command, CommandKind, parse_command
from .providers.base import ProviderError, SpeechProvider
from .wakeword import WakeWordDetector

log = logging.getLogger("ftd.voice")


def next_voice_command(
    microphone: Microphone,
    speech: SpeechProvider,
    cfg: Config,
) -> Optional[Command]:
    """One hands-free cycle: record -> local loudness gate -> STT -> parse.
    Returns a Command only when something actionable was clearly heard;
    None means 'nothing happened, keep listening'. Never raises."""
    try:
        wav = microphone.record_wav(cfg.hands_free_chunk_s)
    except AudioError as exc:
        log.warning("hands-free recording failed: %s", exc)
        return None
    level = wav_rms(wav)
    if level < cfg.voice_rms_threshold:
        log.debug("chunk below speech threshold (rms=%.0f)", level)
        return None  # silence: no cloud call, no cost
    try:
        transcription = speech.transcribe(wav, cfg.stt_language)
    except ProviderError as exc:
        log.warning("hands-free transcription failed: %s", exc)
        return None
    text = transcription.text.strip()
    if not text:
        return None
    log.info("heard: %r", text)
    command = parse_command(text)
    if command.kind == CommandKind.NONE:
        log.info("not a command; ignoring")
        return None
    return command


def wake_word_command(
    stream: AudioStream,
    detector: WakeWordDetector,
    speech: SpeechProvider,
    cfg: Config,
    should_stop=None,
    on_wake=None,
) -> Optional[Command]:
    """Wake-word cycle: feed 80 ms frames to the on-device detector; when the
    wake word fires, record the command utterance from the same stream and
    transcribe it. Audio reaches the cloud only AFTER a local wake detection.

    ``should_stop`` is polled between frames (each ~80 ms) so SIGTERM exits
    promptly; ``on_wake`` runs right after detection (beep/status). Returns a
    Command, or None for 'keep listening'. Never raises on audio/STT faults —
    an unrecoverable stream error also returns None and the caller rebuilds
    the stream."""
    try:
        while True:
            if should_stop is not None and should_stop():
                return None
            frame = stream.read_frame()
            if detector.feed(frame):
                break
    except AudioError as exc:
        log.warning("wake-word stream failed: %s", exc)
        return None
    log.info("wake word detected")
    if on_wake is not None:
        try:
            on_wake()
        except Exception:
            pass
    try:
        wav = stream.record_wav(cfg.record_seconds)
    except AudioError as exc:
        log.warning("command recording failed: %s", exc)
        return None
    if wav_rms(wav) < cfg.voice_rms_threshold:
        log.info("nothing said after the wake word; back to listening")
        return None
    try:
        transcription = speech.transcribe(wav, cfg.stt_language)
    except ProviderError as exc:
        log.warning("transcription failed: %s", exc)
        return None
    text = transcription.text.strip()
    if not text:
        return None
    log.info("heard: %r", text)
    command = parse_command(text)
    if command.kind == CommandKind.NONE:
        log.info("not a command; ignoring")
        return None
    return command
