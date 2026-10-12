"""Oura routes (app/routes/oura.py) with a faked Oura: no network."""
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.routes import oura as oura_routes
from app.utils import oura_sync
from tests.test_oura_sync import FakeOura


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


@pytest.fixture
def fake():
    f = FakeOura()

    def handler(req):
        if req.url.path.endswith("/oauth/token") and b"authorization_code" in req.content:
            f.requests.append(req)
            return httpx.Response(200, json={"access_token": "AC", "refresh_token": "RC", "expires_in": 86400})
        return f(req)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    oura_routes.set_http_client(client)
    return f


async def _state(client):
    r = await client.get("/oura/connect", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("https://cloud.ouraring.com/oauth/authorize?")
    return parse_qs(urlparse(r.headers["location"]).query)["state"][0]


@pytest.mark.asyncio
async def test_connect_then_callback_stores_tokens_and_backfills(client, anon_client, db, fake):
    state = await _state(client)
    # Oura's redirect back carries no session cookie (SameSite=Strict): anon client.
    r = await anon_client.get(f"/oura/callback?code=c0de&state={state}", follow_redirects=False)
    assert r.status_code == 200 and 'url=/settings?oura=connected' in r.text
    async with db.execute("SELECT access_token, refresh_token FROM oura_connections WHERE user_id=1") as cur:
        assert tuple(await cur.fetchone()) == ("AC", "RC")
    async with db.execute("SELECT COUNT(*) AS n FROM oura_daily WHERE user_id=1") as cur:
        assert (await cur.fetchone())["n"] == 90
    # the state is single use
    again = await anon_client.get(f"/oura/callback?code=c0de&state={state}", follow_redirects=False)
    assert again.status_code == 400


@pytest.mark.asyncio
async def test_forged_or_foreign_state_is_rejected(client, anon_client, user_b_client, db, fake):
    r = await anon_client.get("/oura/callback?code=x&state=not-signed", follow_redirects=False)
    assert r.status_code == 400
    # a state signed for user 2 can't be replayed with user 1's nonce
    state_b = await _state(user_b_client)
    data = oura_routes._signer().loads(state_b)
    forged = oura_routes._signer().dumps({"uid": 1, "n": data["n"]})
    r = await anon_client.get(f"/oura/callback?code=x&state={forged}", follow_redirects=False)
    assert r.status_code == 400
    async with db.execute("SELECT COUNT(*) AS n FROM oura_connections") as cur:
        assert (await cur.fetchone())["n"] == 0


@pytest.mark.asyncio
async def test_denied_on_ouras_page(client, anon_client, db, fake):
    state = await _state(client)
    r = await anon_client.get(f"/oura/callback?error=access_denied&state={state}", follow_redirects=False)
    assert "oura=denied" in r.text
    async with db.execute("SELECT COUNT(*) AS n FROM oura_connections") as cur:
        assert (await cur.fetchone())["n"] == 0


@pytest.mark.asyncio
async def test_everything_is_404_when_not_configured(client, monkeypatch):
    monkeypatch.delenv("OURA_CLIENT_ID")
    assert (await client.get("/oura/connect", follow_redirects=False)).status_code == 404
    assert (await client.post("/oura/sync")).status_code == 404
    assert (await client.get("/api/oura/today")).json() == {"connected": False}


@pytest.mark.asyncio
async def test_sync_now_today_and_disconnect(client, anon_client, db, fake):
    state = await _state(client)
    await anon_client.get(f"/oura/callback?code=c&state={state}", follow_redirects=False)
    r = await client.post("/oura/sync")
    assert r.json()["status"] == "ok" and "access_token" not in r.text
    t = (await client.get("/api/oura/today")).json()
    assert t["connected"] and t["readiness_score"] == 75 and t["band"] == "Good" and t["is_today"]
    assert (await client.post("/oura/disconnect", json={"keep_data": False})).status_code == 200
    assert (await client.get("/api/oura/today")).json() == {"connected": False}
    async with db.execute("SELECT COUNT(*) AS n FROM oura_daily") as cur:
        assert (await cur.fetchone())["n"] == 0


@pytest.mark.asyncio
async def test_another_user_sees_nothing(client, anon_client, user_b_client, fake):
    state = await _state(client)
    await anon_client.get(f"/oura/callback?code=c&state={state}", follow_redirects=False)
    assert (await user_b_client.get("/api/oura/today")).json() == {"connected": False}
    await user_b_client.post("/oura/disconnect", json={"keep_data": False})
    assert (await client.get("/api/oura/today")).json()["connected"] is True
