"""Student-facing narration + structured status file.

Short, plain sentences a ninth grader can follow on a projector:
"Scanning left", "Target seen", "Moving toward open floor", "Blocked",
"Found it".
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from .models import MissionState, MissionStatus

log = logging.getLogger("ftd")


class StatusBoard:
    """Keeps the current MissionStatus, logs student-friendly lines, and
    atomically writes ~/.ftd/status.json for `ftd status`."""

    def __init__(self, runtime_dir: str, speaker=None) -> None:
        self._dir = Path(runtime_dir)
        self._speaker = speaker
        self.status = MissionStatus()

    def update(
        self,
        state: Optional[MissionState] = None,
        message: Optional[str] = None,
        say: bool = False,
        **fields,
    ) -> None:
        if state is not None:
            self.status.state = state
        if message is not None:
            self.status.message = message
            log.info("[%s] %s", self.status.state.value, message)
            if say and self._speaker is not None:
                try:
                    self._speaker.say(message)
                except Exception:
                    pass
        for key, value in fields.items():
            setattr(self.status, key, value)
        self._write()

    def _write(self) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            data = dataclasses.asdict(self.status)
            data["state"] = self.status.state.value
            fd, tmp = tempfile.mkstemp(dir=str(self._dir), suffix=".tmp")
            with os.fdopen(fd, "w") as handle:
                json.dump(data, handle, indent=2)
            os.replace(tmp, self._dir / "status.json")
        except OSError:
            pass  # status file is best-effort; never kill a mission over it


def read_status(runtime_dir: str) -> Optional[dict]:
    path = Path(runtime_dir) / "status.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None
