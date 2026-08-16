"""
Thin async client for a local or LAN Ollama server, with a same-host fallback.

The AI coach (app/routes/coach.py) uses this to turn a user's training
history into a structured workout routine. All inference stays on the LAN —
nothing about the user leaves the host/network — unlike the nightly Claude
enrichment job.

Configuration (env vars):
  OLLAMA_URL             primary server URL       (default http://localhost:11434)
  OLLAMA_MODEL           primary model tag        (default qwen2.5:1.5b)
  OLLAMA_FALLBACK_URL    fallback server URL      (default http://localhost:11434)
  OLLAMA_FALLBACK_MODEL  fallback model tag       (default qwen2.5:1.5b)

A bigger model on a beefier networked machine (e.g. qwen3:8b) is a reasonable
primary — it's simply not something this Pi can serve fast on its own — but
that machine being unreachable shouldn't take the whole coach feature down
with it. `chat_json_with_fallback()` tries the primary first and, only for
host-unavailability failures (connection refused/unreachable, timeout, model
not installed), retries once against the fallback. The fallback defaults to
this same Pi's own local Ollama, which is always in the app's control — no
LAN dependency, no separate machine to keep running.

In Docker, a host's Ollama is reachable at http://host.docker.internal:11434
(see docker-compose.yml).
"""

import json
import logging
import os

import httpx

DEFAULT_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:1.5b"


def ollama_url() -> str:
    return os.environ.get("OLLAMA_URL", DEFAULT_URL).rstrip("/")


def ollama_model() -> str:
    return os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def ollama_fallback_url() -> str:
    return os.environ.get("OLLAMA_FALLBACK_URL", DEFAULT_URL).rstrip("/")


