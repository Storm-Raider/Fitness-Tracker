"""Regression tests for the 2026-10-10 security review (PR 1).

Each test names the defect it guards: reset links built from the Host header,
bcrypt's 72-byte limit surfacing as a 500, cross-user template/routine reads,
non-finite or out-of-range CSV values, and a replayable undo.
"""
import asyncio
import io
from unittest.mock import AsyncMock, patch

import pytest

LONG_PASSWORD = "x" * 80  # bcrypt 5 raises ValueError above 72 bytes


# ── Password reset links ignore the Host header ──────────────────────────────

@pytest.mark.asyncio
async def test_reset_link_uses_public_url_not_host_header(anon_client, db_conn, monkeypatch):
    monkeypatch.setenv("PUBLIC_URL", "https://zenkai.example:8443/")
    await db_conn.execute("UPDATE users SET email = 'testuser@example.com' WHERE id = 1")
    await db_conn.commit()

    with patch("app.routes.auth.send_email", new_callable=AsyncMock) as mock_send:
        resp = await anon_client.post(
            "/forgot-password",
            data={"email": "testuser@example.com"},
            headers={"Host": "evil.example"},
        )

    assert resp.status_code == 200
    _to, _subject, body_text, body_html = mock_send.call_args.args
    assert "https://zenkai.example:8443/reset-password/" in body_text
    assert "evil.example" not in body_text
    assert "evil.example" not in body_html


@pytest.mark.asyncio
async def test_invite_link_uses_public_url(admin_client, monkeypatch):
    monkeypatch.setenv("PUBLIC_URL", "https://zenkai.example:8443")
    resp = await admin_client.post("/invite", data={"max_uses": 1}, headers={"Host": "evil.example"})
    assert resp.status_code == 200
    assert "https://zenkai.example:8443/invite/accept/" in resp.text
    assert "evil.example" not in resp.text


# ── Passwords longer than bcrypt's 72 bytes ──────────────────────────────────

