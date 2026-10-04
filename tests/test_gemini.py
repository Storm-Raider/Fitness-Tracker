import json as _json

import httpx
import pytest

from app.utils import gemini

SCHEMA = {"type": "object", "properties": {"ok": {"type": "string"}}, "required": ["ok"]}


def _ok_body(text: str = '{"ok": "ok"}') -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}


class _FakeResponse:
    def __init__(self, status: int = 200, payload: dict | None = None, text: str = ""):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = text or _json.dumps(self._payload)

    def json(self):
        return self._payload


class _FakeStreamResponse:
    def __init__(self, status: int, lines: list[str], body: str = ""):
        self.status_code = status
        self._lines = lines
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aread(self):
        return self._body.encode()

    async def aiter_lines(self):
        for line in self._lines:
            yield line


def _install_client(monkeypatch, responses, *, stream_lines=None, stream_status=200):
    """Patch gemini.httpx.AsyncClient; `responses` is consumed one per post()."""
    calls: list[dict] = []
    queue = list(responses)

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, headers=None):
            calls.append({"url": url, "json": json, "headers": headers})
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        def stream(self, method, url, json=None, headers=None):
            calls.append({"url": url, "json": json, "headers": headers})
            return _FakeStreamResponse(stream_status, stream_lines or [])

    monkeypatch.setattr(gemini.httpx, "AsyncClient", _FakeAsyncClient)
    return calls


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(gemini.asyncio, "sleep", _no_sleep)


# ── Configuration ────────────────────────────────────────────────────

def test_is_configured_reflects_api_key(monkeypatch):
    assert gemini.is_configured() is True
    monkeypatch.setenv("GEMINI_API_KEY", "   ")
    assert gemini.is_configured() is False
    monkeypatch.delenv("GEMINI_API_KEY")
    assert gemini.is_configured() is False


def test_model_default_and_override(monkeypatch):
    assert gemini.model() == gemini.DEFAULT_MODEL
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-pro")
    assert gemini.model() == "gemini-2.5-pro"


# ── Request shape ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_json_request_shape(monkeypatch):
    calls = _install_client(monkeypatch, [_FakeResponse(200, _ok_body())])

    result = await gemini.chat_json("sys prompt", "user prompt", SCHEMA, temperature=0.2)

    assert result == {"ok": "ok"}
    call = calls[0]
    assert call["url"].endswith(f"/models/{gemini.DEFAULT_MODEL}:generateContent")
    # Key travels in a header, never in the URL (URLs end up in logs).
    assert "test-key" not in call["url"]
    assert call["headers"]["x-goog-api-key"] == "test-key"
    body = call["json"]
    assert body["systemInstruction"]["parts"][0]["text"] == "sys prompt"
    assert body["contents"][0]["parts"][0]["text"] == "user prompt"
    cfg = body["generationConfig"]
    assert cfg["responseMimeType"] == "application/json"
    assert cfg["responseJsonSchema"] == SCHEMA
    assert cfg["temperature"] == 0.2


@pytest.mark.asyncio
async def test_thinking_disabled_only_for_flash_models(monkeypatch):
    calls = _install_client(monkeypatch, [_FakeResponse(200, _ok_body())] * 3)

    await gemini.chat_json("s", "u", SCHEMA)  # default model is a Flash model
    assert calls[0]["json"]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}

    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    await gemini.chat_json("s", "u", SCHEMA)
    assert calls[1]["json"]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}

    # Pro models can't turn thinking off — sending the field would 400.
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-pro")
    await gemini.chat_json("s", "u", SCHEMA)
    assert "thinkingConfig" not in calls[2]["json"]["generationConfig"]


@pytest.mark.asyncio
async def test_generate_json_returns_model_used(monkeypatch):
    _install_client(monkeypatch, [_FakeResponse(200, _ok_body())])
    result, model_used = await gemini.generate_json("s", "u", SCHEMA)
    assert result == {"ok": "ok"}
    assert model_used == gemini.DEFAULT_MODEL


