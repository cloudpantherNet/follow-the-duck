"""Viewpoint ranking for the search phase.

Given the latest scan observations (one per camera pan angle), rank candidate
reposition directions by:
  + apparent navigable floor (clef probability — a visual hypothesis)
  + novelty vs. recently chosen directions (prefer new viewpoints)
  + potential visibility gain (directions where the target was 'uncertain')
  - uncertainty of the floor estimate
  - steering cost

Weights and tie-breaking are deterministic and configurable. Candidates whose
floor probability is below threshold are rejected *before* ranking. An AI
"open floor" answer is never treated as proof of clearance: the supervisor
still gates every pulse on the ultrasonic reading, and side clearance beyond
the fixed forward beam remains a documented blind spot (hence short pulses).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence

from .config import Config
from .models import Observation, Presence

#: Direction name -> (steering sign, which floor_clear key, which scan pan)
DIRECTIONS = ("left", "center", "right")


@dataclass(frozen=True)
class Candidate:
    direction: str          # 'left' | 'center' | 'right'
    steer_deg: float
    floor_p: float
    score: float
    feasible: bool
    reason: str


def _steer_for(direction: str, cfg: Config) -> float:
    if direction == "left":
        return -cfg.max_steer_deg
    if direction == "right":
        return cfg.max_steer_deg
    return 0.0


def _floor_probability(direction: str, observations: Sequence[Observation]) -> float:
    """Combine floor estimates for a chassis-relative direction across scan
    frames, conservatively (minimum of relevant estimates)."""
    relevant: List[float] = []
    for obs in observations:
        # A frame panned left mostly describes the left of the chassis, etc.
        if obs.pan_deg < -10:
            frame_dir = "left"
        elif obs.pan_deg > 10:
            frame_dir = "right"
        else:
            frame_dir = "center"
        if frame_dir == direction:
            # Center third of a panned frame is the best estimate for that side.
            value = obs.floor_clear.get("center")
        else:
            value = obs.floor_clear.get(direction) if frame_dir == "center" else None
        if value is not None:
            relevant.append(value)
    if not relevant:
        return 0.0  # no evidence means NOT navigable, never "probably fine"
    return min(relevant)


def _uncertain_target_bonus(direction: str, observations: Sequence[Observation]) -> float:
    for obs in observations:
        if obs.presence is Presence.UNCERTAIN:
            if (direction == "left" and obs.pan_deg < -10) or (
                direction == "right" and obs.pan_deg > 10
            ) or (direction == "center" and abs(obs.pan_deg) <= 10):
                return 1.0
    return 0.0


def rank_viewpoints(
    observations: Sequence[Observation],
    recent_directions: Sequence[str],
    cfg: Config,
) -> List[Candidate]:
    """Deterministic ranked list (best first). Infeasible candidates are
    included (feasible=False) for logging, sorted last."""
    history = list(recent_directions)
    candidates: List[Candidate] = []
    for direction in DIRECTIONS:
        floor_p = _floor_probability(direction, observations)
        uncertainty = 1.0 - abs(2.0 * floor_p - 1.0)
        visits = history.count(direction)
        novelty = 1.0 / (1.0 + visits)
        steer = _steer_for(direction, cfg)
        steer_cost = abs(steer) / max(1.0, cfg.max_steer_deg)
        score = (
            cfg.w_floor * floor_p
            - cfg.w_uncertainty * uncertainty
            + cfg.w_novelty * novelty
            + 0.4 * _uncertain_target_bonus(direction, observations)
            - cfg.w_steer_cost * steer_cost
        )
        feasible = floor_p >= cfg.floor_clear_threshold
        reason = (
            "floor=%.2f novelty=%.2f" % (floor_p, novelty)
            if feasible
            else "floor=%.2f below threshold %.2f" % (floor_p, cfg.floor_clear_threshold)
        )
        candidates.append(
            Candidate(direction, steer, floor_p, round(score, 6), feasible, reason)
        )
    # Deterministic order: feasible first, then score desc, smaller |steer|,
    # then name for a total order.
    candidates.sort(
        key=lambda c: (not c.feasible, -c.score, abs(c.steer_deg), c.direction)
    )
    return candidates


def best_feasible(candidates: Sequence[Candidate]) -> "Candidate | None":
    for candidate in candidates:
        if candidate.feasible:
            return candidate
    return None
