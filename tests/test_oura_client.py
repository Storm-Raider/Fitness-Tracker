"""The Oura client (app/utils/oura.py) against a faked Oura: no network."""
from datetime import date
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.utils import oura


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("OURA_CLIENT_ID", "cid")
    monkeypatch.setenv("OURA_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("PUBLIC_URL", "https://pi.example.ts.net/")


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_configured_only_with_both_settings(monkeypatch):
    assert oura.is_configured()
    monkeypatch.setenv("OURA_CLIENT_SECRET", " ")
    assert not oura.is_configured()


def test_authorize_url_asks_for_daily_only_and_returns_to_public_url():
    q = parse_qs(urlparse(oura.authorize_url("st8")).query)
    assert q["client_id"] == ["cid"] and q["scope"] == ["daily"] and q["state"] == ["st8"]
    assert q["redirect_uri"] == ["https://pi.example.ts.net/oura/callback"]
    assert q["response_type"] == ["code"]


@pytest.mark.asyncio
async def test_code_exchange_and_refresh_rotation():
    seen = []

    def handler(req):
        form = parse_qs(req.content.decode())
        seen.append(form)
        if form["grant_type"] == ["authorization_code"]:
            return httpx.Response(200, json={"access_token": "A1", "refresh_token": "R1", "expires_in": 86400})
        return httpx.Response(200, json={"access_token": "A2", "refresh_token": "R2", "expires_in": 3600})

    async with _client(handler) as c:
        t1 = await oura.exchange_code(c, "code123")
        t2 = await oura.refresh(c, t1["refresh_token"])
    assert (t1["access_token"], t1["refresh_token"]) == ("A1", "R1")
    assert (t2["access_token"], t2["refresh_token"]) == ("A2", "R2")
    assert seen[0]["redirect_uri"] == ["https://pi.example.ts.net/oura/callback"]
    assert seen[1]["refresh_token"] == ["R1"] and seen[1]["client_secret"] == ["csecret"]


@pytest.mark.asyncio
async def test_refresh_keeps_the_old_token_only_if_oura_sends_none():
    async with _client(lambda r: httpx.Response(200, json={"access_token": "A2"})) as c:
        t = await oura.refresh(c, "R1")
    assert t["refresh_token"] == "R1"


@pytest.mark.parametrize("status,body,exc", [
    (401, {}, oura.OuraAuthError),
    (403, {}, oura.OuraAuthError),
    (400, {"error": "invalid_grant"}, oura.OuraAuthError),
    (429, {}, oura.OuraUnavailable),
    (503, {}, oura.OuraUnavailable),
    (400, {"error": "weird"}, oura.OuraError),
])
@pytest.mark.asyncio
async def test_errors_map_to_reconnect_or_retry(status, body, exc):
    async with _client(lambda r: httpx.Response(status, json=body)) as c:
        with pytest.raises(exc):
            await oura.refresh(c, "R1")


@pytest.mark.asyncio
async def test_no_answer_is_unavailable():
    def handler(req):
        raise httpx.ConnectTimeout("slow")
    async with _client(handler) as c:
        with pytest.raises(oura.OuraUnavailable):
            await oura.fetch_days(c, "A", date(2026, 10, 1), date(2026, 10, 2))


DATA = {
    "daily_sleep": [{"day": "2026-10-10", "score": 76}, {"day": "2026-10-11", "score": 81}],
    "daily_readiness": [{"day": "2026-10-10", "score": 82, "temperature_deviation": -0.2}],
    "daily_activity": [{"day": "2026-10-10", "steps": 9123, "active_calories": 410}],
    "sleep": [
        {"day": "2026-10-10", "type": "long_sleep", "total_sleep_duration": 26100, "average_hrv": 48, "lowest_heart_rate": 52},
        {"day": "2026-10-10", "type": "late_nap", "total_sleep_duration": 1800, "average_hrv": 70, "lowest_heart_rate": 60},
        {"day": "2026-10-11", "type": "long_sleep", "total_sleep_duration": 20000, "average_hrv": 40, "lowest_heart_rate": 55},
        {"day": "2026-10-11", "type": "long_sleep", "total_sleep_duration": 9000, "average_hrv": 90, "lowest_heart_rate": 50},
    ],
}


@pytest.mark.asyncio
async def test_fetch_days_merges_endpoints_per_day_and_picks_the_main_sleep():
    calls = []

    def handler(req):
        name = req.url.path.rsplit("/", 1)[-1]
        calls.append((name, req.headers["authorization"], dict(req.url.params)))
        return httpx.Response(200, json={"data": DATA[name], "next_token": None})

    async with _client(handler) as c:
        days = await oura.fetch_days(c, "TOKEN", date(2026, 10, 10), date(2026, 10, 11))
    assert {n for n, *_ in calls} == {"daily_sleep", "daily_readiness", "daily_activity", "sleep"}
    assert all(auth == "Bearer TOKEN" for _, auth, _ in calls)
    assert calls[0][2] == {"start_date": "2026-10-10", "end_date": "2026-10-12"}
    d10, d11 = days
    assert d10 == {"day": "2026-10-10", "sleep_score": 76, "readiness_score": 82, "sleep_hours": 7.25,
                   "hrv_ms": 48, "resting_hr": 52, "steps": 9123, "active_calories": 410, "temp_deviation": -0.2}
    # the longer long_sleep wins; days with no readiness/activity keep None
    assert (d11["sleep_hours"], d11["hrv_ms"], d11["readiness_score"], d11["steps"]) == (5.56, 40, None, None)


@pytest.mark.asyncio
async def test_fetch_days_follows_paging():
    def handler(req):
        name = req.url.path.rsplit("/", 1)[-1]
        if name == "daily_activity" and "next_token" not in req.url.params:
            return httpx.Response(200, json={"data": [{"day": "2026-10-01", "steps": 1}], "next_token": "p2"})
        if name == "daily_activity":
            return httpx.Response(200, json={"data": [{"day": "2026-10-02", "steps": 2}], "next_token": None})
        return httpx.Response(200, json={"data": [], "next_token": None})

    async with _client(handler) as c:
        days = await oura.fetch_days(c, "T", date(2026, 10, 1), date(2026, 10, 2))
    assert [(d["day"], d["steps"]) for d in days] == [("2026-10-01", 1), ("2026-10-02", 2)]


@pytest.mark.parametrize("score,word", [(85, "Optimal"), (84, "Good"), (70, "Good"), (69, "Fair"),
                                        (60, "Fair"), (59, "Pay attention"), (None, None)])
def test_band_words(score, word):
    assert oura.band(score) == word
