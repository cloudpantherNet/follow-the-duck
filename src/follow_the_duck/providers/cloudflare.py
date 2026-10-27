"""Cloudflare Workers AI providers.

API contracts verified 2026-10-26 against the published model schemas:

Vision — @cf/cloudflare/clef-flash
  POST {api_base}/accounts/{account}/ai/run/@cf/cloudflare/clef-flash
  Authorization: Bearer <token>
  Body: {"model": "clef-flash", "state": <str|obj>,
         "questions": {id: {type: noul|choice|score, instructions, criteria}},
         "images": [{"content_type": "image/jpeg", "base64": "..."}]}
  Limits: <=4 images, 4 MiB & 16 MP each, 8 MiB total decoded, 13 MiB body.
  Result: {"model": ..., "answers": {id: typed answer}, "usage": ...}
  Clef returns probabilities per typed question. It does NOT return bounding
  boxes, pixel coordinates, or calibrated depth — nothing here pretends it
  does.

Speech — @cf/openai/whisper-large-v3-turbo
  POST {api_base}/accounts/{account}/ai/run/@cf/openai/whisper-large-v3-turbo
  Body: {"audio": "<base64>", "task": "transcribe", "language": ...,
         "vad_filter": true, "initial_prompt": ...}
  Result: {"text": ..., "word_count": ..., "segments": [...],
           "transcription_info": {"language", "language_probability",
           "duration", ...}}

Both responses arrive wrapped in the standard Cloudflare envelope
{"result": ..., "success": bool, "errors": [...]}.
"""

from __future__ import annotations

import base64
import json
import re
import threading
import urllib.error
import urllib.request
from collections import deque
from typing import Deque, Dict, List, Optional

from ..config import Config
from .base import (
    ProviderError,
    ProviderTimeout,
    SpeechProvider,
    Transcription,
    VisionProvider,
)

_MAX_IMAGE_BYTES = 4 * 1024 * 1024
_MAX_BODY_BYTES = 13 * 1024 * 1024

#: Cloudflare account IDs are 32 lowercase hex characters.
_ACCOUNT_ID_RE = re.compile(r"[0-9a-f]{32}")


def check_credentials(account_id: str, api_token: str) -> None:
    """Fail fast, with a human fix, instead of a confusing HTTP 404 later."""
    if not account_id or not api_token:
        raise ProviderError(
            "CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN not configured"
        )
    if not _ACCOUNT_ID_RE.fullmatch(account_id):
        hint = ""
        if account_id.startswith(("cfut_", "cfapi_")) or len(account_id) > 32:
            hint = " (this looks like an API token — that goes in CLOUDFLARE_API_TOKEN)"
        raise ProviderError(
            "CLOUDFLARE_ACCOUNT_ID %r is not a Cloudflare account ID%s. "
            "Expected 32 hex characters — find it in the dashboard sidebar "
            "under Workers & Pages, or in your dashboard URL."
            % (account_id[:12] + "...", hint)
        )


class RateLimiter:
    """Simple sliding-window limiter: at most `per_minute` acquisitions per
    60s. Raises instead of sleeping — the mission loop decides what to do."""

    def __init__(self, per_minute: int, now_fn) -> None:
        self._per_minute = max(1, per_minute)
        self._now = now_fn
        self._stamps: Deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = self._now()
            while self._stamps and now - self._stamps[0] > 60.0:
                self._stamps.popleft()
            if len(self._stamps) >= self._per_minute:
                raise ProviderError(
                    "request budget exhausted (%d/min)" % self._per_minute
                )
            self._stamps.append(now)


