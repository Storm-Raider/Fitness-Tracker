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