def ollama_fallback_model() -> str:
    return os.environ.get("OLLAMA_FALLBACK_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


class OllamaError(RuntimeError):
    """Raised when the Ollama server is unreachable or returns bad output."""

    def __init__(self, message: str, *, host_unavailable: bool = False):
        super().__init__(message)
        # True only for failures that mean "this host/model isn't currently
        # servable" (refused/unreachable connection, timeout, model not
        # installed) — the class of failure chat_json_with_fallback() will
        # retry against the fallback host. False for failures that mean the
        # host responded but the content was bad (empty/malformed JSON) —
        # retrying a *different* host for a content problem isn't obviously
        # right, and app/routes/coach.py already retries those same-host.
        self.host_unavailable = host_unavailable


async def is_available(timeout: float = 3.0) -> tuple[bool, list[str]]:
    """Return (reachable, [model names]) without raising."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{ollama_url()}/api/tags")
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
            return True, models
    except Exception:
        return False, []


async def warm_up() -> None:
    """Pre-load model weights into RAM so the first real generation doesn't pay the
    load cost. Goes through the same fallback path a real generation would: if the
    primary is unreachable at startup, this naturally warms the fallback instead,
    so a later degraded generation isn't ALSO paying a cold-load penalty on top of
    already having lost its preferred model."""
    try:
        _, model_used = await chat_json_with_fallback(
            "You are ready.",
            "ok",
            {"type": "object", "properties": {"ok": {"type": "string"}}, "required": ["ok"]},
            timeout=60.0,
            num_ctx=512,
        )
        logging.info("ollama: model warm-up complete (%s)", model_used)
    except Exception as exc:
        logging.debug("ollama: warm-up skipped (%s)", exc)


async def chat_json(
    system: str,
    user: str,
    schema: dict,
    *,
    url: str | None = None,
    model: str | None = None,
    temperature: float = 0.4,
    timeout: float = 240.0,
    num_ctx: int | None = None,
    on_tokens: "object | None" = None,  # async callable(count: int) → None; enables streaming
) -> dict:
    """
    Send a chat request to one specific Ollama server with a JSON-schema-
    constrained response and return the parsed object.

    `schema` is passed as Ollama's `format` field (structured outputs), which
    forces small models to emit valid JSON matching the shape we expect.

    When `on_tokens` is an async callable it receives a running chunk count every
    15 streaming chunks so callers can push progress events to connected clients.

    Raises OllamaError on any transport/parse failure with a user-friendly
    message — callers surface this to the UI rather than a 500. This function
    talks to exactly the (url, model) it's given and never falls back on its
    own — that policy lives in chat_json_with_fallback(), which is what
    app/routes/coach.py actually calls.
    """
    url = url or ollama_url()
    model = model or ollama_model()
    opts: dict = {
        "temperature": temperature,
        # The coach's actual repetition problem is long-range and semantic (the
        # same exercise reappearing several *days* — thousands of tokens — later
        # in a single generation), not local n-gram repetition. Ollama's default
        # repeat_last_n (64 tokens) only looks back far enough to catch local
        # stutter, not cross-day duplication. repeat_last_n=-1 extends the
        # penalty window to the full context so it can actually see and
        # discourage repeats that far back; repeat_penalty is nudged only
        # slightly above Ollama's own default (1.1) since this is layered on
        # top of, not a replacement for, the prompt instructions and the
        # deterministic _repair_plan() pass — the goal is fewer retries/swaps
        # needed downstream, not to eliminate the safety net.
        "repeat_penalty": 1.15,
        "repeat_last_n": -1,
    }
    if num_ctx:
        opts["num_ctx"] = num_ctx
    payload = {
        "model": model,
        "stream": on_tokens is not None,
        # Disable hidden reasoning on models that support it (e.g. qwen3).
        # Nothing in this app reads message.thinking — every caller wants a
        # fast, structured JSON answer. Non-reasoning models ignore this
        # field entirely, so it's safe to send unconditionally.
        "think": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "format": schema,
        "keep_alive": -1,
        "options": opts,
    }

    if on_tokens is not None:
        # Streaming path: accumulate tokens, fire progress callbacks every 15 chunks.
        content_parts: list[str] = []
        chunk_count = 0
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", f"{url}/api/chat", json=payload) as resp:
                    if resp.status_code == 404:
                        raise OllamaError(
                            f"Model '{model}' is not installed on {url}. Run `ollama pull {model}`.",
                            host_unavailable=True,
                        )
                    if resp.status_code != 200:
                        body = await resp.aread()
                        raise OllamaError(
                            f"Ollama returned HTTP {resp.status_code}: {body.decode()[:200]}"
                        )
                    async for raw_line in resp.aiter_lines():
                        if not raw_line:
                            continue
                        try:
                            chunk = json.loads(raw_line)
                        except json.JSONDecodeError:
                            continue
                        token = (chunk.get("message") or {}).get("content", "")
                        if token:
                            content_parts.append(token)
                            chunk_count += 1
                            if chunk_count % 15 == 0:
                                await on_tokens(chunk_count)
                        if chunk.get("done"):
                            break
        except httpx.ConnectError as exc:
            raise OllamaError(
                f"Couldn't reach Ollama at {url}. Is the server running "
                f"(`ollama serve`)?",
                host_unavailable=True,
            ) from exc
        except httpx.TimeoutException as exc:
            raise OllamaError(
                f"Ollama at {url} timed out generating the routine.",
                host_unavailable=True,
            ) from exc
        except OllamaError:
            raise
        content = "".join(content_parts).strip()
    else:
        # Non-streaming path (warm-up, spec generation, tests).
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(f"{url}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError as exc:
            raise OllamaError(
                f"Couldn't reach Ollama at {url}. Is the server running "
                f"(`ollama serve`)?",
                host_unavailable=True,
            ) from exc
        except httpx.TimeoutException as exc:
            raise OllamaError(
                f"Ollama at {url} timed out generating the routine.",
                host_unavailable=True,
            ) from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:200]
            if exc.response.status_code == 404:
                raise OllamaError(
                    f"Model '{model}' is not installed on {url}. Run `ollama pull {model}`.",
                    host_unavailable=True,
                ) from exc
            raise OllamaError(
                f"Ollama returned HTTP {exc.response.status_code}: {detail}"
            ) from exc
        content = (data.get("message") or {}).get("content", "").strip()

    if not content:
        raise OllamaError("Ollama returned an empty response.")
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise OllamaError("Ollama returned malformed JSON.") from exc


async def chat_json_with_fallback(
    system: str,
    user: str,
    schema: dict,
    *,
    temperature: float = 0.4,
    timeout: float = 240.0,
    num_ctx: int | None = None,
    on_tokens: "object | None" = None,
) -> tuple[dict, str]:
    """
    Try the configured primary (OLLAMA_URL/OLLAMA_MODEL) first; on a
    host-unavailability failure (unreachable, timeout, model not installed —
    see OllamaError.host_unavailable), retry once against the fallback
    (OLLAMA_FALLBACK_URL/OLLAMA_FALLBACK_MODEL, which defaults to this same
    machine's own local Ollama). Content-quality failures (empty/malformed
    JSON) are NOT retried here — the host responded fine, that's a different
    problem, and app/routes/coach.py already has its own retry for it.

    Returns (parsed_json, model_name_actually_used) — callers that record
    which model produced a result (e.g. coach_plans.model) must use the
    returned name, not ollama_model(), or a silent fallback would be
    misreported as having come from the primary.
    """
    primary_url, primary_model = ollama_url(), ollama_model()
    try:
        result = await chat_json(
            system, user, schema,
            url=primary_url, model=primary_model,
            temperature=temperature, timeout=timeout, num_ctx=num_ctx, on_tokens=on_tokens,
        )
        return result, primary_model
    except OllamaError as exc:
        if not exc.host_unavailable:
            raise
        fallback_url, fallback_model = ollama_fallback_url(), ollama_fallback_model()
        if fallback_url == primary_url and fallback_model == primary_model:
            raise  # fallback is identical to primary — nothing to gain by retrying
        logging.warning(
            "ollama: primary %s (%s) unavailable (%s) — falling back to %s (%s)",
            primary_url, primary_model, exc, fallback_url, fallback_model,
        )
        result = await chat_json(
            system, user, schema,
            url=fallback_url, model=fallback_model,
            temperature=temperature, timeout=timeout, num_ctx=num_ctx, on_tokens=on_tokens,
        )
        return result, fallback_model