def _post_json(url: str, token: str, payload: dict, timeout_s: float) -> dict:
    body = json.dumps(payload).encode("utf-8")
    if len(body) > _MAX_BODY_BYTES:
        raise ProviderError("request body exceeds 13 MiB limit")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        raise ProviderError("HTTP %s from provider: %s" % (exc.code, detail))
    except urllib.error.URLError as exc:
        reason = str(getattr(exc, "reason", exc))
        if "timed out" in reason.lower():
            raise ProviderTimeout("provider request timed out")
        raise ProviderError("network error: %s" % reason)
    except TimeoutError:
        raise ProviderTimeout("provider request timed out")
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProviderError("non-JSON provider response: %s" % exc)
    if not isinstance(envelope, dict) or not envelope.get("success", False):
        errors = envelope.get("errors") if isinstance(envelope, dict) else None
        raise ProviderError("provider reported failure: %s" % (errors,))
    result = envelope.get("result")
    if not isinstance(result, dict):
        raise ProviderError("missing result object in provider response")
    return result


def build_clef_payload(
    images: List[bytes], state: object, questions: Dict[str, dict]
) -> dict:
    """Build the documented clef-flash request body (exported for tests)."""
    if not questions or len(questions) > 64:
        raise ProviderError("clef requires 1..64 questions")
    if len(images) > 4:
        raise ProviderError("clef accepts at most 4 images")
    encoded = []
    for jpeg in images:
        if len(jpeg) > _MAX_IMAGE_BYTES:
            raise ProviderError("image exceeds 4 MiB clef limit")
        encoded.append(
            {
                "content_type": "image/jpeg",
                "base64": base64.b64encode(jpeg).decode("ascii"),
            }
        )
    payload: dict = {
        "model": "clef-flash",
        "state": state,
        "questions": questions,
    }
    if encoded:
        payload["images"] = encoded
    return payload


class CloudflareVision(VisionProvider):
    def __init__(self, cfg: Config, now_fn=None) -> None:
        check_credentials(cfg.cloudflare_account_id, cfg.cloudflare_api_token)
        self._cfg = cfg
        import time as _time

        self._limiter = RateLimiter(
            cfg.max_vision_calls_per_minute, now_fn or _time.monotonic
        )

    def _url(self) -> str:
        return "%s/accounts/%s/ai/run/%s" % (
            self._cfg.api_base,
            self._cfg.cloudflare_account_id,
            self._cfg.vision_model,
        )

    def ask(
        self, images: List[bytes], state: object, questions: Dict[str, dict]
    ) -> Dict[str, dict]:
        self._limiter.acquire()
        payload = build_clef_payload(images, state, questions)
        result = _post_json(
            self._url(), self._cfg.cloudflare_api_token, payload,
            self._cfg.http_timeout_s,
        )
        answers = result.get("answers")
        if not isinstance(answers, dict):
            raise ProviderError("clef response missing 'answers'")
        return answers


class CloudflareSpeech(SpeechProvider):
    def __init__(self, cfg: Config) -> None:
        check_credentials(cfg.cloudflare_account_id, cfg.cloudflare_api_token)
        self._cfg = cfg

    def _url(self) -> str:
        return "%s/accounts/%s/ai/run/%s" % (
            self._cfg.api_base,
            self._cfg.cloudflare_account_id,
            self._cfg.stt_model,
        )

    def transcribe(self, wav_bytes: bytes, language: str = "en") -> Transcription:
        if not wav_bytes:
            raise ProviderError("empty audio")
        payload: dict = {
            "audio": base64.b64encode(wav_bytes).decode("ascii"),
            "task": "transcribe",
            "vad_filter": True,
            # Hint the expected vocabulary; this biases decoding, it is not a
            # command channel.
            "initial_prompt": "Short robot command, e.g. find the yellow duck.",
        }
        if language:
            payload["language"] = language
        result = _post_json(
            self._url(), self._cfg.cloudflare_api_token, payload,
            self._cfg.http_timeout_s,
        )
        text = result.get("text")
        if not isinstance(text, str):
            raise ProviderError("whisper response missing 'text'")
        info = result.get("transcription_info") or {}
        return Transcription(
            text=text.strip(),
            language=str(info.get("language", "")),
            duration_s=float(info.get("duration", 0.0) or 0.0),
        )
