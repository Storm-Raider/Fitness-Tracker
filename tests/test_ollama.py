import json as _json

import httpx
import pytest

from app.utils import ollama


class _FakeResponse:
    """Mimics httpx.Response for the non-streaming chat_json() path."""

    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Mimics httpx.AsyncClient for the non-streaming path and records the
    JSON body passed to post() so the test can assert on it."""

    last_payload = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None):
        _FakeAsyncClient.last_payload = json
        return _FakeResponse({"message": {"content": '{"ok": "ok"}'}})


@pytest.mark.asyncio
async def test_chat_json_disables_thinking_non_streaming(monkeypatch):
    monkeypatch.setattr(ollama.httpx, "AsyncClient", _FakeAsyncClient)

    result = await ollama.chat_json(
        "system prompt", "user prompt",
        {"type": "object", "properties": {"ok": {"type": "string"}}, "required": ["ok"]},
    )

    assert result == {"ok": "ok"}
    assert _FakeAsyncClient.last_payload["think"] is False


@pytest.mark.asyncio
async def test_chat_json_sets_full_window_repeat_penalty_non_streaming(monkeypatch):
    """The coach's actual repetition problem is long-range (an exercise
    reappearing days — thousands of tokens — later), not local n-gram
    repetition, so the penalty window must cover the full context
    (repeat_last_n=-1), not Ollama's default 64-token lookback."""
    monkeypatch.setattr(ollama.httpx, "AsyncClient", _FakeAsyncClient)

    await ollama.chat_json(
        "system prompt", "user prompt",
        {"type": "object", "properties": {"ok": {"type": "string"}}, "required": ["ok"]},
    )

    opts = _FakeAsyncClient.last_payload["options"]
    assert opts["repeat_last_n"] == -1
    assert opts["repeat_penalty"] > 1.0


class _FakeStreamResponse:
    """Mimics the httpx streaming Response yielded inside `async with
    client.stream(...)`, emitting one complete Ollama-shaped JSON line."""

    def __init__(self, content: str):
        self.status_code = 200
        self._content = content

    async def aiter_lines(self):
        yield _json.dumps({"message": {"content": self._content}, "done": True})

    async def aread(self):
        return b""


class _FakeStreamCM:
    def __init__(self, content: str):
        self._resp = _FakeStreamResponse(content)

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *args):
        return False


class _FakeAsyncClientStreaming:
    """Mimics httpx.AsyncClient for the streaming path (on_tokens set) and
    records the JSON body passed to stream() so the test can assert on it."""

    last_payload = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, method, url, json=None):
        _FakeAsyncClientStreaming.last_payload = json
        return _FakeStreamCM('{"ok": "ok"}')


@pytest.mark.asyncio
async def test_chat_json_disables_thinking_streaming(monkeypatch):
    monkeypatch.setattr(ollama.httpx, "AsyncClient", _FakeAsyncClientStreaming)

    async def _on_tokens(count):
        pass

    result = await ollama.chat_json(
        "system prompt", "user prompt",
        {"type": "object", "properties": {"ok": {"type": "string"}}, "required": ["ok"]},
        on_tokens=_on_tokens,
    )

    assert result == {"ok": "ok"}
    assert _FakeAsyncClientStreaming.last_payload["think"] is False


@pytest.mark.asyncio
async def test_chat_json_sets_full_window_repeat_penalty_streaming(monkeypatch):
    monkeypatch.setattr(ollama.httpx, "AsyncClient", _FakeAsyncClientStreaming)

    async def _on_tokens(count):
        pass

    await ollama.chat_json(
        "system prompt", "user prompt",
        {"type": "object", "properties": {"ok": {"type": "string"}}, "required": ["ok"]},
        on_tokens=_on_tokens,
    )

    opts = _FakeAsyncClientStreaming.last_payload["options"]
    assert opts["repeat_last_n"] == -1
    assert opts["repeat_penalty"] > 1.0


# ── chat_json_with_fallback: primary → local fallback degradation ─────

class _FakeAsyncClientPerHost:
    """Routes based on the target URL embedded in the request: PRIMARY_URL
    always fails as unreachable (simulating a networked machine being down);
    any other URL (the fallback) succeeds. Records which URLs were hit."""

    urls_hit: list[str] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None):
        _FakeAsyncClientPerHost.urls_hit.append(url)
        if url.startswith("http://primary-unreachable:11434"):
            raise httpx.ConnectError("connection refused", request=None)
        return _FakeResponse({"message": {"content": '{"ok": "fallback-served"}'}})


