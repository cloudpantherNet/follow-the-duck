from __future__ import annotations

from follow_the_duck.config import Config
from follow_the_duck.models import validate_observation
from follow_the_duck.planner import best_feasible, rank_viewpoints

from test_models import raw_obs


def scan_obs(pan, floor_left=0.5, floor_center=0.5, floor_right=0.5, presence="absent"):
    return validate_observation(
        raw_obs(
            pan_deg=pan,
            presence=presence,
            bearing="unknown" if presence == "absent" else "center",
            proximity="unknown" if presence == "absent" else "mid",
            floor_clear={"left": floor_left, "center": floor_center, "right": floor_right},
        )
    )


def three_pan_scan(left_open, center_open, right_open):
    """One scan: frames at pan -35/0/+35 with given floor probabilities."""
    return [
        scan_obs(-35, floor_center=left_open),
        scan_obs(0, floor_left=left_open, floor_center=center_open, floor_right=right_open),
        scan_obs(35, floor_center=right_open),
    ]


def test_prefers_open_left_over_blocked_right():
    cfg = Config()
    ranked = rank_viewpoints(three_pan_scan(0.9, 0.1, 0.1), [], cfg)
    choice = best_feasible(ranked)
    assert choice is not None
    assert choice.direction == "left"
    assert choice.steer_deg < 0


def test_prefers_open_right_over_blocked_left():
    cfg = Config()
    choice = best_feasible(rank_viewpoints(three_pan_scan(0.1, 0.1, 0.9), [], cfg))
    assert choice.direction == "right"
    assert choice.steer_deg > 0


def test_center_wins_when_all_equal():
    cfg = Config()
    choice = best_feasible(rank_viewpoints(three_pan_scan(0.9, 0.9, 0.9), [], cfg))
    assert choice.direction == "center"  # lower steering cost breaks the tie


def test_no_feasible_direction_when_all_blocked():
    cfg = Config()
    assert best_feasible(rank_viewpoints(three_pan_scan(0.2, 0.2, 0.2), [], cfg)) is None


def test_recently_visited_direction_deprioritized():
    cfg = Config()
    scan = three_pan_scan(0.9, 0.85, 0.9)
    first = best_feasible(rank_viewpoints(scan, [], cfg))
    repeat = best_feasible(rank_viewpoints(scan, [first.direction] * 4, cfg))
    assert repeat.direction != first.direction


def test_missing_floor_evidence_is_not_navigable():
    cfg = Config()
    # Only a center frame, with no estimate for 'left'.
    obs = scan_obs(0, floor_left=0.0, floor_center=0.9, floor_right=0.0)
    ranked = rank_viewpoints([obs], [], cfg)
    by_dir = {c.direction: c for c in ranked}
    assert not by_dir["left"].feasible
    assert not by_dir["right"].feasible
    assert by_dir["center"].feasible


def test_ranking_is_deterministic():
    cfg = Config()
    scan = three_pan_scan(0.8, 0.7, 0.8)
    first = rank_viewpoints(scan, ["left"], cfg)
    second = rank_viewpoints(scan, ["left"], cfg)
    assert [c.direction for c in first] == [c.direction for c in second]
    assert [c.score for c in first] == [c.score for c in second]
