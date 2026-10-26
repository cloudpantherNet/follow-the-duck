"""Monotonic clock abstraction.

Every timestamp in this project (command expiry, heartbeats, observation
staleness) uses a monotonic clock, never wall time. Tests inject FakeClock so
missions run deterministically and instantly.
"""

from __future__ import annotations

import threading
import time


class Clock:
    """Real monotonic clock."""

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class FakeClock(Clock):
    """Deterministic clock for tests: sleep() advances time instantly."""

    def __init__(self, start: float = 1000.0) -> None:
        self._now = start
        self._lock = threading.Lock()

    def now(self) -> float:
        with self._lock:
            return self._now

    def sleep(self, seconds: float) -> None:
        self.advance(max(0.0, seconds))

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._now += seconds
