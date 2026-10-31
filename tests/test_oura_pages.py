"""Oura on the pages: Settings card states and the page-load sync hook."""
import asyncio
from datetime import datetime, timedelta

import pytest

from app.routes import oura as oura_routes
from app.utils import oura_sync
from tests.test_oura_routes import fake, _state  # noqa: F401  (fixture + helper)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("OURA_CLIENT_ID", "cid")
    monkeypatch.setenv("OURA_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("PUBLIC_URL", "https://pi.example.ts.net")
    oura_sync.reset_state()
    oura_routes._pending.clear()
    yield
    oura_routes.set_http_client(None)
    oura_sync.reset_state()


async def _connected(client, anon_client):
    state = await _state(client)
    await anon_client.get(f"/oura/callback?code=c&state={state}", follow_redirects=False)


@pytest.mark.asyncio
async def test_settings_card_not_set_up(client, admin_client, monkeypatch):
    monkeypatch.delenv("OURA_CLIENT_ID")
    page = (await client.get("/settings")).text
    assert "Oura isn't set up on this server." in page and "OURA_CLIENT_ID" not in page
    admin = (await admin_client.get("/settings")).text
    assert "OURA_CLIENT_ID" in admin and "https://pi.example.ts.net/oura/callback" in admin


@pytest.mark.asyncio
async def test_settings_card_connect_then_connected_then_reconnect(client, anon_client, db, fake):
    page = (await client.get("/settings")).text
    assert 'href="/oura/connect"' in page and "Connect Oura" in page
    await _connected(client, anon_client)
    page = (await client.get("/settings?oura=connected")).text
    assert "Your Oura days are in Zenkai" in page and "Last synced" in page and "Sync now" in page
    await db.execute("UPDATE oura_connections SET needs_reconnect = 1")
    await db.commit()
    assert "Reconnect Oura" in (await client.get("/settings")).text


@pytest.mark.asyncio
async def test_settings_error_paths_still_render_the_card(client):
    r = await client.post("/settings/password", data={"current_password": "wrong", "new_password": "x" * 9,
                                                     "new_password_confirm": "x" * 9})
    assert r.status_code == 200 and 'id="oura-card"' in r.text


@pytest.mark.asyncio
async def test_page_load_starts_a_stale_sync(client, anon_client, db, fake):
    await _connected(client, anon_client)
    oura_sync.reset_state()
    old = (datetime.now() - timedelta(hours=2)).isoformat(sep=" ", timespec="seconds")
    await db.execute("UPDATE oura_connections SET last_sync_at = ?", (old,))
    await db.commit()
    n = len(fake.requests)
    await client.get("/achievements/unseen")
    await asyncio.gather(*list(oura_sync._tasks))
    assert len(fake.requests) > n


async def _put_day(db, day, **vals):
    base = {"sleep_score": 80, "readiness_score": 82, "sleep_hours": 7.25, "hrv_ms": 48.0, "resting_hr": 52.0,
            "steps": 9000, "active_calories": 300, "temp_deviation": 0.1}
    base.update(vals)
    await db.execute(f"INSERT OR REPLACE INTO oura_daily(user_id, day, {', '.join(base)}) VALUES (1, ?, {', '.join('?' for _ in base)})",
                     (day, *base.values()))
    await db.commit()


async def _connect_only(db):
    await oura_sync.save_tokens(db, 1, {"access_token": "A", "refresh_token": "R",
                                        "expires_at": (datetime.now() + timedelta(days=1)).isoformat(timespec="seconds")})


@pytest.mark.asyncio
async def test_daily_log_offers_oura_values_for_empty_fields_only(client, db):
    from datetime import date
    today = date.today().isoformat()
    await _connect_only(db)
    await _put_day(db, today)
    page = (await client.get("/journal")).text
    assert '"steps": 9000' in page and '"sleep_hours": 7.25' in page and 'id="oura-tag-sleep"' in page
    # past day via the entry API carries that day's Oura numbers
    await _put_day(db, "2026-01-05", steps=1234)
    data = (await client.get("/journal/entry?date=2026-01-05")).json()
    assert data["oura"]["steps"] == 1234 and data["oura"]["band"] == "Good"
    assert (await client.get("/journal/entry?date=2026-01-06")).json()["oura"] is None


@pytest.mark.asyncio
async def test_home_card_today_waiting_and_absent(client, db):
    from datetime import date
    H = {"Accept": "text/html"}
    assert 'class="oura-card"' not in (await client.get("/", headers=H)).text      # not connected
    await _connect_only(db)
    page = (await client.get("/", headers=H)).text
    assert "Waiting for today's Oura sync." in page
    await _put_day(db, date.today().isoformat())
    page = (await client.get("/", headers=H)).text
    assert "Readiness <b class=\"num\">82</b> <em>Good</em>" in page   # Home shows readiness only (2026-10-31)


@pytest.mark.asyncio
async def test_session_line_before_the_first_set(client, db):
    from datetime import date
    await _connect_only(db)
    await _put_day(db, date.today().isoformat(), readiness_score=64)
    w = (await client.post("/workouts", json={})).json()["id"]
    page = (await client.get(f"/workouts/{w}")).text
    assert 'id="session-oura" >Readiness' in page and "(Fair)" in page          # shown, not hidden
    ex = (await client.post("/exercises", json={"name": "Oura Line Lift"})).json()["id"]
    await client.post(f"/workouts/{w}/sets", json={"exercise_id": ex, "reps": 5, "weight_kg": 50})
    page = (await client.get(f"/workouts/{w}")).text
    assert 'id="session-oura" hidden' in page
