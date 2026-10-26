"""Command-line interface.

    ftd find "yellow duck"        typed-target mission
    ftd listen [--loop]           push-to-talk (Enter records; typing works too)
    ftd stop                      stop a running ftd process (local, no cloud)
    ftd status                    show the latest mission status
    ftd doctor [--online]         health checks (never moves the wheels)
    ftd replay SCRIPT [TARGET]    deterministic mock mission from a JSON script

During a mission, pressing Enter (or Ctrl-C, or SIGTERM from `ftd stop`)
stops the robot immediately and locally — never via the cloud.
"""

from __future__ import annotations

import argparse
import atexit
import logging
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Optional

from . import __version__
from .config import Config, config_from_env
from .intent import CommandKind, parse_command
from .models import MissionState
from .runtime import Runtime, build_runtime
from .statuslog import read_status

log = logging.getLogger("ftd")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def _pidfile(cfg: Config) -> Path:
    return Path(cfg.runtime_dir) / "ftd.pid"


def _write_pidfile(cfg: Config) -> None:
    path = _pidfile(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()))
    atexit.register(lambda: path.unlink(missing_ok=True))


def _install_signal_handlers(runtime: Runtime) -> None:
    def handler(signum, _frame):
        log.info("signal %s -> STOP", signum)
        runtime.mission.request_stop()

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            pass  # non-main thread or unsupported platform
    atexit.register(runtime.supervisor.stop, "process exit")


def _start_enter_to_stop(runtime: Runtime) -> threading.Event:
    """While a mission runs, any line on stdin triggers a local STOP."""
    done = threading.Event()

    def watch() -> None:
        try:
            while not done.is_set():
                line = sys.stdin.readline()
                if done.is_set():
                    return
                if line == "":  # EOF (e.g. systemd): nothing to watch
                    return
                runtime.mission.request_stop()
                print("STOP requested.")
                return
        except Exception:
            pass

    thread = threading.Thread(target=watch, name="ftd-stop-key", daemon=True)
    thread.start()
    return done


def _run_mission(runtime: Runtime, target: str, interactive: bool = True) -> int:
    _write_pidfile(runtime.cfg)
    _install_signal_handlers(runtime)
    runtime.start()
    stopper = None
    if interactive and sys.stdin.isatty():
        print("(press Enter to STOP)")
        stopper = _start_enter_to_stop(runtime)
    try:
        status = runtime.mission.run(target)
    finally:
        if stopper is not None:
            stopper.set()
        runtime.supervisor.stop("mission over")
    print("\nResult: %s — %s" % (status.state.value, status.message))
    print(
        "steps=%d vision_calls=%d elapsed=%.1fs latency=%s"
        % (status.steps_taken, status.vision_calls, status.elapsed_s, status.last_latency)
    )
    return 0 if status.state == MissionState.ARRIVED else 1


# ---------------------------------------------------------------- commands


def cmd_find(args, cfg: Config) -> int:
    runtime = build_runtime(
        cfg,
        dry_run=args.dry_run,
        vision_script=Path(args.script) if args.script else None,
        fixtures_dir=Path(args.fixtures) if args.fixtures else None,
    )
    try:
        return _run_mission(runtime, args.target)
    finally:
        runtime.shutdown()


def cmd_replay(args, cfg: Config) -> int:
    cfg.hardware_backend = "mock"
    cfg.camera_backend = "mock"
    runtime = build_runtime(
        cfg,
        dry_run=True,
        vision_script=Path(args.script),
        fixtures_dir=Path(args.fixtures) if args.fixtures else None,
    )
    try:
        return _run_mission(runtime, args.target, interactive=False)
    finally:
        runtime.shutdown()


def cmd_listen(args, cfg: Config) -> int:
    runtime = build_runtime(cfg, dry_run=args.dry_run)
    microphone = _make_microphone(cfg, args.dry_run)
    try:
        _write_pidfile(cfg)
        _install_signal_handlers(runtime)
        runtime.start()
        while True:
            runtime.status.update(
                MissionState.IDLE, "Idle. Press Enter to talk, or type a command."
            )
            print(
                "\nPress Enter to record %.0fs, type a command "
                "('find the ...', 'stop', 'quit'):" % cfg.record_seconds
            )
            try:
                line = input("> ").strip()
            except EOFError:
                return 0
            if line.lower() in ("quit", "q", "exit"):
                return 0
            if line:
                command = parse_command(line)
            else:
                command = _record_and_parse(runtime, microphone, cfg)
                if command is None:
                    continue
            if command.kind == CommandKind.FIND:
                print("Target: %s" % command.target)
                _run_mission(runtime, command.target)
                if not args.loop:
                    return 0
            elif command.kind in (CommandKind.STOP, CommandKind.CANCEL):
                runtime.mission.request_stop()
                print("Stopped (idle).")
            else:
                print("I did not catch a target. Nothing started.")
    finally:
        runtime.shutdown()


