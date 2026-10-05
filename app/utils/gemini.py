"""
Thin async client for the Google AI Studio (Gemini) API.

The AI coach (app/routes/coach.py) uses this to turn a user's training history
into a structured workout routine. Unlike the old on-device Ollama backend, the
prompt (training history, set notes, journal wellness, injury flags) is sent to
Google. On the free AI Studio tier Google may use submitted content to improve
its products; the paid tier does not.

Configuration (env vars):
  GEMINI_API_KEY   AI Studio API key (required) — https://aistudio.google.com/apikey
  GEMINI_MODEL     model id (default gemini-3.5-flash-lite)
  GEMINI_CHAT_MODEL  optional model for the coach chat (the chat route reads it and
                     passes model=; everything else uses GEMINI_MODEL)

Transient failures (429 / 5xx / connection errors / timeouts) are retried with
a short backoff. Everything else surfaces as a GeminiError whose message is safe
to show in the UI — callers render it instead of a 500. The key travels in the
x-goog-api-key header, never in the URL.
"""

import asyncio
import json
import logging
import os
import time

import httpx

DEFAULT_MODEL = "gemini-3.5-flash-lite"
BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = (2.0, 5.0)
# Long enough for a 7-day plan; short enough that a truncated (MAX_TOKENS) reply
# isn't a realistic failure mode.
_MAX_OUTPUT_TOKENS = 8192
# Streaming reports progress as an estimated token count (~4 chars/token), in
# steps of this size, to match the "(N tokens)" label in the plan UI.
_PROGRESS_STEP = 15


def api_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "").strip()


def model() -> str:
    return os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_MODEL


_env_model = model  # chat_turn_json's `model=` parameter shadows the name inside it


def chat_model() -> str:
    """Model for the coach chat: GEMINI_CHAT_MODEL when set, else GEMINI_MODEL."""
    return os.environ.get("GEMINI_CHAT_MODEL", "").strip() or model()


def is_configured() -> bool:
    """True when an API key is set. Deliberately makes no network call — a ping
    on every page load would spend quota and add latency for no real signal."""
    return bool(api_key())


# Models that answered 400 to a request carrying thinkingConfig and then
# succeeded without it. The known ones (verified live) are pre-seeded so even the
# first request skips the wasted call; unknown models are discovered at runtime
# by chat_turn_json()'s fallback and remembered per process — a real saving on
# small free-tier daily quotas.
_KNOWN_NO_THINKING = frozenset({"gemini-3.5-flash-lite"})
_NO_THINKING_CFG: set[str] = set(_KNOWN_NO_THINKING)

KINDS = (
    "not_configured", "auth", "quota", "rate_limited", "timeout",
    "unreachable", "blocked", "empty", "malformed", "bad_request",
)


