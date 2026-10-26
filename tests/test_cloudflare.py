"""Payload/contract tests for the Cloudflare providers (no network)."""

from __future__ import annotations

import base64
import json

import pytest

from follow_the_duck.clock import FakeClock
from follow_the_duck.config import Config
from follow_the_duck.providers import cloudflare as cf
from follow_the_duck.providers.base import ProviderError


def creds_cfg() -> Config:
    cfg = Config()
    cfg.cloudflare_account_id = "acct"
    cfg.cloudflare_api_token = "token"
    return cfg


def test_clef_payload_matches_documented_schema():
    payload = cf.build_clef_payload(
        [b"jpegbytes"],
        {"target_description": "yellow duck"},
        {"present": {"type": "noul", "instructions": "visible?"}},
    )
    assert payload["model"] == "clef-flash"
    assert payload["state"]["target_description"] == "yellow duck"
    assert list(payload["questions"]) == ["present"]
    image = payload["images"][0]
    assert image["content_type"] == "image/jpeg"
    assert base64.b64decode(image["base64"]) == b"jpegbytes"
    json.dumps(payload)  # must be JSON-serializable


def test_clef_payload_limits():
    question = {"q": {"type": "noul", "instructions": "x"}}
    with pytest.raises(ProviderError):
        cf.build_clef_payload([b"x"] * 5, {}, question)  # >4 images
    with pytest.raises(ProviderError):
        cf.build_clef_payload([b"x" * (4 * 1024 * 1024 + 1)], {}, question)
    with pytest.raises(ProviderError):
        cf.build_clef_payload([b"x"], {}, {})  # no questions


def test_vision_requires_credentials():
    with pytest.raises(ProviderError):
        cf.CloudflareVision(Config())
    with pytest.raises(ProviderError):
        cf.CloudflareSpeech(Config())


def test_vision_url_and_answers(monkeypatch):
    captured = {}

    def fake_post(url, token, payload, timeout_s):
        captured.update(url=url, token=token, payload=payload)
        return {"answers": {"present": {"type": "noul", "noul": 0.9}}}

    monkeypatch.setattr(cf, "_post_json", fake_post)
    vision = cf.CloudflareVision(creds_cfg(), now_fn=FakeClock().now)
    answers = vision.ask(
        [b"img"], {"s": 1}, {"present": {"type": "noul", "instructions": "?"}}
    )
    assert answers["present"]["noul"] == 0.9
    assert captured["url"] == (
        "https://api.cloudflare.com/client/v4/accounts/acct/ai/run/"
        "@cf/cloudflare/clef-flash"
    )
    assert captured["token"] == "token"


def test_vision_rejects_missing_answers(monkeypatch):
    monkeypatch.setattr(cf, "_post_json", lambda *a, **k: {"nope": 1})
    vision = cf.CloudflareVision(creds_cfg(), now_fn=FakeClock().now)
    with pytest.raises(ProviderError):
        vision.ask([b"i"], {}, {"q": {"type": "noul", "instructions": "?"}})


def test_speech_payload_and_response(monkeypatch):
    captured = {}

    def fake_post(url, token, payload, timeout_s):
        captured.update(url=url, payload=payload)
        return {
            "text": "  find the yellow duck ",
            "word_count": 4,
            "transcription_info": {"language": "en", "duration": 3.1},
        }

    monkeypatch.setattr(cf, "_post_json", fake_post)
    speech = cf.CloudflareSpeech(creds_cfg())
    result = speech.transcribe(b"WAVDATA", "en")
    assert result.text == "find the yellow duck"
    assert result.duration_s == pytest.approx(3.1)
    payload = captured["payload"]
    assert base64.b64decode(payload["audio"]) == b"WAVDATA"
    assert payload["task"] == "transcribe"
    assert payload["language"] == "en"
    assert captured["url"].endswith("/ai/run/@cf/openai/whisper-large-v3-turbo")


def test_speech_rejects_empty_audio():
    with pytest.raises(ProviderError):
        cf.CloudflareSpeech(creds_cfg()).transcribe(b"")


def test_rate_limiter_budget():
    clock = FakeClock()
    limiter = cf.RateLimiter(2, clock.now)
    limiter.acquire()
    limiter.acquire()
    with pytest.raises(ProviderError):
        limiter.acquire()
    clock.advance(61)
    limiter.acquire()  # window rolled over