@pytest.mark.asyncio
async def test_fallback_used_when_primary_unreachable(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://primary-unreachable:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "big-networked-model")
    monkeypatch.setenv("OLLAMA_FALLBACK_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_FALLBACK_MODEL", "small-local-model")
    monkeypatch.setattr(ollama.httpx, "AsyncClient", _FakeAsyncClientPerHost)
    _FakeAsyncClientPerHost.urls_hit = []

    result, model_used = await ollama.chat_json_with_fallback(
        "system prompt", "user prompt",
        {"type": "object", "properties": {"ok": {"type": "string"}}, "required": ["ok"]},
    )

    assert result == {"ok": "fallback-served"}
    assert model_used == "small-local-model"
    assert _FakeAsyncClientPerHost.urls_hit == [
        "http://primary-unreachable:11434/api/chat",
        "http://localhost:11434/api/chat",
    ]


@pytest.mark.asyncio
async def test_no_fallback_needed_when_primary_reachable(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "primary-model")
    monkeypatch.setenv("OLLAMA_FALLBACK_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_FALLBACK_MODEL", "small-local-model")
    monkeypatch.setattr(ollama.httpx, "AsyncClient", _FakeAsyncClientPerHost)
    _FakeAsyncClientPerHost.urls_hit = []

    result, model_used = await ollama.chat_json_with_fallback(
        "system prompt", "user prompt",
        {"type": "object", "properties": {"ok": {"type": "string"}}, "required": ["ok"]},
    )

    assert result == {"ok": "fallback-served"}  # this fake always returns this content
    assert model_used == "primary-model"  # but the model tag reflects who actually served it
    assert _FakeAsyncClientPerHost.urls_hit == ["http://localhost:11434/api/chat"]  # one call only


@pytest.mark.asyncio
async def test_fallback_not_retried_when_identical_to_primary(monkeypatch):
    """If OLLAMA_FALLBACK_URL/MODEL are left at their defaults and happen to
    equal the primary (e.g. no networked machine configured at all — both
    point at this same local Ollama), there's nothing to gain by retrying the
    identical target, so the original error should surface directly."""
    monkeypatch.setenv("OLLAMA_URL", "http://primary-unreachable:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "same-model")
    monkeypatch.setenv("OLLAMA_FALLBACK_URL", "http://primary-unreachable:11434")
    monkeypatch.setenv("OLLAMA_FALLBACK_MODEL", "same-model")
    monkeypatch.setattr(ollama.httpx, "AsyncClient", _FakeAsyncClientPerHost)
    _FakeAsyncClientPerHost.urls_hit = []

    with pytest.raises(ollama.OllamaError):
        await ollama.chat_json_with_fallback(
            "system prompt", "user prompt",
            {"type": "object", "properties": {"ok": {"type": "string"}}, "required": ["ok"]},
        )
    assert _FakeAsyncClientPerHost.urls_hit == ["http://primary-unreachable:11434/api/chat"]


@pytest.mark.asyncio
async def test_fallback_not_used_for_content_only_failures(monkeypatch):
    """A host that responds but with malformed content is a different problem
    than an unreachable host — chat_json_with_fallback() must not mask it by
    silently trying a different model (app/routes/coach.py has its own retry
    for this specific case, at the same host/model)."""
    class _BadContentClient(_FakeAsyncClientPerHost):
        async def post(self, url, json=None):
            _FakeAsyncClientPerHost.urls_hit.append(url)
            return _FakeResponse({"message": {"content": "not valid json"}})

    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_FALLBACK_URL", "http://localhost:9999")  # distinct from primary
    monkeypatch.setattr(ollama.httpx, "AsyncClient", _BadContentClient)
    _FakeAsyncClientPerHost.urls_hit = []

    with pytest.raises(ollama.OllamaError, match="malformed JSON"):
        await ollama.chat_json_with_fallback(
            "system prompt", "user prompt",
            {"type": "object", "properties": {"ok": {"type": "string"}}, "required": ["ok"]},
        )
    # Only the primary was hit — no fallback attempt for a content problem.
    assert _FakeAsyncClientPerHost.urls_hit == ["http://localhost:11434/api/chat"]
