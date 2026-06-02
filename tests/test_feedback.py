import json

import pytest

from app.routes import feedback
from app.routes.workouts import set_http_client


class _FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _FakeClient:
    """Records the last POST and returns a canned response."""
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return self.response


@pytest.fixture(autouse=True)
def _reset_cooldown():
    feedback._last_submit.clear()
    yield
    feedback._last_submit.clear()
    set_http_client(None)


@pytest.fixture
def gh_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_testtoken")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")


@pytest.mark.asyncio
async def test_page_configured(client, gh_env):
    r = await client.get("/feedback", headers={"Accept": "text/html"})
    assert r.status_code == 200
    assert "Report a bug or request a feature" in r.text
    assert "isn't configured" not in r.text


@pytest.mark.asyncio
async def test_page_not_configured(client, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    r = await client.get("/feedback", headers={"Accept": "text/html"})
    assert r.status_code == 200
    assert "isn't configured" in r.text


@pytest.mark.asyncio
async def test_submit_bug_creates_issue_with_bug_label(client, gh_env):
    fake = _FakeClient(_FakeResponse(201, {"html_url": "https://github.com/owner/repo/issues/5", "number": 5}))
    set_http_client(fake)

    r = await client.post("/feedback", json={
        "type": "bug", "title": "Login breaks", "description": "It throws a 500 on submit.",
    })
    assert r.status_code == 201
    assert r.json()["number"] == 5

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"] == "https://api.github.com/repos/owner/repo/issues"
    assert call["json"]["labels"] == ["bug"]
    assert call["json"]["title"].startswith("[Bug]")
    assert "Bearer ghp_testtoken" == call["headers"]["Authorization"]
    # username footer present
    assert "testuser" in call["json"]["body"]


@pytest.mark.asyncio
async def test_submit_feature_uses_enhancement_label(client, gh_env):
    fake = _FakeClient(_FakeResponse(201, {"html_url": "u", "number": 9}))
    set_http_client(fake)

    r = await client.post("/feedback", json={
        "type": "feature", "title": "Dark graphs", "description": "Please add charts in dark mode.",
    })
    assert r.status_code == 201
    assert fake.calls[0]["json"]["labels"] == ["enhancement"]
    assert fake.calls[0]["json"]["title"].startswith("[Feature]")


@pytest.mark.asyncio
async def test_submit_rejected_when_not_configured(client, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    r = await client.post("/feedback", json={
        "type": "bug", "title": "anything", "description": "ten chars min ok",
    })
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_invalid_type_rejected(client, gh_env):
    r = await client.post("/feedback", json={
        "type": "complaint", "title": "x y z", "description": "ten chars min ok",
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_short_title_rejected(client, gh_env):
    r = await client.post("/feedback", json={
        "type": "bug", "title": "ab", "description": "ten chars min ok",
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_github_error_surfaces_502(client, gh_env):
    fake = _FakeClient(_FakeResponse(403, text="rate limited"))
    set_http_client(fake)
    r = await client.post("/feedback", json={
        "type": "bug", "title": "Broken thing", "description": "it is broken badly",
    })
    assert r.status_code == 502


@pytest.mark.asyncio
async def test_cooldown_blocks_rapid_resubmit(client, gh_env):
    fake = _FakeClient(_FakeResponse(201, {"html_url": "u", "number": 1}))
    set_http_client(fake)
    first = await client.post("/feedback", json={
        "type": "bug", "title": "First report", "description": "something is wrong here",
    })
    assert first.status_code == 201
    second = await client.post("/feedback", json={
        "type": "bug", "title": "Second report", "description": "another thing is wrong",
    })
    assert second.status_code == 429
