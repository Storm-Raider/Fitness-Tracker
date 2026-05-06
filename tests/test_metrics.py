import pytest


@pytest.mark.asyncio
async def test_create_metric(client):
    resp = await client.post("/metrics", json={"weight_kg": 75.5, "calories": 2200})
    assert resp.status_code == 201
    assert "id" in resp.json()


@pytest.mark.asyncio
async def test_create_metric_no_calories(client):
    resp = await client.post("/metrics", json={"weight_kg": 80.0})
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_list_metrics(client):
    await client.post("/metrics", json={"weight_kg": 70.0})
    resp = await client.get("/metrics", headers={"Accept": "application/json"})
    assert resp.status_code == 200
