import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from tests.conftest import _test_session_token


async def _cardio_exercise_id(db_conn):
    async with db_conn.execute(
        "SELECT id FROM exercises WHERE category='Cardio' ORDER BY id LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    assert row, "No Cardio exercises seeded"
    return row["id"]


async def _non_cardio_exercise_id(db_conn):
    async with db_conn.execute(
        "SELECT id FROM exercises WHERE category != 'Cardio' LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    return row["id"]


@pytest.mark.asyncio
async def test_cardio_page_renders(client: AsyncClient, db_conn):
    r = await client.get("/cardio", headers={"Accept": "text/html"})
    assert r.status_code == 200
    assert "Log Session" in r.text


@pytest.mark.asyncio
async def test_log_cardio_session(client: AsyncClient, db_conn):
    ex_id = await _cardio_exercise_id(db_conn)
    r = await client.post("/cardio", json={
        "exercise_id": ex_id,
        "logged_date": "2026-05-16",
        "duration_minutes": 30.0,
        "distance_km": 5.0,
        "notes": "felt good",
    })
    assert r.status_code == 201
    assert "id" in r.json()


@pytest.mark.asyncio
async def test_log_cardio_without_distance(client: AsyncClient, db_conn):
    ex_id = await _cardio_exercise_id(db_conn)
    r = await client.post("/cardio", json={
        "exercise_id": ex_id,
        "logged_date": "2026-05-16",
        "duration_minutes": 45.0,
    })
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_log_cardio_rejects_non_cardio_exercise(client: AsyncClient, db_conn):
    ex_id = await _non_cardio_exercise_id(db_conn)
    r = await client.post("/cardio", json={
        "exercise_id": ex_id,
        "logged_date": "2026-05-16",
        "duration_minutes": 30.0,
    })
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_delete_cardio_session(client: AsyncClient, db_conn):
    ex_id = await _cardio_exercise_id(db_conn)
    r = await client.post("/cardio", json={
        "exercise_id": ex_id,
        "logged_date": "2026-05-16",
        "duration_minutes": 20.0,
    })
    log_id = r.json()["id"]
    r2 = await client.delete(f"/cardio/{log_id}")
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_delete_cardio_other_user_returns_404(client: AsyncClient, db_conn):
    ex_id = await _cardio_exercise_id(db_conn)
    r = await client.post("/cardio", json={
        "exercise_id": ex_id,
        "logged_date": "2026-05-16",
        "duration_minutes": 25.0,
    })
    log_id = r.json()["id"]

    from app.routes.auth import _hash_password
    pw = _hash_password("pass1234")
    async with db_conn.execute(
        "INSERT INTO users(username, password_hash, is_admin) VALUES (?,?,0)", ("intruder", pw)
    ) as cur:
        intruder_id = cur.lastrowid
    await db_conn.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"fittrack_session": _test_session_token(user_id=intruder_id)},
    ) as intruder:
        r2 = await intruder.delete(f"/cardio/{log_id}")
    assert r2.status_code == 404