class GeminiError(RuntimeError):
    """Raised when the Gemini API is unreachable, rejects the request, or
    returns unusable output.

    `kind` (one of KINDS) is what callers branch on to pick a status code and
    user-facing copy; the message is also safe to show. `retryable` marks
    failures worth another attempt; `status` is set only for an
    otherwise-unclassified HTTP error (e.g. a 400 whose cause Google doesn't
    spell out)."""

    def __init__(self, message: str, *, kind: str = "bad_request",
                 retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.status = status


def _http_error(status: int, body: str, model_name: str) -> GeminiError:
    if status in (401, 403) or (status == 400 and "api key" in body.lower()):
        return GeminiError("Gemini rejected the API key. Check GEMINI_API_KEY.", kind="auth")
    if status == 404:
        # Google 404s both for unknown ids and for models retired to new users
        # (e.g. gemini-2.5-flash) — its own message says which, so pass it on.
        try:
            reason = json.loads(body)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            reason = "wasn't found"
        return GeminiError(f"Gemini model '{model_name}' isn't usable: {reason[:200]} Check GEMINI_MODEL.")
    if status == 429:
        # A per-day quota will not clear by retrying in a few seconds, and each
        # retry would only spend more of it; a per-minute limit will.
        squashed = body.lower().replace(" ", "").replace("_", "")
        if "perday" in squashed:
            return GeminiError("Gemini's daily quota is used up. Try again tomorrow.", kind="quota")
        return GeminiError(
            "Gemini rate limit or quota reached. Try again in a minute.",
            kind="rate_limited", retryable=True,
        )
    if status in (500, 502, 503, 504):
        return GeminiError("Gemini is temporarily unavailable.", kind="unreachable", retryable=True)
    return GeminiError(f"Gemini returned HTTP {status}: {body[:200]}", status=status)


def _extract_text(data: dict) -> str:
    """Concatenate the answer text of the first candidate (thought parts skipped).
    Raises if the prompt was blocked; returns '' when there's nothing usable."""
    block = (data.get("promptFeedback") or {}).get("blockReason")
    if block:
        raise GeminiError(f"Gemini blocked the request ({block}).", kind="blocked")
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def user_turn(text: str) -> dict:
    return {"role": "user", "parts": [{"text": text}]}


def model_turn(text: str) -> dict:
    return {"role": "model", "parts": [{"text": text}]}


def _build_payload(system: str, contents: list, schema: dict, temperature: float, model_name: str) -> dict:
    config: dict = {
        "temperature": temperature,
        "responseMimeType": "application/json",
        "responseJsonSchema": schema,
        "maxOutputTokens": _MAX_OUTPUT_TOKENS,
    }
    # Hidden reasoning only costs latency here — every caller wants a fast,
    # structured answer. 2.5/3.1/3.8 Flash accept a zero budget (on 3.8 Flash
    # the default spends ~150 thought tokens on a trivial prompt); Pro models
    # can't turn thinking off, and some Flash models (3.5 Flash-Lite) reject the
    # field outright — chat_turn_json() handles that by retrying without it.
    if "flash" in model_name and model_name not in _NO_THINKING_CFG:
        config["thinkingConfig"] = {"thinkingBudget": 0}
    return {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": config,
    }


async def _generate_once(payload: dict, headers: dict, timeout: float, model_name: str) -> str:
    url = f"{BASE_URL}/models/{model_name}:generateContent"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload, headers=headers)
    if resp.status_code != 200:
        raise _http_error(resp.status_code, resp.text, model_name)
    return _extract_text(resp.json())


async def _stream_once(payload: dict, headers: dict, timeout: float, on_tokens, model_name: str) -> str:
    url = f"{BASE_URL}/models/{model_name}:streamGenerateContent?alt=sse"
    parts: list[str] = []
    chars = 0
    reported = 0
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise _http_error(resp.status_code, body.decode(errors="replace"), model_name)
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    chunk = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                text = _extract_text(chunk)
                if not text:
                    continue
                parts.append(text)
                chars += len(text)
                est_tokens = chars // 4
                if est_tokens - reported >= _PROGRESS_STEP:
                    reported = est_tokens
                    await on_tokens(est_tokens)
    return "".join(parts)


