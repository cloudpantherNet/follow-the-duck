"""Camera interface. Exactly one Camera instance owns the device."""

from __future__ import annotations

import abc


class CameraError(RuntimeError):
    pass


class Camera(abc.ABC):
    @abc.abstractmethod
    def capture_jpeg(self) -> bytes:
        """Capture one frame and return JPEG bytes. Raises CameraError."""

    def close(self) -> None:  # pragma: no cover - trivial default
        pass
