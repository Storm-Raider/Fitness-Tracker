"""Oura storage and sync (app/utils/oura_sync.py) against a faked Oura."""
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest

from app.utils import oura_sync

TODAY = date(2026, 10, 11)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("OURA_CLIENT_ID", "cid")
    monkeypatch.setenv("OURA_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("PUBLIC_URL", "https://pi.example.ts.net")
    oura_sync.reset_state()
    yield
    oura_sync.reset_state()


class FakeOura:
    """Records requests; serves one readiness/sleep/activity day per requested day."""
    def __init__(self, status=200, refresh_ok=True):
        self.status, self.refresh_ok = status, refresh_ok
        self.requests, self.refresh_tokens_used = [], []
        self.n = 0

    def __call__(self, req: httpx.Request):
        self.requests.append(req)
        if req.url.path.endswith("/oauth/token"):
            form = parse_qs(req.content.decode())
            rt = form.get("refresh_token", [None])[0]
            self.refresh_tokens_used.append(rt)
            if not self.refresh_ok:
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.n += 1
            return httpx.Response(200, json={"access_token": f"A{self.n}", "refresh_token": f"R{self.n}", "expires_in": 86400})
        if req.url.path.endswith("/oauth/revoke"):
            return httpx.Response(200, json={})
        if self.status != 200:
            return httpx.Response(self.status, json={})
        name = req.url.path.rsplit("/", 1)[-1]
        start = date.fromisoformat(req.url.params["start_date"])
        end = date.fromisoformat(req.url.params["end_date"]) - timedelta(days=1)
        days = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
        data = {
            "daily_sleep": [{"day": d, "score": 80} for d in days],
            "daily_readiness": [{"day": d, "score": 75, "temperature_deviation": 0.1} for d in days],
            "daily_activity": [{"day": d, "steps": 8000, "active_calories": 300} for d in days],
            "sleep": [{"day": d, "type": "long_sleep", "total_sleep_duration": 27000, "average_hrv": 50, "lowest_heart_rate": 53} for d in days],
        }[name]
        return httpx.Response(200, json={"data": data, "next_token": None})

    def windows(self, name="daily_sleep"):
        return [(r.url.params["start_date"], r.url.params["end_date"]) for r in self.requests if r.url.path.endswith(name)]


def _client(fake):
    return httpx.AsyncClient(transport=httpx.MockTransport(fake))


async def _connect(db, uid=1, expires_in_h=24, refresh="R0"):
    await oura_sync.save_tokens(db, uid, {
        "access_token": "A0", "refresh_token": refresh,
        "expires_at": (datetime.now() + timedelta(hours=expires_in_h)).isoformat(timespec="seconds")})


async def _days(db, uid=1):
    async with db.execute("SELECT day FROM oura_daily WHERE user_id=? ORDER BY day", (uid,)) as c:
        return [r["day"] for r in await c.fetchall()]


@pytest.mark.asyncio
async def test_first_sync_backfills_90_days_then_refetches_the_last_three(db):
    await _connect(db)
    fake = FakeOura()
    async with _client(fake) as c:
        assert await oura_sync.sync_user(db, c, 1, today=TODAY) == "ok"
        assert fake.windows() == [("2026-07-14", "2026-10-12")]          # 90 days, end inclusive
        days = await _days(db)
        assert len(days) == 90 and days[-1] == "2026-10-11"
        assert await oura_sync.sync_user(db, c, 1, today=TODAY + timedelta(days=1)) == "ok"
    assert fake.windows()[-1] == ("2026-10-09", "2026-10-13")
    row = await oura_sync.day_for(db, 1, "2026-10-11")
    assert (row["sleep_hours"], row["hrv_ms"], row["resting_hr"], row["steps"]) == (7.5, 50, 53, 8000)
    status = await oura_sync.connection(db, 1)
    assert status["last_sync_at"] and status["last_error"] is None and "access_token" not in status


@pytest.mark.asyncio
async def test_an_expiring_token_is_refreshed_and_the_new_pair_stored(db):
    await _connect(db, expires_in_h=0)
    fake = FakeOura()
    async with _client(fake) as c:
        await oura_sync.sync_user(db, c, 1, today=TODAY)
        async with db.execute("SELECT access_token, refresh_token FROM oura_connections WHERE user_id=1") as cur:
            assert tuple(await cur.fetchone()) == ("A1", "R1")
        await db.execute("UPDATE oura_connections SET expires_at = '2000-01-01T00:00:00' WHERE user_id=1")
        await db.commit()
        await oura_sync.sync_user(db, c, 1, today=TODAY)
    assert fake.refresh_tokens_used == ["R0", "R1"]           # each refresh token used once
    data_auth = {r.headers["authorization"] for r in fake.requests if "usercollection" in r.url.path}
    assert data_auth == {"Bearer A1", "Bearer A2"}


@pytest.mark.asyncio
async def test_a_rejected_refresh_asks_the_user_to_reconnect(db):
    await _connect(db, expires_in_h=0)
    async with _client(FakeOura(refresh_ok=False)) as c:
        assert await oura_sync.sync_user(db, c, 1, today=TODAY) == "reconnect"
        assert await oura_sync.sync_user(db, c, 1, today=TODAY) == "reconnect"   # no further calls
    s = await oura_sync.connection(db, 1)
    assert s["needs_reconnect"] == 1 and "no longer accepts" in s["last_error"]


@pytest.mark.asyncio
async def test_a_revoked_access_token_asks_the_user_to_reconnect(db):
    await _connect(db)
    async with _client(FakeOura(status=401)) as c:
        assert await oura_sync.sync_user(db, c, 1, today=TODAY) == "reconnect"


@pytest.mark.asyncio
async def test_oura_down_keeps_the_days_and_records_the_error(db):
    await _connect(db)
    async with _client(FakeOura()) as c:
        await oura_sync.sync_user(db, c, 1, today=TODAY)
    async with _client(FakeOura(status=503)) as c:
        assert await oura_sync.sync_user(db, c, 1, today=TODAY) == "unavailable"
    s = await oura_sync.connection(db, 1)
    assert s["needs_reconnect"] == 0 and "didn't respond" in s["last_error"]
    assert len(await _days(db)) == 90


@pytest.mark.asyncio
async def test_not_connected(db):
    async with _client(FakeOura()) as c:
        assert await oura_sync.sync_user(db, c, 1, today=TODAY) == "not_connected"


@pytest.mark.asyncio
async def test_users_are_isolated_and_disconnect_can_keep_or_delete(db, user_b):
    await _connect(db, uid=1)
    await _connect(db, uid=2)
    fake = FakeOura()
    async with _client(fake) as c:
        await oura_sync.sync_user(db, c, 1, today=TODAY)
        await oura_sync.sync_user(db, c, 2, today=TODAY)
        await oura_sync.disconnect(db, c, 1, keep_data=True)
        assert await oura_sync.connection(db, 1) is None and len(await _days(db, 1)) == 90
        await oura_sync.disconnect(db, c, 2, keep_data=False)
    assert await _days(db, 2) == [] and len(await _days(db, 1)) == 90
    assert any(r.url.path.endswith("/oauth/revoke") for r in fake.requests)


@pytest.mark.asyncio
async def test_today_for_reports_today_or_the_latest_day(db):
    await _connect(db)
    async with _client(FakeOura()) as c:
        await oura_sync.sync_user(db, c, 1, today=TODAY)
    t = await oura_sync.today_for(db, 1, today=TODAY)
    assert t["is_today"] and t["readiness_score"] == 75 and t["band"] == "Good"
    later = await oura_sync.today_for(db, 1, today=TODAY + timedelta(days=2))
    assert later["day"] == "2026-10-11" and not later["is_today"]


@pytest.mark.asyncio
async def test_maybe_sync_runs_in_the_background_once_and_then_backs_off(db):
    import asyncio
    await _connect(db)
    fake = FakeOura()
    async with _client(fake) as c:
        await oura_sync.maybe_sync(db, c, 1)
        await oura_sync.maybe_sync(db, c, 1)                     # in flight / throttled
        await asyncio.gather(*list(oura_sync._tasks))
        n = len(fake.requests)
        await oura_sync.maybe_sync(db, c, 1)                     # just synced: nothing
    assert n > 0 and len(fake.requests) == n
    assert len(fake.windows()) == 1
