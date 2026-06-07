"""
Thin async client for a local Ollama server.

The AI coach (app/routes/coach.py) uses this to turn a user's training
history into a structured workout routine. Ollama runs entirely on-device,
so no data leaves the host — unlike the nightly Claude enrichment job.

Configuration (env vars):
  OLLAMA_URL    base URL of the Ollama server   (default http://localhost:11434)
  OLLAMA_MODEL  model tag to use for generation  (default qwen2.5:3b)

In Docker, the host's Ollama is reachable at http://host.docker.internal:11434
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


class OllamaError(RuntimeError):
    """Raised when the Ollama server is unreachable or returns bad output."""


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
    """Pre-load model weights into RAM so the first real generation doesn't pay the load cost."""
    try:
        await chat_json(
            "You are ready.",
            "ok",
            {"type": "object", "properties": {"ok": {"type": "string"}}, "required": ["ok"]},
            timeout=60.0,
            num_ctx=512,
        )
        logging.info("ollama: model warm-up complete (%s)", ollama_model())
    except Exception as exc:
        logging.debug("ollama: warm-up skipped (%s)", exc)


async def chat_json(
    system: str,
    user: str,
    schema: dict,
    *,
    model: str | None = None,
    temperature: float = 0.4,
    timeout: float = 240.0,
    num_ctx: int | None = None,
    on_tokens: "object | None" = None,  # async callable(count: int) → None; enables streaming
) -> dict:
    """
    Send a chat request to Ollama with a JSON-schema-constrained response and
    return the parsed object.

    `schema` is passed as Ollama's `format` field (structured outputs), which
    forces small models to emit valid JSON matching the shape we expect.

    When `on_tokens` is an async callable it receives a running chunk count every
    15 streaming chunks so callers can push progress events to connected clients.

    Raises OllamaError on any transport/parse failure with a user-friendly
    message — callers surface this to the UI rather than a 500.
    """
    model = model or ollama_model()
    opts: dict = {"temperature": temperature}
    if num_ctx:
        opts["num_ctx"] = num_ctx
    payload = {
        "model": model,
        "stream": on_tokens is not None,
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
                async with client.stream("POST", f"{ollama_url()}/api/chat", json=payload) as resp:
                    if resp.status_code == 404:
                        raise OllamaError(
                            f"Model '{model}' is not installed. Run `ollama pull {model}`."
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
                f"Couldn't reach Ollama at {ollama_url()}. Is the server running "
                f"(`ollama serve`)?"
            ) from exc
        except httpx.TimeoutException as exc:
            raise OllamaError(
                "Ollama timed out generating the routine. Try fewer training days, "
                "or switch to a faster model (set OLLAMA_MODEL=qwen2.5:1.5b in your .env)."
            ) from exc
        except OllamaError:
            raise
        content = "".join(content_parts).strip()
    else:
        # Non-streaming path (warm-up, spec generation, tests).
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(f"{ollama_url()}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError as exc:
            raise OllamaError(
                f"Couldn't reach Ollama at {ollama_url()}. Is the server running "
                f"(`ollama serve`)?"
            ) from exc
        except httpx.TimeoutException as exc:
            raise OllamaError(
                "Ollama timed out generating the routine. Try fewer training days, "
                "or switch to a faster model (set OLLAMA_MODEL=qwen2.5:1.5b in your .env)."
            ) from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:200]
            if exc.response.status_code == 404:
                raise OllamaError(
                    f"Model '{model}' is not installed. Run `ollama pull {model}`."
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
