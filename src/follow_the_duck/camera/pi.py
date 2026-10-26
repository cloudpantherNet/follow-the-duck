"""Raspberry Pi cameras.

Preferred: Picamera2 kept open for the whole mission (one persistent owner,
no per-frame process startup). Fallback: rpicam-still subprocess (slow,
~1s+/frame, but useful for `ftd doctor` and first bring-up).
Both import/spawn lazily so this module is importable off-Pi.
"""

from __future__ import annotations

import io
import subprocess
from typing import Optional

from ..config import Config
from .base import Camera, CameraError


class Picamera2Camera(Camera):
    def __init__(self, cfg: Config) -> None:
        try:
            from picamera2 import Picamera2  # system package on Pi OS
            from libcamera import Transform
        except ImportError as exc:  # pragma: no cover - Pi-only
            raise CameraError("picamera2 not available: %s" % exc)
        self._picam = Picamera2()
        config = self._picam.create_still_configuration(
            main={"size": (cfg.camera_width, cfg.camera_height)},
            transform=Transform(hflip=int(cfg.camera_hflip), vflip=int(cfg.camera_vflip)),
        )
        self._picam.configure(config)
        self._picam.options["quality"] = cfg.camera_jpeg_quality
        self._picam.start()

    def capture_jpeg(self) -> bytes:  # pragma: no cover - Pi-only
        buf = io.BytesIO()
        self._picam.capture_file(buf, format="jpeg")
        data = buf.getvalue()
        if not data:
            raise CameraError("picamera2 returned empty frame")
        return data

    def close(self) -> None:  # pragma: no cover - Pi-only
        try:
            self._picam.stop()
            self._picam.close()
        except Exception:
            pass


class RpicamStillCamera(Camera):
    """Shell out to rpicam-still per frame. Slow but dependency-free."""

    def __init__(self, cfg: Config, binary: str = "rpicam-still") -> None:
        self._cfg = cfg
        self._binary = binary

    def capture_jpeg(self) -> bytes:
        cmd = [
            self._binary, "--immediate", "--nopreview",
            "--width", str(self._cfg.camera_width),
            "--height", str(self._cfg.camera_height),
            "--quality", str(self._cfg.camera_jpeg_quality),
            "--timeout", "500", "--output", "-",
        ]
        if self._cfg.camera_hflip:
            cmd.append("--hflip")
        if self._cfg.camera_vflip:
            cmd.append("--vflip")
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=10)
        except FileNotFoundError:
            raise CameraError("%s not found" % self._binary)
        except subprocess.TimeoutExpired:
            raise CameraError("camera capture timed out")
        if proc.returncode != 0 or not proc.stdout:
            raise CameraError(
                "rpicam-still failed rc=%s: %s"
                % (proc.returncode, proc.stderr.decode(errors="replace")[-200:])
            )
        return proc.stdout


def make_camera(cfg: Config) -> Camera:
    """Pick a camera backend per configuration ('auto' tries picamera2 first,
    then rpicam-still; 'mock' always works)."""
    backend = cfg.camera_backend
    if backend == "mock":
        from .mock import SyntheticCamera

        return SyntheticCamera()
    if backend in ("auto", "picamera2"):
        try:
            return Picamera2Camera(cfg)
        except CameraError:
            if backend == "picamera2":
                raise
    if backend in ("auto", "rpicam"):
        import shutil

        if shutil.which("rpicam-still"):
            return RpicamStillCamera(cfg)
        if backend == "rpicam":
            raise CameraError("rpicam-still not found")
        raise CameraError(
            "no camera backend available (picamera2 missing, rpicam-still "
            "missing); use FTD_CAMERA_BACKEND=mock off the robot"
        )
    raise CameraError("unknown camera backend: %s" % backend)
