import json as _json

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