def _make_microphone(cfg: Config, dry_run: bool):
    import shutil as _shutil

    if dry_run or cfg.audio_backend == "mock":
        from .audio.mock import MockMicrophone

        return MockMicrophone()
    if cfg.audio_backend in ("auto", "alsa") and _shutil.which("arecord"):
        from .audio.alsa import ArecordMicrophone

        return ArecordMicrophone(cfg.mic_device, cfg.record_rate)
    from .audio.mock import MockMicrophone

    log.warning("arecord unavailable; using mock microphone")
    return MockMicrophone()


def _record_and_parse(runtime: Runtime, microphone, cfg: Config):
    from .audio.base import AudioError
    from .providers.base import ProviderError

    if runtime.speech is None:
        print("Speech-to-text is not configured (set Cloudflare credentials); "
              "type the command instead.")
        return None
    runtime.status.update(MissionState.LISTENING, "Listening...")
    print("Recording %.0fs... speak now." % cfg.record_seconds)
    try:
        wav = microphone.record_wav(cfg.record_seconds)
    except AudioError as exc:
        print("Recording failed: %s" % exc)
        return None
    print("Transcribing...")
    try:
        transcription = runtime.speech.transcribe(wav, cfg.stt_language)
    except ProviderError as exc:
        print("Transcription failed: %s" % exc)
        return None
    print("Heard: %r" % transcription.text)
    command = parse_command(transcription.text)
    if command.kind == CommandKind.NONE:
        print("That did not sound like a command. Nothing started.")
        return None
    return command


def cmd_stop(args, cfg: Config) -> int:
    path = _pidfile(cfg)
    try:
        pid = int(path.read_text().strip())
    except (OSError, ValueError):
        print("No running ftd process found (%s)." % path)
        return 1
    try:
        os.kill(pid, signal.SIGTERM)
        print("Sent STOP to pid %d." % pid)
        return 0
    except ProcessLookupError:
        print("Process %d is not running." % pid)
        path.unlink(missing_ok=True)
        return 1


def cmd_status(args, cfg: Config) -> int:
    status = read_status(cfg.runtime_dir)
    if status is None:
        print("No status recorded yet (%s/status.json)." % cfg.runtime_dir)
        return 1
    for key in ("state", "message", "target", "mission_id", "steps_taken",
                "vision_calls", "elapsed_s", "last_latency"):
        print("%-13s %s" % (key + ":", status.get(key)))
    return 0


def cmd_doctor(args, cfg: Config) -> int:
    from .doctor import FAIL, format_results, run_checks

    results = run_checks(cfg, online=args.online, record=args.record)
    print(format_results(results))
    return 1 if any(r.level == FAIL for r in results) else 0


# ------------------------------------------------------------------- parser


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ftd", description="Follow the Duck — PiCar-X object search demo"
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--env", default=".env", help="path to .env file")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_find = sub.add_parser("find", help="run a mission for a typed target")
    p_find.add_argument("target")
    p_find.add_argument("--dry-run", action="store_true",
                        help="mock hardware/camera (runs anywhere)")
    p_find.add_argument("--script", help="JSON vision script (implies mock vision)")
    p_find.add_argument("--fixtures", help="directory of .jpg frames to replay")
    p_find.set_defaults(func=cmd_find)

    p_listen = sub.add_parser("listen", help="push-to-talk voice command")
    p_listen.add_argument("--loop", action="store_true",
                          help="return to listening after each mission")
    p_listen.add_argument("--dry-run", action="store_true")
    p_listen.set_defaults(func=cmd_listen)

    p_replay = sub.add_parser("replay", help="deterministic mock mission")
    p_replay.add_argument("script", help="JSON vision script")
    p_replay.add_argument("target", nargs="?", default="yellow duck")
    p_replay.add_argument("--fixtures", help="directory of .jpg frames")
    p_replay.set_defaults(func=cmd_replay)

    p_stop = sub.add_parser("stop", help="stop a running ftd process")
    p_stop.set_defaults(func=cmd_stop)

    p_status = sub.add_parser("status", help="show latest mission status")
    p_status.set_defaults(func=cmd_status)

    p_doctor = sub.add_parser("doctor", help="system health checks (no motion)")
    p_doctor.add_argument("--online", action="store_true",
                          help="also run live Cloudflare smoke tests")
    p_doctor.add_argument("--record", action="store_true",
                          help="also record 1s from the microphone")
    p_doctor.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    cfg = config_from_env(Path(args.env))
    problems = cfg.validate()
    if problems and args.command != "doctor":
        for problem in problems:
            print("config error: %s" % problem, file=sys.stderr)
        return 2
    return args.func(args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
