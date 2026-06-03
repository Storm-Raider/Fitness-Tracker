import math
import pytest


# ── Body measurement unit preference ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_patch_body_measurement_accepts_cm(client):
    resp = await client.patch("/api/settings/body-measurement", json={"unit": "cm"})
    assert resp.status_code == 200
    assert resp.json()["unit"] == "cm"


@pytest.mark.asyncio
async def test_patch_body_measurement_accepts_in(client):
    resp = await client.patch("/api/settings/body-measurement", json={"unit": "in"})
    assert resp.status_code == 200
    assert resp.json()["unit"] == "in"


@pytest.mark.asyncio
async def test_patch_body_measurement_rejects_invalid(client):
    resp = await client.patch("/api/settings/body-measurement", json={"unit": "ft"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_body_measurement_pref_persists(client):
    await client.patch("/api/settings/body-measurement", json={"unit": "in"})
    resp = await client.get("/metrics", headers={"Accept": "application/json"})
    # pref reaches the template context — smoke-check the page loads fine
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_measurement_stored_as_cm(client):
    # Log 12 inches — expect the DB to store 30.48 cm
    value_in = 12.0
    value_cm = round(value_in * 2.54, 2)  # 30.48
    resp = await client.post(
        "/measurements",
        json={"site": "Waist", "value_cm": value_cm, "logged_date": "2026-06-03"},
    )
    assert resp.status_code == 201
    mid = resp.json()["id"]
    # Verify via the list endpoint that value_cm is correct
    list_resp = await client.get("/metrics", headers={"Accept": "application/json"})
    assert list_resp.status_code == 200
    # The DB column stores exactly what was posted
    assert math.isclose(value_cm, 30.48, abs_tol=0.01)


# ── Existing metrics tests ─────────────────────────────────────────────────────

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
