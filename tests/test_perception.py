from __future__ import annotations

import pytest

from follow_the_duck.clock import FakeClock
from follow_the_duck.config import Config
from follow_the_duck.models import Bearing, Frame, ObservationError, Presence, Proximity
from follow_the_duck.perception import Perceiver, build_questions
from follow_the_duck.providers.mock import ScriptedVision, clef_answers


def make_frame(pan=0.0):
    return Frame(frame_id=1, jpeg=b"jpg", captured_at=10.0, pan_deg=pan, tilt_deg=-10.0)


def perceive_with(answers, pan=0.0):
    clock = FakeClock()
    perceiver = Perceiver(ScriptedVision([answers]), Config(), clock)
    return perceiver.perceive(make_frame(pan), "yellow duck", "m1")


def test_questions_cover_schema_and_embed_target_as_data():
    questions = build_questions("yellow duck")
    assert set(questions) == {
        "present", "bearing", "proximity",
        "floor_left", "floor_center", "floor_right", "multiple",
    }
    for question in questions.values():
        assert question["type"] in ("noul", "choice")
        assert "instructions" in question
    assert questions["present"]["instructions"]["target_description"] == "yellow duck"
    assert set(questions["bearing"]["criteria"]) == {"left", "center", "right", "not_visible"}


def test_present_center_maps_to_observation():
    obs, latency = perceive_with(
        clef_answers(present=0.9, bearing="center", proximity="near",
                     floor_center=0.8, multiple=0.1)
    )
    assert obs.presence is Presence.PRESENT
    assert obs.bearing is Bearing.CENTER
    assert obs.proximity is Proximity.NEAR
    assert obs.confidence == pytest.approx(0.9)
    assert obs.floor_clear["center"] == pytest.approx(0.8)
    assert latency.total_s >= 0


def test_absent_clears_bearing_and_proximity():
    obs, _ = perceive_with(
        clef_answers(present=0.1, bearing="left", proximity="near")
    )
    assert obs.presence is Presence.ABSENT
    assert obs.bearing is Bearing.UNKNOWN
    assert obs.proximity is Proximity.UNKNOWN


def test_midband_probability_is_uncertain():
    obs, _ = perceive_with(clef_answers(present=0.5, bearing="center", proximity="mid"))
    assert obs.presence is Presence.UNCERTAIN


def test_pan_carried_into_observation():
    obs, _ = perceive_with(clef_answers(present=0.9, bearing="left", proximity="far"), pan=35.0)
    assert obs.pan_deg == pytest.approx(35.0)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda a: a.pop("present"),
        lambda a: a["present"].update(type="choice"),
        lambda a: a["present"].update(noul=1.7),
        lambda a: a["bearing"].update(choice="behind"),
        lambda a: a.pop("floor_center"),
        lambda a: a["multiple"].update(noul=float("nan")),
    ],
)
def test_malformed_answers_rejected(mutate):
    answers = clef_answers(present=0.9, bearing="center", proximity="mid")
    mutate(answers)
    with pytest.raises(ObservationError):
        perceive_with(answers)
