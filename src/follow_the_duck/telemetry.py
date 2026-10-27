"""Mission telemetry: stream what the robot saw and decided to a dashboard.

Educational goal: students can watch every camera frame, the AI's typed
answers, the ranked movement candidates, and each motor decision on a web
page (see deploy/worker/ for the Cloudflare Worker backend + UI).

Design rules:
* Telemetry must NEVER affect the mission: events go into a bounded queue
  consumed by a background thread; a full queue or a dead backend silently
  drops events (counted, logged once) — missions keep running.
* Disabled by default (FTD_TELEMETRY_URL unset). Frames are only uploaded
  when FTD_TELEMETRY_SEND_IMAGES is true.
* The token is sent as a Bearer header and never logged.
"""

from __future__ import annotations

import base64
import json
import logging
import queue
import threading
import urllib.error
import urllib.request
from typing import Dict, List, Optional

log = logging.getLogger("ftd.telemetry")


class EventSink:
    """Interface. emit() must be cheap and must never raise."""

    def emit(self, event: Dict[str, object]) -> None:  # pragma: no cover
        pass

    def close(self) -> None:  # pragma: no cover - trivial default
        pass


class ListSink(EventSink):
    """Collects events in memory (tests, replay inspection)."""

    def __init__(self) -> None:
        self.events: List[Dict[str, object]] = []

    def emit(self, event: Dict[str, object]) -> None:
        self.events.append(event)

    def of_kind(self, kind: str) -> List[Dict[str, object]]:
        return [e for e in self.events if e.get("kind") == kind]


class TelemetryClient(EventSink):
    """Posts event batches to the dashboard Worker from a daemon thread."""

    def __init__(
        self,
        url: str,
        token: str,
        send_images: bool = True,
        max_queue: int = 256,
        batch_size: int = 10,
        flush_interval_s: float = 1.0,
        timeout_s: float = 5.0,
    ) -> None:
        self._url = url.rstrip("/") + "/api/events"
        self._token = token
        self._send_images = send_images
        self._batch_size = batch_size
        self._flush_interval_s = flush_interval_s
        self._timeout_s = timeout_s
        self._queue: "queue.Queue[Optional[dict]]" = queue.Queue(maxsize=max_queue)
        self.dropped = 0
        self.failed = 0
        self.sent = 0
        self._warned = False
        self._thread = threading.Thread(
            target=self._run, name="ftd-telemetry", daemon=True
        )
        self._thread.start()

    # ------------------------------------------------------------- EventSink

    def emit(self, event: Dict[str, object]) -> None:
        try:
            event = dict(event)
            jpeg = event.pop("jpeg", None)
            if jpeg is not None and self._send_images and isinstance(jpeg, bytes):
                event["image_b64"] = base64.b64encode(jpeg).decode("ascii")
            self._queue.put_nowait(event)
        except queue.Full:
            self.dropped += 1
        except Exception:
            self.dropped += 1

    def close(self) -> None:
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout=self._timeout_s + 2)

    # ------------------------------------------------------------- internals

    def _run(self) -> None:
        finished = False
        while not finished:
            batch: List[dict] = []
            try:
                item = self._queue.get(timeout=self._flush_interval_s)
                if item is None:
                    finished = True
                else:
                    batch.append(item)
            except queue.Empty:
                continue
            while len(batch) < self._batch_size:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    finished = True
                    break
                batch.append(item)
            if batch:
                self._post_batch(batch)

    def _post_batch(self, batch: List[dict]) -> None:
        try:
            body = json.dumps({"events": batch}).encode("utf-8")
            request = urllib.request.Request(
                self._url,
                data=body,
                headers={
                    "Authorization": "Bearer " + self._token,
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=self._timeout_s) as resp:
                resp.read()
            self.sent += len(batch)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self.failed += len(batch)
            if not self._warned:
                self._warned = True
                log.warning(
                    "telemetry upload failing (%s) — missions continue, "
                    "events are dropped", exc,
                )