@pytest.mark.asyncio
async def test_login_with_overlong_password_is_401_not_500(anon_client):
    resp = await anon_client.post(
        "/login", data={"username": "testuser", "password": LONG_PASSWORD}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_reset_password_rejects_overlong_password(anon_client, db_conn):
    await db_conn.execute(
        "INSERT INTO password_reset_tokens(token, user_id, expires_at) "
        "VALUES ('long-tok', 1, datetime('now','localtime','+1 hour'))"
    )
    await db_conn.commit()
    resp = await anon_client.post(
        "/reset-password/long-tok",
        data={"password": LONG_PASSWORD, "password_confirm": LONG_PASSWORD},
    )
    assert resp.status_code == 200
    assert "too long" in resp.text.lower()


@pytest.mark.asyncio
async def test_invite_accept_rejects_overlong_password(anon_client, db_conn):
    await db_conn.execute(
        "INSERT INTO invite_tokens(token, created_by, expires_at, max_uses) "
        "VALUES ('long-inv', 1, datetime('now','localtime','+1 day'), 1)"
    )
    await db_conn.commit()
    resp = await anon_client.post(
        "/invite/accept/long-inv",
        data={
            "username": "longpw",
            "email": "longpw@example.com",
            "password": LONG_PASSWORD,
            "password_confirm": LONG_PASSWORD,
        },
    )
    assert resp.status_code == 200
    assert "too long" in resp.text.lower()


@pytest.mark.asyncio
async def test_settings_password_change_rejects_overlong_password(client):
    resp = await client.post(
        "/settings/password",
        data={
            "current_password": "test-password",
            "new_password": LONG_PASSWORD,
            "new_password_confirm": LONG_PASSWORD,
        },
    )
    assert resp.status_code == 200
    assert "too long" in resp.text.lower()


@pytest.mark.asyncio
async def test_settings_overlong_current_password_is_rejected_not_500(client):
    resp = await client.post(
        "/settings/password",
        data={
            "current_password": LONG_PASSWORD,
            "new_password": "new-password-ok",
            "new_password_confirm": "new-password-ok",
        },
    )
    assert resp.status_code == 200
    assert "incorrect" in resp.text.lower()


# ── ?tpl= and ?routine= are scoped to the current user ───────────────────────

async def _exercise(db, name):
    async with db.execute("INSERT INTO exercises(name) VALUES (?)", (name,)) as cur:
        return cur.lastrowid


@pytest.mark.asyncio
async def test_workout_page_ignores_another_users_template(client, db, user_b):
    ex = await _exercise(db, "Secret Template Lift")
    async with db.execute(
        "INSERT INTO workout_templates(user_id, name) VALUES (2, 'B private')"
    ) as cur:
        tpl = cur.lastrowid
    await db.execute(
        "INSERT INTO workout_template_exercises(template_id, exercise_id, order_idx) VALUES (?,?,0)",
        (tpl, ex),
    )
    await db.commit()

    wid = (await client.post("/workouts", json={})).json()["id"]
    resp = await client.get(f"/workouts/{wid}?tpl={tpl}", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "Secret Template Lift" not in resp.text


@pytest.mark.asyncio
async def test_workout_page_ignores_another_users_routine(client, db, user_b):
    ex = await _exercise(db, "Secret Routine Lift")
    async with db.execute("INSERT INTO routines(name, user_id) VALUES ('B routine', 2)") as cur:
        rid = cur.lastrowid
    await db.execute(
        "INSERT INTO routine_exercises(routine_id, exercise_id, order_idx) VALUES (?,?,0)",
        (rid, ex),
    )
    await db.commit()

    wid = (await client.post("/workouts", json={})).json()["id"]
    resp = await client.get(f"/workouts/{wid}?routine={rid}", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "Secret Routine Lift" not in resp.text


@pytest.mark.asyncio
async def test_workout_page_still_loads_global_routine(client, db):
    ex = await _exercise(db, "Global Routine Lift")
    async with db.execute("INSERT INTO routines(name, user_id) VALUES ('Global', NULL)") as cur:
        rid = cur.lastrowid
    await db.execute(
        "INSERT INTO routine_exercises(routine_id, exercise_id, order_idx) VALUES (?,?,0)",
        (rid, ex),
    )
    await db.commit()

    wid = (await client.post("/workouts", json={})).json()["id"]
    resp = await client.get(f"/workouts/{wid}?routine={rid}", headers={"Accept": "text/html"})
    assert "Global Routine Lift" in resp.text


# ── CSV import rejects values the app can't store or score ───────────────────

def _csv(weight, reps):
    return (
        "Date,Workout Name,Exercise Name,Set Order,Weight,Reps,Weight Unit,Notes\n"
        f"2024-01-15,Push Day,Bench Press,1,{weight},{reps},kg,\n"
    ).encode()


@pytest.mark.asyncio
@pytest.mark.parametrize("weight,reps", [
    ("inf", "5"),
    ("nan", "5"),
    ("-20", "5"),
    ("1001", "5"),
    ("100", "-3"),
    ("100", "1000"),
])
async def test_import_rejects_out_of_range_values(client, db, weight, reps):
    resp = await client.post(
        "/import/csv",
        files={"file": ("w.csv", io.BytesIO(_csv(weight, reps)), "text/csv")},
    )
    assert resp.status_code == 422
    async with db.execute("SELECT COUNT(*) AS n FROM sets WHERE user_id = 1") as cur:
        assert (await cur.fetchone())["n"] == 0


# ── Undo is atomic and single-use under concurrency ──────────────────────────

@pytest.mark.asyncio
async def test_concurrent_undo_restores_once(client, db):
    ex = (await client.post("/exercises", json={"name": "Undo Race Lift"})).json()["id"]
    w = (await client.post("/workouts", json={})).json()["id"]
    s = (await client.post(f"/workouts/{w}/sets",
                           json={"exercise_id": ex, "reps": 5, "weight_kg": 100.0})).json()["id"]
    token = (await client.delete(f"/workouts/{w}/sets/{s}")).headers["X-Undo-Token"]

    r1, r2 = await asyncio.gather(client.post(f"/undo/{token}"), client.post(f"/undo/{token}"))

    assert sorted([r1.status_code, r2.status_code]) == [200, 404]
    async with db.execute(
        "SELECT COUNT(*) AS n FROM sets WHERE workout_id = ? AND exercise_id = ?", (w, ex)
    ) as cur:
        assert (await cur.fetchone())["n"] == 1


@pytest.mark.asyncio
async def test_failed_undo_keeps_token(client, db):
    """A restore that can't happen (parent workout gone) must leave the token usable."""
    ex = (await client.post("/exercises", json={"name": "Orphan Lift"})).json()["id"]
    w = (await client.post("/workouts", json={})).json()["id"]
    s = (await client.post(f"/workouts/{w}/sets",
                           json={"exercise_id": ex, "reps": 5, "weight_kg": 100.0})).json()["id"]
    token = (await client.delete(f"/workouts/{w}/sets/{s}")).headers["X-Undo-Token"]
    await client.delete(f"/workouts/{w}")

    resp = await client.post(f"/undo/{token}")
    assert resp.status_code == 409
    async with db.execute("SELECT COUNT(*) AS n FROM deleted_items WHERE token = ?", (token,)) as cur:
        assert (await cur.fetchone())["n"] == 1
