"""Oura Ring API v2 client: the only code in Zenkai that talks to Oura.

  OURA_CLIENT_ID      from the API application registered at cloud.ouraring.com
  OURA_CLIENT_SECRET  (the integration is off unless both are set)
  PUBLIC_URL          the https address Zenkai is opened at; Oura sends the
                      browser back to {PUBLIC_URL}/oura/callback

OAuth2 only (personal access tokens were retired in December 2025). Refresh
tokens are single-use and rotate: callers must store the new pair before the
old refresh token could be used again (app/utils/oura_sync.py does).

Reads the `daily` scope only: daily_sleep, daily_readiness, daily_activity and
sleep periods, merged into one dict per day. Nothing here touches the database.
"""
import os
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

import httpx

AUTHORIZE_URL = "https://cloud.ouraring.com/oauth/authorize"
TOKEN_URL = "https://api.ouraring.com/oauth/token"
REVOKE_URL = "https://api.ouraring.com/oauth/revoke"
API = "https://api.ouraring.com/v2/usercollection"
SCOPE = "daily"
TIMEOUT = httpx.Timeout(20.0, connect=10.0)


class OuraError(Exception):
    """Anything Oura answered that isn't an auth or availability problem."""


class OuraAuthError(OuraError):
    """401/403 or a rejected grant: the user must connect again."""


class OuraUnavailable(OuraError):
    """5xx, 429 or no answer: try again later."""


def client_id() -> str:
    return os.environ.get("OURA_CLIENT_ID", "").strip()


def client_secret() -> str:
    return os.environ.get("OURA_CLIENT_SECRET", "").strip()


def is_configured() -> bool:
    return bool(client_id() and client_secret())


def redirect_uri() -> str:
    return os.environ.get("PUBLIC_URL", "").strip().rstrip("/") + "/oura/callback"


def authorize_url(state: str) -> str:
    return AUTHORIZE_URL + "?" + urlencode({
        "response_type": "code",
        "client_id": client_id(),
        "redirect_uri": redirect_uri(),
        "scope": SCOPE,
        "state": state,
    })


def band(score) -> str | None:
    """Oura's own words for a readiness (or sleep) score."""
    if score is None:
        return None
    if score >= 85:
        return "Optimal"
    if score >= 70:
        return "Good"
    if score >= 60:
        return "Fair"
    return "Pay attention"


def _raise_for(resp: httpx.Response) -> None:
    if resp.status_code in (401, 403):
        raise OuraAuthError(f"Oura refused the token ({resp.status_code})")
    if resp.status_code == 429 or resp.status_code >= 500:
        raise OuraUnavailable(f"Oura is unavailable ({resp.status_code})")
    if resp.status_code >= 400:
        # The token endpoint answers 400 invalid_grant for a used or revoked
        # refresh token / code: that means "connect again", not "retry".
        try:
            err = resp.json().get("error")
        except ValueError:
            err = None
        if err in ("invalid_grant", "unauthorized_client", "invalid_client"):
            raise OuraAuthError(f"Oura rejected the grant ({err})")
        raise OuraError(f"Oura answered {resp.status_code}")


async def _request(client: httpx.AsyncClient, method: str, url: str, **kw) -> httpx.Response:
    try:
        resp = await client.request(method, url, timeout=TIMEOUT, **kw)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise OuraUnavailable(f"No answer from Oura ({type(exc).__name__})") from exc
    _raise_for(resp)
    return resp


def _tokens(payload: dict, previous_refresh: str | None = None) -> dict:
    access = payload.get("access_token")
    if not access:
        raise OuraError("Oura's token answer had no access token")
    expires_in = int(payload.get("expires_in") or 86400)
    return {
        "access_token": access,
        # Rotation: Oura sends a new refresh token; keep the old one only if none came.
        "refresh_token": payload.get("refresh_token") or previous_refresh,
        "expires_at": (datetime.now() + timedelta(seconds=expires_in)).isoformat(timespec="seconds"),
        "scope": payload.get("scope") or SCOPE,
    }


