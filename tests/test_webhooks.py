import os
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_get_webhook_config_no_url(admin_client):
    resp = await admin_client.get("/webhooks")
    assert resp.status_code == 200
    data = resp.json()
    assert data["url"] is None
    assert data["events"] == []


@pytest.mark.asyncio
async def test_get_webhook_config_requires_admin(client):
    resp = await client.get("/webhooks")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_webhook_config_with_url(admin_client, monkeypatch):
    monkeypatch.setenv("WEBHOOK_URL", "https://example.com/hook")
    resp = await admin_client.get("/webhooks")
    data = resp.json()
    assert data["url"] == "https://example.com/hook"
    assert "pr_achieved" in data["events"]


@pytest.mark.asyncio
async def test_webhook_fires_on_pr(client, monkeypatch):
    monkeypatch.setenv("WEBHOOK_URL", "https://example.com/hook")

    mock_client = AsyncMock()
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_client.post = AsyncMock(return_value=mock_response)

    from app.routes import workouts as workouts_module
    workouts_module._http_client = mock_client

    ex = await client.post("/exercises", json={"name": "Test Bench Press"})
    ex_id = ex.json()["id"]
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]

    resp = await client.post(f"/workouts/{w_id}/sets",
                             json={"exercise_id": ex_id, "reps": 5, "weight_kg": 100.0})
    assert resp.json()["is_pr"] is True


@pytest.mark.asyncio
async def test_webhook_pr_payload_includes_user_identity(client, monkeypatch):
    monkeypatch.setenv("WEBHOOK_URL", "https://example.com/hook")

    mock_client = AsyncMock()
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_client.post = AsyncMock(return_value=mock_response)

    from app.routes import workouts as workouts_module
    workouts_module._http_client = mock_client

    ex = await client.post("/exercises", json={"name": "PR Payload Squat"})
    ex_id = ex.json()["id"]
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]

    await client.post(f"/workouts/{w_id}/sets",
                      json={"exercise_id": ex_id, "reps": 3, "weight_kg": 120.0})

    assert mock_client.post.called
    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["event"] == "pr_achieved"
    assert payload["user_id"] == 1
    assert payload["username"] == "testuser"


@pytest.mark.asyncio
async def test_webhook_session_complete_payload_includes_user_identity(client, monkeypatch):
    monkeypatch.setenv("WEBHOOK_URL", "https://example.com/hook")

    mock_client = AsyncMock()
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_client.post = AsyncMock(return_value=mock_response)

    from app.routes import workouts as workouts_module
    workouts_module._http_client = mock_client

    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]

    await client.post(f"/workouts/{w_id}/finish")

    assert mock_client.post.called
    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["event"] == "session_complete"
    assert payload["user_id"] == 1
    assert payload["username"] == "testuser"


@pytest.mark.asyncio
async def test_webhook_does_not_fire_on_non_pr(client, monkeypatch):
    monkeypatch.setenv("WEBHOOK_URL", "https://example.com/hook")

    mock_client = AsyncMock()
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_client.post = AsyncMock(return_value=mock_response)

    from app.routes import workouts as workouts_module
    workouts_module._http_client = mock_client

    ex = await client.post("/exercises", json={"name": "Pull Up"})
    ex_id = ex.json()["id"]
    w = await client.post("/workouts", json={"notes": None})
    w_id = w.json()["id"]

    # First set sets the PR
    await client.post(f"/workouts/{w_id}/sets",
                      json={"exercise_id": ex_id, "reps": 5, "weight_kg": 70.0})
    mock_client.post.reset_mock()

    # Same weight — not a PR, webhook should not fire
    resp = await client.post(f"/workouts/{w_id}/sets",
                             json={"exercise_id": ex_id, "reps": 5, "weight_kg": 70.0})
    assert resp.json()["is_pr"] is False
