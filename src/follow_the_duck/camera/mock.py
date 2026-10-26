"""Mock cameras: a synthetic JPEG generator and a fixture-replay camera."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from .base import Camera, CameraError

# A minimal valid 1x1 JPEG (baseline, grayscale) so tests and dry-runs have
# real JPEG bytes without any imaging dependency.
_TINY_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010101004800480000ffdb00430003020202020203"
    "0202020303030304060404040404080606050609080a0a090809090a0c0f0c0a"
    "0b0e0b09090d110d0e0f101011100a0c12131210130f101010ffc9000b080001"
    "000101011100ffcc000600101005ffda0008010100003f00d2cf20ffd9"
)


class SyntheticCamera(Camera):
    """Always returns a tiny valid JPEG; optionally fails on request."""

    def __init__(self) -> None:
        self.captures = 0
        self.fail_next = False

    def capture_jpeg(self) -> bytes:
        if self.fail_next:
            self.fail_next = False
            raise CameraError("injected capture failure")
        self.captures += 1
        return _TINY_JPEG


class ReplayCamera(Camera):
    """Replays JPEG files from a directory in sorted order (looping on the
    last frame), for `ftd replay` against previously captured footage."""

    def __init__(self, fixtures_dir: Path) -> None:
        self._files: List[Path] = sorted(
            p for p in Path(fixtures_dir).glob("*.jpg") if p.is_file()
        )
        if not self._files:
            raise CameraError("no .jpg fixtures in %s" % fixtures_dir)
        self._index = 0

    def capture_jpeg(self) -> bytes:
        path = self._files[min(self._index, len(self._files) - 1)]
        self._index += 1
        return path.read_bytes()