# ── Errors ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_key_raises_without_network(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    calls = _install_client(monkeypatch, [])
    with pytest.raises(gemini.GeminiError, match="GEMINI_API_KEY"):
        await gemini.chat_json("s", "u", SCHEMA)
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status,text", [
    (400, '{"error": {"message": "API key not valid. Please pass a valid API key."}}'),
    (401, "unauthorized"),
    (403, "forbidden"),
])
async def test_bad_key_is_reported_not_retried(monkeypatch, status, text):
    calls = _install_client(monkeypatch, [_FakeResponse(status, text=text)] * 3)
    with pytest.raises(gemini.GeminiError, match="API key"):
        await gemini.chat_json("s", "u", SCHEMA)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_unknown_model_404_surfaces_googles_reason(monkeypatch):
    # Google also 404s for retired-but-still-listed models ("no longer available
    # to new users") — the real reason must reach the UI, not a generic
    # "model not found" that sends people hunting for a typo.
    monkeypatch.setenv("GEMINI_MODEL", "gemini-old")
    body = '{"error": {"message": "This model models/gemini-old is no longer available to new users."}}'
    _install_client(monkeypatch, [_FakeResponse(404, text=body)])
    with pytest.raises(gemini.GeminiError, match="gemini-old.*no longer available to new users"):
        await gemini.chat_json("s", "u", SCHEMA)

    _install_client(monkeypatch, [_FakeResponse(404, text="<html>not json</html>")])
    with pytest.raises(gemini.GeminiError, match="gemini-old"):
        await gemini.chat_json("s", "u", SCHEMA)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
async def test_transient_status_is_retried_then_succeeds(monkeypatch, status):
    calls = _install_client(
        monkeypatch,
        [_FakeResponse(status, text="busy"), _FakeResponse(200, _ok_body())],
    )
    assert await gemini.chat_json("s", "u", SCHEMA) == {"ok": "ok"}
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_transient_status_gives_up_after_max_attempts(monkeypatch):
    calls = _install_client(monkeypatch, [_FakeResponse(429, text="quota")] * 5)
    with pytest.raises(gemini.GeminiError, match="rate limit|quota"):
        await gemini.chat_json("s", "u", SCHEMA)
    assert len(calls) == gemini.MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_timeout_and_connect_errors_are_wrapped(monkeypatch):
    _install_client(monkeypatch, [httpx.ConnectError("boom")] * gemini.MAX_ATTEMPTS)
    with pytest.raises(gemini.GeminiError, match="reach Gemini"):
        await gemini.chat_json("s", "u", SCHEMA)

    _install_client(monkeypatch, [httpx.ReadTimeout("slow")] * gemini.MAX_ATTEMPTS)
    with pytest.raises(gemini.GeminiError, match="timed out"):
        await gemini.chat_json("s", "u", SCHEMA)


@pytest.mark.asyncio
async def test_empty_and_malformed_content(monkeypatch):
    _install_client(monkeypatch, [_FakeResponse(200, {"candidates": []})])
    with pytest.raises(gemini.GeminiError, match="empty"):
        await gemini.chat_json("s", "u", SCHEMA)

    _install_client(monkeypatch, [_FakeResponse(200, _ok_body("{not json"))])
    with pytest.raises(gemini.GeminiError, match="malformed"):
        await gemini.chat_json("s", "u", SCHEMA)


@pytest.mark.asyncio
async def test_prompt_blocked_is_reported(monkeypatch):
    _install_client(monkeypatch, [_FakeResponse(200, {"promptFeedback": {"blockReason": "SAFETY"}})])
    with pytest.raises(gemini.GeminiError, match="SAFETY"):
        await gemini.chat_json("s", "u", SCHEMA)


@pytest.mark.asyncio
async def test_thought_parts_are_ignored(monkeypatch):
    body = {"candidates": [{"content": {"parts": [
        {"text": "let me think", "thought": True},
        {"text": '{"ok": "ok"}'},
    ]}}]}
    _install_client(monkeypatch, [_FakeResponse(200, body)])
    assert await gemini.chat_json("s", "u", SCHEMA) == {"ok": "ok"}


# ── Streaming ────────────────────────────────────────────────────────

def _sse(text: str) -> str:
    return "data: " + _json.dumps(_ok_body(text))


@pytest.mark.asyncio
async def test_streaming_assembles_chunks_and_reports_progress(monkeypatch):
    # 3 chunks that concatenate into valid JSON; the middle one is long enough
    # (~200 chars ≈ 50 est. tokens) to cross the progress threshold.
    pad = "x" * 200
    chunks = ['{"ok": "', pad, '"}']
    calls = _install_client(
        monkeypatch, [], stream_lines=["", _sse(chunks[0]), _sse(chunks[1]), _sse(chunks[2])],
    )
    seen: list[int] = []

    async def on_tokens(n):
        seen.append(n)

    result = await gemini.chat_json("s", "u", SCHEMA, on_tokens=on_tokens)

    assert result == {"ok": pad}
    assert calls[0]["url"].endswith(":streamGenerateContent?alt=sse")
    assert seen and seen == sorted(seen) and seen[-1] >= 15


@pytest.mark.asyncio
async def test_streaming_http_error_is_mapped(monkeypatch):
    _install_client(monkeypatch, [], stream_status=403)

    async def on_tokens(n):
        pass

    with pytest.raises(gemini.GeminiError, match="API key"):
        await gemini.chat_json("s", "u", SCHEMA, on_tokens=on_tokens)
