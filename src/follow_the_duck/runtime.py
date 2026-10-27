"""Runtime wiring: build real or mock components from configuration.

`--dry-run` (or backend=mock settings) swaps in mocks everywhere so the full
mission loop runs on any machine with no robot, no camera, and optionally no
cloud credentials (with a scripted vision provider).
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .camera.base import Camera
from .camera.mock import ReplayCamera, SyntheticCamera
from .clock import Clock
from .config import Config
from .hardware.base import Chassis, DistanceSensor, GrayscaleSensor, SpeakerOut
from .hardware.mock import MockChassis, MockDistanceSensor, MockGrayscale, MockSpeaker
from .mission import MissionController
from .perception import Perceiver
from .providers.base import ProviderError, SpeechProvider, VisionProvider
from .providers.mock import ScriptedVision, clef_answers
from .statuslog import StatusBoard
from .supervisor import MotorSupervisor, SensorPoller
from .telemetry import EventSink, TelemetryClient


def _picarx_available() -> bool:
    return importlib.util.find_spec("picarx") is not None


@dataclass
class Runtime:
    cfg: Config
    clock: Clock
    chassis: Chassis
    distance: DistanceSensor
    grayscale: Optional[GrayscaleSensor]
    camera: Camera
    speaker: SpeakerOut
    vision: VisionProvider
    speech: Optional[SpeechProvider]
    poller: SensorPoller
    supervisor: MotorSupervisor
    status: StatusBoard
    mission: MissionController
    sink: Optional[EventSink] = None

    def start(self) -> None:
        self.poller.start()
        self.supervisor.start_watchdog()

    def shutdown(self) -> None:
        try:
            self.supervisor.shutdown()
        finally:
            self.poller.stop()
            if self.sink is not None:
                try:
                    self.sink.close()
                except Exception:
                    pass
            try:
                self.camera.close()
            except Exception:
                pass
            try:
                self.chassis.close()
            except Exception:
                pass


def load_vision_script(path: Path) -> List[object]:
    """Load a replay script: a JSON list of clef_answers() keyword dicts
    (see tests/fixtures/replay_center.json), or {"error": "..."} entries."""
    entries = json.loads(Path(path).read_text())
    if not isinstance(entries, list) or not entries:
        raise ValueError("replay script must be a non-empty JSON list")
    script: List[object] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("replay entries must be objects")
        if "error" in entry:
            script.append(ProviderError(str(entry["error"])))
        else:
            script.append(clef_answers(**entry))
    return script


def build_runtime(
    cfg: Config,
    dry_run: bool = False,
    vision_script: Optional[Path] = None,
    fixtures_dir: Optional[Path] = None,
    clock: Optional[Clock] = None,
) -> Runtime:
    clock = clock or Clock()
    use_real_hw = (
        not dry_run
        and cfg.hardware_backend != "mock"
        and (cfg.hardware_backend == "picarx" or _picarx_available())
    )

    if use_real_hw:
        from .hardware.picarx_hw import EspeakSpeaker, make_picarx_bundle

        chassis, distance, grayscale = make_picarx_bundle()
        speaker: SpeakerOut = EspeakSpeaker(cfg.speaker_device)
    else:
        chassis = MockChassis()
        # Dry-run demo physics: range shrinks a little with every drive pulse
        # so replay missions can actually "arrive".
        mock_chassis = chassis

        def _demo_range() -> float:
            drives = sum(1 for e in mock_chassis.log if e[0] == "drive")
            return max(26.0, 150.0 - 25.0 * drives)

        distance = MockDistanceSensor(_demo_range)
        grayscale = MockGrayscale()
        speaker = MockSpeaker()

    if dry_run or cfg.camera_backend == "mock":
        camera: Camera = (
            ReplayCamera(fixtures_dir) if fixtures_dir else SyntheticCamera()
        )
    else:
        from .camera.pi import make_camera

        camera = make_camera(cfg)

    if vision_script is not None:
        vision: VisionProvider = ScriptedVision(load_vision_script(vision_script))
        speech: Optional[SpeechProvider] = None
    else:
        from .providers.cloudflare import CloudflareSpeech, CloudflareVision

        vision = CloudflareVision(cfg)
        try:
            speech = CloudflareSpeech(cfg)
        except ProviderError:
            speech = None

    poller = SensorPoller(distance, cfg, clock, grayscale)
    poller.poll_once()
    supervisor = MotorSupervisor(chassis, poller, cfg, clock)
    status = StatusBoard(cfg.runtime_dir, speaker)
    perceiver = Perceiver(vision, cfg, clock)
    sink: Optional[EventSink] = None
    if cfg.telemetry_url and cfg.telemetry_token:
        sink = TelemetryClient(
            cfg.telemetry_url, cfg.telemetry_token,
            send_images=cfg.telemetry_send_images,
        )
    mission = MissionController(
        cfg, clock, chassis, supervisor, camera, perceiver, status, sink=sink
    )
    return Runtime(
        sink=sink,
        cfg=cfg,
        clock=clock,
        chassis=chassis,
        distance=distance,
        grayscale=grayscale,
        camera=camera,
        speaker=speaker,
        vision=vision,
        speech=speech,
        poller=poller,
        supervisor=supervisor,
        status=status,
        mission=mission,
    )
