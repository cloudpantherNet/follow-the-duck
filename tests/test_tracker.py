from __future__ import annotations

from follow_the_duck.config import Config
from follow_the_duck.models import validate_observation
from follow_the_duck.tracker import TargetTracker

from test_models import raw_obs


def obs(presence="present", bearing="center", ambiguity=0.0, frame_id=1):
    return validate_observation(
        raw_obs(
            frame_id=frame_id,
            presence=presence,
            bearing=bearing if presence == "present" else "unknown",
            proximity="mid" if presence == "present" else "unknown",
            ambiguity=ambiguity,
        )
    )


def test_acquire_after_consecutive_confirmations():
    tracker = TargetTracker(Config())
    assert tracker.update(obs()) == "hit"
    assert not tracker.acquired
    assert tracker.update(obs(frame_id=2)) == "acquired"
    assert tracker.acquired


def test_absent_resets_streak():
    tracker = TargetTracker(Config())
    tracker.update(obs())
    assert tracker.update(obs(presence="absent", frame_id=2)) == "miss"
    assert tracker.hits == 0


def test_lost_after_two_misses_when_acquired():
    tracker = TargetTracker(Config())
    tracker.update(obs())
    tracker.update(obs(frame_id=2))
    assert tracker.acquired
    assert tracker.update(obs(presence="absent", frame_id=3)) == "miss"
    assert tracker.update(obs(presence="absent", frame_id=4)) == "lost"
    assert not tracker.acquired


def test_identity_jump_with_ambiguity_is_suspect_not_retarget():
    """Locked on a duck at the left; a sudden 'match' far right while the
    model reports multiple matches must not steal the lock."""
    tracker = TargetTracker(Config())
    tracker.update(obs(bearing="left"))
    tracker.update(obs(bearing="left", frame_id=2))
    assert tracker.acquired
    verdict = tracker.update(obs(bearing="right", ambiguity=0.9, frame_id=3))
    assert verdict == "suspect"
    assert tracker.last_good.bearing.value == "left"


def test_unambiguous_bearing_change_is_followed():
    tracker = TargetTracker(Config())
    tracker.update(obs(bearing="left"))
    tracker.update(obs(bearing="left", frame_id=2))
    verdict = tracker.update(obs(bearing="center", ambiguity=0.0, frame_id=3))
    assert verdict == "hit"
    assert tracker.last_good.bearing.value == "center"


def test_uncertain_decays_without_clearing():
    tracker = TargetTracker(Config())
    tracker.update(obs())
    assert tracker.update(obs(presence="uncertain", frame_id=2)) == "uncertain"
    assert tracker.hits == 0
    assert tracker.misses == 0