async def exchange_code(client: httpx.AsyncClient, code: str) -> dict:
    resp = await _request(client, "POST", TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(),
        "client_id": client_id(),
        "client_secret": client_secret(),
    })
    return _tokens(resp.json())


async def refresh(client: httpx.AsyncClient, refresh_token: str) -> dict:
    resp = await _request(client, "POST", TOKEN_URL, data={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id(),
        "client_secret": client_secret(),
    })
    return _tokens(resp.json(), previous_refresh=refresh_token)


async def revoke(client: httpx.AsyncClient, access_token: str) -> None:
    """Best effort: a failure here must not stop a local disconnect."""
    try:
        await client.get(REVOKE_URL, params={"access_token": access_token}, timeout=TIMEOUT)
    except httpx.HTTPError:
        pass


async def _collection(client, token: str, name: str, start: date, end: date) -> list[dict]:
    """Every document of one usercollection between start and end (inclusive),
    following Oura's next_token paging."""
    out: list[dict] = []
    params = {"start_date": start.isoformat(), "end_date": (end + timedelta(days=1)).isoformat()}
    for _ in range(50):                       # paging guard
        resp = await _request(client, "GET", f"{API}/{name}", params=params,
                              headers={"Authorization": f"Bearer {token}"})
        body = resp.json()
        out.extend(body.get("data") or [])
        nxt = body.get("next_token")
        if not nxt:
            break
        params = {**params, "next_token": nxt}
    return out


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


async def fetch_days(client: httpx.AsyncClient, access_token: str, start: date, end: date) -> list[dict]:
    """One dict per day from start to end that Oura has anything for:
    day, sleep_score, readiness_score, sleep_hours, hrv_ms, resting_hr, steps,
    active_calories, temp_deviation. Missing values are None."""
    days: dict[str, dict] = {}

    def row(day: str) -> dict:
        return days.setdefault(day, {
            "day": day, "sleep_score": None, "readiness_score": None, "sleep_hours": None,
            "hrv_ms": None, "resting_hr": None, "steps": None, "active_calories": None,
            "temp_deviation": None,
        })

    for d in await _collection(client, access_token, "daily_sleep", start, end):
        if d.get("day"):
            row(d["day"])["sleep_score"] = _num(d.get("score"))
    for d in await _collection(client, access_token, "daily_readiness", start, end):
        if d.get("day"):
            r = row(d["day"])
            r["readiness_score"] = _num(d.get("score"))
            r["temp_deviation"] = _num(d.get("temperature_deviation"))
    for d in await _collection(client, access_token, "daily_activity", start, end):
        if d.get("day"):
            r = row(d["day"])
            r["steps"] = _num(d.get("steps"))
            r["active_calories"] = _num(d.get("active_calories"))
    # Sleep periods: the night's main sleep ("long_sleep") carries duration,
    # HRV and the lowest heart rate (Oura's resting heart rate). Naps are skipped;
    # if a day has several long sleeps, the longest wins.
    best: dict[str, dict] = {}
    for p in await _collection(client, access_token, "sleep", start, end):
        day = p.get("day")
        if not day or p.get("type") != "long_sleep":
            continue
        dur = _num(p.get("total_sleep_duration")) or 0
        if day not in best or dur > (_num(best[day].get("total_sleep_duration")) or 0):
            best[day] = p
    for day, p in best.items():
        r = row(day)
        dur = _num(p.get("total_sleep_duration"))
        r["sleep_hours"] = round(dur / 3600, 2) if dur is not None else None
        r["hrv_ms"] = _num(p.get("average_hrv"))
        r["resting_hr"] = _num(p.get("lowest_heart_rate"))

    lo, hi = start.isoformat(), end.isoformat()
    return [days[k] for k in sorted(days) if lo <= k <= hi]