async def chat_turn_json(
    system: str,
    contents: list,
    schema: dict,
    *,
    temperature: float = 0.4,
    timeout: float = 120.0,
    on_tokens: "object | None" = None,  # async callable(count: int) → None; enables streaming
    model: str | None = None,           # default: GEMINI_MODEL
    deadline: float | None = None,      # absolute time.monotonic() by which the whole call must end
    on_request: "object | None" = None, # async callable() awaited before EVERY HTTP request
    max_attempts: int = MAX_ATTEMPTS,   # transient failures tolerated before giving up
    max_requests: int | None = None,    # hard cap on HTTP requests of any kind (incl. the thinkingConfig fallback)
    retry_timeouts: bool = True,
    retry_malformed: bool = False,      # also retry empty / non-JSON answers
    min_retry_seconds: float = 0.0,     # retry only if at least this long remains before `deadline`
) -> dict:
    """
    The one request core: send `contents` (a list of user/model turns) with a
    JSON-schema-constrained response and return the parsed object.

    `schema` goes in `responseJsonSchema`, so the model is constrained to the
    shape we expect (enums, min/max items, length caps included).

    When `on_tokens` is an async callable the response is streamed and it
    receives an estimated token count as text arrives.

    `on_request` is awaited right before each HTTP request, retries included; the
    daily usage counter lives in that callback (so this module stays DB-free), and
    raising from it aborts the call. `deadline` bounds the whole call: each attempt
    gets at most the time that is left, and a retry is skipped when less than
    `min_retry_seconds` (after the backoff) would remain.

    Raises GeminiError (with .kind) on any failure with a user-friendly message.
    """
    key = api_key()
    if not key:
        raise GeminiError(
            "Gemini isn't configured. Set GEMINI_API_KEY (get one at "
            "https://aistudio.google.com/apikey).",
            kind="not_configured",
        )
    model_name = model or _env_model()
    headers = {"x-goog-api-key": key, "content-type": "application/json"}
    payload = _build_payload(system, contents, schema, temperature, model_name)

    failures = 0        # transient failures so far (the thinkingConfig fallback isn't counted)
    requests_made = 0   # every HTTP request, for max_requests
    dropped_thinking = False
    while True:
        attempt_timeout = timeout
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GeminiError("Gemini took too long to answer.", kind="timeout")
            attempt_timeout = min(timeout, remaining)
        if on_request is not None:
            await on_request()
        requests_made += 1
        try:
            if on_tokens is not None:
                content = await _stream_once(payload, headers, attempt_timeout, on_tokens, model_name)
            else:
                content = await _generate_once(payload, headers, attempt_timeout, model_name)
            if dropped_thinking:
                _NO_THINKING_CFG.add(model_name)  # succeeded only once the field was gone
            content = content.strip()
            if not content:
                raise GeminiError("Gemini returned an empty response.", kind="empty",
                                  retryable=retry_malformed)
            try:
                return json.loads(content)
            except json.JSONDecodeError as exc:
                raise GeminiError("Gemini returned malformed JSON.", kind="malformed",
                                  retryable=retry_malformed) from exc
        except httpx.TimeoutException:
            err = GeminiError("Gemini timed out generating the routine.", kind="timeout",
                              retryable=retry_timeouts)
        except httpx.TransportError:
            err = GeminiError("Couldn't reach Gemini. Check the network connection.",
                              kind="unreachable", retryable=True)
        except GeminiError as exc:
            err = exc
            if (exc.status == 400 and "thinkingConfig" in payload["generationConfig"]
                    and (max_requests is None or requests_made < max_requests)):
                # Some models reject thinkingConfig with a bare 400. Retry once
                # without it (not counted against the transient-error attempts).
                config = {k: v for k, v in payload["generationConfig"].items() if k != "thinkingConfig"}
                payload = {**payload, "generationConfig": config}
                dropped_thinking = True
                continue
        failures += 1
        if (not err.retryable or failures >= max_attempts
                or (max_requests is not None and requests_made >= max_requests)):
            raise err
        delay = _BACKOFF_SECONDS[min(failures - 1, len(_BACKOFF_SECONDS) - 1)]
        if deadline is not None and deadline - time.monotonic() - delay < min_retry_seconds:
            raise err
        logging.warning("gemini: %s — retrying in %.0fs (attempt %d/%d failed)",
                        err, delay, failures, max_attempts)
        await asyncio.sleep(delay)


async def chat_json(
    system: str,
    user: str,
    schema: dict,
    *,
    temperature: float = 0.4,
    timeout: float = 120.0,
    on_tokens: "object | None" = None,
    model: str | None = None,
    on_request: "object | None" = None,
) -> dict:
    """A single user message in, parsed JSON out: chat_turn_json() with the
    plan-generation retry policy (transient failures retried, long timeout)."""
    return await chat_turn_json(
        system, [user_turn(user)], schema,
        temperature=temperature, timeout=timeout, on_tokens=on_tokens,
        model=model, on_request=on_request,
    )


async def generate_json(
    system: str,
    user: str,
    schema: dict,
    *,
    temperature: float = 0.4,
    timeout: float = 120.0,
    on_tokens: "object | None" = None,
    model: str | None = None,
    on_request: "object | None" = None,
) -> tuple[dict, str]:
    """chat_json() plus the model name actually used — callers that record which
    model produced a result (e.g. coach_plans.model) store the returned name."""
    model_name = model or _env_model()
    result = await chat_json(
        system, user, schema,
        temperature=temperature, timeout=timeout, on_tokens=on_tokens,
        model=model_name, on_request=on_request,
    )
    return result, model_name
