"""Target identity tracking across observations.

Acquisition needs several consecutive confirmations; once locked, the tracker
refuses to silently jump to a different plausible match (e.g. from the duck to
a yellow chair): a sudden full left<->right bearing jump while the model also
reports multiple matching objects is treated as a suspect sighting, not a
retarget. Loss requires several consecutive misses, after which the mission
rescans rather than driving on toward a stale location.
"""

from __future__ import annotations

from typing import Optional

from .config import Config
from .models import Bearing, Observation, Presence

_BEARING_STEP = {Bearing.LEFT: -1, Bearing.CENTER: 0, Bearing.RIGHT: 1}


class TargetTracker:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self.hits = 0
        self.misses = 0
        self.acquired = False
        self.last_good: Optional[Observation] = None
        self.ambiguous_seen = False

    def update(self, obs: Observation) -> str:
        """Feed one validated observation. Returns one of:
        'hit', 'acquired', 'miss', 'lost', 'suspect', 'uncertain'."""
        if obs.ambiguity >= self._cfg.ambiguity_threshold:
            self.ambiguous_seen = True

        if obs.presence is Presence.PRESENT:
            if self.acquired and self._is_identity_jump(obs):
                # Plausible different object; keep the locked candidate.
                return "suspect"
            self.hits += 1
            self.misses = 0
            self.last_good = obs
            if not self.acquired and self.hits >= self._cfg.confirmations_to_acquire:
                self.acquired = True
                return "acquired"
            return "hit"

        if obs.presence is Presence.ABSENT:
            self.hits = 0
            self.misses += 1
            if self.acquired and self.misses >= 2:
                self.acquired = False
                return "lost"
            return "miss"

        # UNCERTAIN: neither confirms nor clears; decay the hit streak.
        self.hits = max(0, self.hits - 1)
        return "uncertain"

    def _is_identity_jump(self, obs: Observation) -> bool:
        if self.last_good is None:
            return False
        prev = _BEARING_STEP.get(self.last_good.bearing)
        new = _BEARING_STEP.get(obs.bearing)
        if prev is None or new is None:
            return False
        return abs(new - prev) >= 2 and obs.ambiguity >= self._cfg.ambiguity_threshold

    def reset(self) -> None:
        self.hits = 0
        self.misses = 0
        self.acquired = False
        self.last_good = None
