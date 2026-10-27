"""Telemetry: mission event emission + the non-blocking upload client."""

from __future__ import annotations

import base64
import json
import time

import pytest

from follow_the_duck.models import MissionState
from follow_the_duck.telemetry import ListSink, TelemetryClient

from test_mission import absent, present, shrinking_sonar


# ----------------------------------------------------- mission event stream


def test_mission_emits_full_event_stream(harness_factory):
    script = [present(), present(), present(proximity="near"),
              present(proximity="very_close")]
    h = harness_factory(script)
    h.sensor.set_script(shrinking_sonar(h.chassis))
    sink = ListSink()
    h.mission._sink = sink
    status = h.mission.run("yellow duck")
    assert status.state == MissionState.ARRIVED

    starts = sink.of_kind("mission_start")
    assert len(starts) == 1 and starts[0]["target"] == "yellow duck"

    observations = sink.of_kind("observation")
    assert observations and observations[0]["presence"] == "present"
    assert observations[0]["jpeg"].startswith(b"\xff\xd8")  # frame attached
    assert "floor_clear" in observations[0]
    assert all(o["mission_id"] == status.mission_id for o in observations)

    moves = sink.of_kind("move")
    assert moves and all(m["ok"] for m in moves)

    ends = sink.of_kind("mission_end")
    assert len(ends) == 1 and ends[0]["state"] == "ARRIVED"


def test_search_mission_emits_ranked_decisions(harness_factory):
    h = harness_factory([absent(floor_left=0.9, floor_center=0.15, floor_right=0.15),
                         absent(floor_center=0.9), absent(floor_center=0.15)])
    h.cfg.max_reposition_steps = 1
    sink = ListSink()
    h.mission._sink = sink
    h.mission.run("duck")
    decisions = sink.of_kind("decision")
    assert decisions
    first = decisions[0]
    assert first["chosen"] == "left"
    assert {c["direction"] for c in first["candidates"]} == {"left", "center", "right"}


def test_sink_errors_never_break_missions(harness_factory):
    class ExplodingSink(ListSink):
        def emit(self, event):
            raise RuntimeError("boom")

    h = harness_factory([absent()])
    h.mission._sink = ExplodingSink()
    status = h.mission.run("duck")  # must not raise
    assert status.state == MissionState.BLOCKED


# ----------------------------------------------------------- upload client


def make_client(monkeypatch, posts, fail=False, **kw):
    client = TelemetryClient("https://example.test", "secret",
                             flush_interval_s=0.02, **kw)

    def fake_post(batch):
        if fail:
            client.failed += len(batch)
            return
        posts.append(batch)
        client.sent += len(batch)

    monkeypatch.setattr(client, "_post_batch", fake_post)
    return client


def test_client_batches_and_encodes_images(monkeypatch):
    posts = []
    client = make_client(monkeypatch, posts)
    client.emit({"kind": "observation", "mission_id": "m1", "t": 1.0,
                 "jpeg": b"JPEGBYTES"})
    client.emit({"kind": "move", "mission_id": "m1", "t": 2.0})
    client.close()
    flat = [e for batch in posts for e in batch]
    assert len(flat) == 2
    obs = flat[0]
    assert "jpeg" not in obs  # raw bytes never serialized
    assert base64.b64decode(obs["image_b64"]) == b"JPEGBYTES"
    json.dumps(flat)  # whole payload must be JSON-safe


def test_client_can_strip_images(monkeypatch):
    posts = []
    client = make_client(monkeypatch, posts, send_images=False)
    client.emit({"kind": "observation", "mission_id": "m1", "t": 1.0,
                 "jpeg": b"JPEGBYTES"})
    client.close()
    event = posts[0][0]
    assert "image_b64" not in event and "jpeg" not in event


def test_full_queue_drops_instead_of_blocking(monkeypatch):
    posts = []
    client = make_client(monkeypatch, posts, max_queue=2)
    # Stall the worker so the queue can actually fill.
    monkeypatch.setattr(client, "_post_batch", lambda b: time.sleep(0.05))
    started = time.monotonic()
    for i in range(50):
        client.emit({"kind": "x", "mission_id": "m1", "t": float(i)})
    elapsed = time.monotonic() - started
    assert elapsed < 0.5  # emit never blocked on the slow backend
    assert client.dropped > 0
    client.close()


def test_real_post_failure_is_contained():
    client = TelemetryClient("https://127.0.0.1:1/nothing-here", "secret",
                             flush_interval_s=0.02, timeout_s=0.2)
    client.emit({"kind": "x", "mission_id": "m1", "t": 1.0})
    client.close()
    assert client.failed >= 1
    assert client.sent == 0
