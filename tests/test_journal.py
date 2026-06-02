from datetime import date

import pytest


TODAY = date.today().isoformat()

LOG = {
    "log_date": TODAY,
    "day_number": 3,
    "weight_kg": 114.0,
    "workout": "Level 2 Day 4",
    "meal_1": "Coffee + Vitamin D, C",
    "meal_2": "Salmon + veggies + 2 multivitamins",
    "meal_3": "Chicken + rice + vegetables",
    "water_l": 2.0,
    "energy": "medium",
    "motivation": "medium",
    "sleep_hrs": 8.0,
    "steps": 4000,
    "notes": None,
}


@pytest.mark.asyncio
async def test_journal_page_renders(client):
    r = await client.get("/journal", headers={"Accept": "text/html"})
    assert r.status_code == 200
    assert "Daily Log" in r.text


@pytest.mark.asyncio
async def test_save_and_load_log(client, db_conn):
    r = await client.post("/journal", json=LOG)
    assert r.status_code == 200

    async with db_conn.execute(
        "SELECT * FROM daily_logs WHERE user_id=1 AND log_date=?", (TODAY,)
    ) as c:
        row = dict(await c.fetchone())

    assert row["weight_kg"] == 114.0
    assert row["energy"] == "medium"
    assert row["steps"] == 4000
    assert row["workout"] == "Level 2 Day 4"


@pytest.mark.asyncio
async def test_upsert_overwrites_same_date(client, db_conn):
    await client.post("/journal", json=LOG)
    updated = {**LOG, "weight_kg": 113.5, "energy": "high"}
    await client.post("/journal", json=updated)

    async with db_conn.execute(
        "SELECT weight_kg, energy FROM daily_logs WHERE user_id=1 AND log_date=?", (TODAY,)
    ) as c:
        row = dict(await c.fetchone())

    assert row["weight_kg"] == 113.5
    assert row["energy"] == "high"


@pytest.mark.asyncio
async def test_journal_shows_filled_entry(client):
    await client.post("/journal", json=LOG)
    r = await client.get("/journal", headers={"Accept": "text/html"})
    assert "Level 2 Day 4" in r.text


@pytest.mark.asyncio
async def test_invalid_energy_rejected(client):
    bad = {**LOG, "energy": "extreme"}
    r = await client.post("/journal", json=bad)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_dashboard_nudge_shown_when_no_log(client):
    r = await client.get("/", headers={"Accept": "text/html"})
    assert "Fill today" in r.text


@pytest.mark.asyncio
async def test_dashboard_nudge_hidden_after_log(client):
    await client.post("/journal", json=LOG)
    r = await client.get("/", headers={"Accept": "text/html"})
    assert "Fill today" not in r.text
