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


async def _log_cardio(client, db_conn, ex_id, **fields):
    """Submit the standalone cardio form (form-encoded, 303 redirect) and
    return the id of the row it created. The endpoint redirects rather than
    returning JSON, so the id comes from the DB."""
    data = {"exercise_id": ex_id, "duration_minutes": 20.0}
    data.update(fields)
    r = await client.post("/cardio", data=data)
    assert r.status_code == 303
    async with db_conn.execute("SELECT id FROM cardio_logs ORDER BY id DESC LIMIT 1") as cur:
        return (await cur.fetchone())["id"]


@pytest.mark.asyncio
async def test_cardio_page_renders(client: AsyncClient, db_conn):
    r = await client.get("/cardio", headers={"Accept": "text/html"})
    assert r.status_code == 200
    assert "Log Session" in r.text
    assert 'action="/cardio"' in r.text  # the log form is present


@pytest.mark.asyncio
async def test_log_cardio_session(client: AsyncClient, db_conn):
    ex_id = await _cardio_exercise_id(db_conn)
    r = await client.post("/cardio", data={
        "exercise_id": ex_id,
        "logged_date": "2026-05-16",
        "duration_minutes": 30.0,
        "distance_km": 5.0,
        "notes": "felt good",
    })
    assert r.status_code == 303  # form submit redirects back to /cardio
    async with db_conn.execute(
        "SELECT duration_minutes, distance_km, notes FROM cardio_logs "
        "WHERE user_id=1 AND exercise_id=?",
        (ex_id,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row["duration_minutes"] == 30.0
    assert row["distance_km"] == 5.0
    assert row["notes"] == "felt good"


@pytest.mark.asyncio
async def test_log_cardio_without_distance(client: AsyncClient, db_conn):
    ex_id = await _cardio_exercise_id(db_conn)
    r = await client.post("/cardio", data={
        "exercise_id": ex_id,
        "logged_date": "2026-05-16",
        "duration_minutes": 45.0,
    })
    assert r.status_code == 303
    async with db_conn.execute(
        "SELECT distance_km, notes FROM cardio_logs WHERE user_id=1 AND duration_minutes=45.0"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row["distance_km"] is None  # blank distance stored as NULL
    assert row["notes"] is None


@pytest.mark.asyncio
async def test_log_cardio_rejects_non_cardio_exercise(client: AsyncClient, db_conn):
    ex_id = await _non_cardio_exercise_id(db_conn)
    r = await client.post("/cardio", data={
        "exercise_id": ex_id,
        "logged_date": "2026-05-16",
        "duration_minutes": 30.0,
    })
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_log_cardio_defaults_date_to_today(client: AsyncClient, db_conn):
    ex_id = await _cardio_exercise_id(db_conn)
    # Omit logged_date — the route should default it to today's local date.
    await _log_cardio(client, db_conn, ex_id, duration_minutes=15.0)
    async with db_conn.execute(
        "SELECT logged_date FROM cardio_logs WHERE user_id=1 AND duration_minutes=15.0"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None and row["logged_date"]  # non-empty date


@pytest.mark.asyncio
async def test_delete_cardio_session(client: AsyncClient, db_conn):
    ex_id = await _cardio_exercise_id(db_conn)
    log_id = await _log_cardio(client, db_conn, ex_id)
    r2 = await client.delete(f"/cardio/{log_id}")
    assert r2.status_code == 200
    async with db_conn.execute(
        "SELECT COUNT(*) AS n FROM cardio_logs WHERE id=?", (log_id,)
    ) as cur:
        assert (await cur.fetchone())["n"] == 0


@pytest.mark.asyncio
async def test_cardio_history_groups_note_with_row(client: AsyncClient, db_conn):
    # Regression: a session's note row must live in the same <tbody> as its
    # data row so the HTMX delete removes both together (no orphan note row).
    ex_id = await _cardio_exercise_id(db_conn)
    log_id = await _log_cardio(client, db_conn, ex_id, notes="tempo intervals")
    r = await client.get("/cardio", headers={"Accept": "text/html"})
    assert r.status_code == 200
    assert f'id="cardio-{log_id}"' in r.text  # per-session tbody keyed by id
    assert "tempo intervals" in r.text


@pytest.mark.asyncio
async def test_delete_cardio_other_user_returns_404(client: AsyncClient, db_conn):
    ex_id = await _cardio_exercise_id(db_conn)
    log_id = await _log_cardio(client, db_conn, ex_id)

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
        cookies={"fitstorm_session": _test_session_token(user_id=intruder_id)},
    ) as intruder:
        r2 = await intruder.delete(f"/cardio/{log_id}")
    assert r2.status_code == 404
