from __future__ import annotations

import pytest

from follow_the_duck.intent import CommandKind, parse_command


@pytest.mark.parametrize(
    "text,target",
    [
        ("Find the yellow duck", "yellow duck"),
        ("find a red cup.", "red cup"),
        ("Go to the green bottle!", "green bottle"),
        ("please look for the blue backpack", "blue backpack"),
        ("Locate an orange cone", "orange cone"),
        ("search for the white board eraser", "white board eraser"),
    ],
)
def test_find_commands(text, target):
    command = parse_command(text)
    assert command.kind == CommandKind.FIND
    assert command.target == target


@pytest.mark.parametrize("text", ["stop", "STOP!", "halt", "Emergency stop"])
def test_stop_commands(text):
    assert parse_command(text).kind == CommandKind.STOP


@pytest.mark.parametrize("text", ["cancel", "never mind", "abort"])
def test_cancel_commands(text):
    assert parse_command(text).kind == CommandKind.CANCEL


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "uh what",
        "the weather is nice",
        "find the",  # no target
        "find the one with the thing next to the other thing over there somewhere",  # too long
    ],
)
def test_uncertain_speech_starts_nothing(text):
    assert parse_command(text).kind == CommandKind.NONE


def test_target_changes_between_missions():
    assert parse_command("find the yellow duck").target == "yellow duck"
    assert parse_command("find the red cup").target == "red cup"
