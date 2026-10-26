"""Turn transcribed speech (or typed text) into a mission command.

Transcripts are untrusted data: we only ever extract an enum command plus a
plain-text target description. Uncertain or empty speech yields NONE and must
not launch a mission.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class CommandKind(str, Enum):
    FIND = "find"
    STOP = "stop"
    CANCEL = "cancel"
    NONE = "none"


@dataclass(frozen=True)
class Command:
    kind: CommandKind
    target: str = ""


_STOP_RE = re.compile(r"^\W*(stop|halt|freeze|emergency stop)\W*$", re.IGNORECASE)
_CANCEL_RE = re.compile(r"^\W*(cancel|never ?mind|abort)\W*$", re.IGNORECASE)
_FIND_RE = re.compile(
    r"^\W*(?:please\s+)?(?:find|go to|go find|look for|locate|search for|"
    r"drive to|follow)\s+(?:the\s+|a\s+|an\s+)?(?P<target>.+?)[.!?\s]*$",
    re.IGNORECASE,
)

_MAX_TARGET_WORDS = 8


def parse_command(text: str) -> Command:
    text = (text or "").strip()
    if not text:
        return Command(CommandKind.NONE)
    if _STOP_RE.match(text):
        return Command(CommandKind.STOP)
    if _CANCEL_RE.match(text):
        return Command(CommandKind.CANCEL)
    match = _FIND_RE.match(text)
    if match:
        target = re.sub(r"\s+", " ", match.group("target")).strip()
        words = [w for w in target.split() if w.lower() not in ("the", "a", "an")]
        if not words or len(words) > _MAX_TARGET_WORDS:
            return Command(CommandKind.NONE)
        return Command(CommandKind.FIND, target=target)
    return Command(CommandKind.NONE)
