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
async def test_get_entry_for_existing_date(client, db_conn):
    await client.post("/journal", json=LOG)

    r = await client.get(f"/journal/entry?date={TODAY}")
    assert r.status_code == 200
    entry = r.json()["entry"]
    assert entry is not None
    assert entry["weight_kg"] == 114.0
    assert entry["workout"] == "Level 2 Day 4"
    assert entry["log_date"] == TODAY


@pytest.mark.asyncio
async def test_get_entry_for_date_with_no_log_returns_null(client):
    r = await client.get("/journal/entry?date=2020-01-01")
    assert r.status_code == 200
    assert r.json()["entry"] is None


@pytest.mark.asyncio
async def test_get_entry_rejects_malformed_date(client):
    r = await client.get("/journal/entry?date=not-a-date")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_get_entry_requires_auth(anon_client):
    r = await anon_client.get("/journal/entry?date=2020-01-01")
    assert r.status_code == 302
    assert "/login" in r.headers["location"]


@pytest.mark.asyncio
async def test_get_entry_scoped_to_current_user(client, db_conn):
    # Seed a log for a different user; the `client` fixture's user (id=1)
    # must never see it.
    await db_conn.execute(
        "INSERT INTO users(id, username, password_hash, is_admin) "
        "VALUES (99, 'otheruser', 'x', 0)"
    )
    await db_conn.execute(
        "INSERT INTO daily_logs(user_id, log_date, weight_kg) VALUES (99, '2020-06-01', 200.0)"
    )
    await db_conn.commit()

    r = await client.get("/journal/entry?date=2020-06-01")
    assert r.status_code == 200
    assert r.json()["entry"] is None


@pytest.mark.asyncio
async def test_dashboard_nudge_shown_when_no_log(client):
    r = await client.get("/", headers={"Accept": "text/html"})
    assert "Fill today" in r.text


@pytest.mark.asyncio
async def test_dashboard_nudge_hidden_after_log(client):
    await client.post("/journal", json=LOG)
    r = await client.get("/", headers={"Accept": "text/html"})
    assert "Fill today" not in r.text


@pytest.mark.asyncio
async def test_journal_page_has_date_picker(client):
    r = await client.get("/journal", headers={"Accept": "text/html"})
    assert r.status_code == 200
    assert 'id="j-date"' in r.text
    assert f'value="{TODAY}"' in r.text
