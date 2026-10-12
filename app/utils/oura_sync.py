"""Oura storage and sync: tokens in oura_connections, days in oura_daily.

Network calls (token refresh, fetching days, revoke) never run inside write_tx:
the app has one write lock and holding it across a request would stall every
other write. A per-user asyncio.Lock serialises a user's syncs instead, so a
single-use refresh token is never used twice: the new pair is stored before the
lock is released.
"""
import asyncio
import logging
import time
from datetime import date, datetime, timedelta

import aiosqlite
import httpx

from app.db import write_tx
from app.utils import oura

log = logging.getLogger(__name__)

BACKFILL_DAYS = 90       # first connect fetches this many days
REFETCH_DAYS = 3         # Oura revises recent days; re-fetch the last few
STALE_MINUTES = 30       # page-load sync only when the last one is older
REFRESH_MARGIN = timedelta(minutes=5)

_locks: dict[int, asyncio.Lock] = {}
_last_attempt: dict[int, float] = {}
_tasks: set[asyncio.Task] = set()     # keep fire-and-forget syncs alive

_FIELDS = ("sleep_score", "readiness_score", "sleep_hours", "hrv_ms", "resting_hr",
           "steps", "active_calories", "temp_deviation")


def _lock(uid: int) -> asyncio.Lock:
    return _locks.setdefault(uid, asyncio.Lock())


def reset_state() -> None:
    """For tests: forget in-memory locks, throttles and tasks."""
    _locks.clear()
    _last_attempt.clear()
    _tasks.clear()


async def connection(conn: aiosqlite.Connection, uid: int) -> dict | None:
    """The user's connection status, never the tokens."""
    async with conn.execute(
        "SELECT connected_at, last_sync_at, last_error, needs_reconnect "
        "FROM oura_connections WHERE user_id = ?", (uid,),
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def save_tokens(conn: aiosqlite.Connection, uid: int, tokens: dict) -> None:
    async with write_tx(conn):
        await conn.execute(
            """INSERT INTO oura_connections(user_id, access_token, refresh_token, expires_at, scope)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   access_token = excluded.access_token,
                   refresh_token = excluded.refresh_token,
                   expires_at = excluded.expires_at,
                   scope = excluded.scope,
                   needs_reconnect = 0,
                   last_error = NULL""",
            (uid, tokens["access_token"], tokens["refresh_token"], tokens["expires_at"], tokens.get("scope")),
        )


async def _set_status(conn, uid: int, *, error: str | None, reconnect: bool | None = None,
                      synced: bool = False) -> None:
    sets, args = ["last_error = ?"], [error]
    if reconnect is not None:
        sets.append("needs_reconnect = ?")
        args.append(1 if reconnect else 0)
    if synced:
        sets.append("last_sync_at = datetime('now','localtime')")
    async with write_tx(conn):
        await conn.execute(f"UPDATE oura_connections SET {', '.join(sets)} WHERE user_id = ?", (*args, uid))


async def _access_token(conn, client: httpx.AsyncClient, uid: int) -> str:
    """A valid access token; refreshes (and stores the rotated pair) when it is
    about to expire. Call only while holding _lock(uid)."""
    async with conn.execute(
        "SELECT access_token, refresh_token, expires_at FROM oura_connections WHERE user_id = ?", (uid,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise oura.OuraAuthError("not connected")
    try:
        expires = datetime.fromisoformat(row["expires_at"])
    except ValueError:
        expires = datetime.min
    if expires - REFRESH_MARGIN > datetime.now():
        return row["access_token"]
    if not row["refresh_token"]:
        raise oura.OuraAuthError("no refresh token")
    tokens = await oura.refresh(client, row["refresh_token"])
    await save_tokens(conn, uid, tokens)          # before the old refresh token could be reused
    return tokens["access_token"]


async def _sync_start(conn, uid: int, today: date) -> date:
    async with conn.execute("SELECT MAX(day) AS d FROM oura_daily WHERE user_id = ?", (uid,)) as cur:
        last = (await cur.fetchone())["d"]
    floor = today - timedelta(days=BACKFILL_DAYS - 1)
    if not last:
        return floor
    return max(date.fromisoformat(last) - timedelta(days=REFETCH_DAYS - 1), floor)


async def _store_days(conn, uid: int, days: list[dict]) -> None:
    if not days:
        return
    cols = ", ".join(_FIELDS)
    marks = ", ".join("?" for _ in _FIELDS)
    updates = ", ".join(f"{f} = excluded.{f}" for f in _FIELDS)
    async with write_tx(conn):
        for d in days:
            await conn.execute(
                f"""INSERT INTO oura_daily(user_id, day, {cols}) VALUES (?, ?, {marks})
                    ON CONFLICT(user_id, day) DO UPDATE SET {updates},
                        updated_at = datetime('now','localtime')""",
                (uid, d["day"], *(d.get(f) for f in _FIELDS)),
            )


async def sync_user(conn: aiosqlite.Connection, client: httpx.AsyncClient, uid: int,
                    today: date | None = None) -> str:
    """Fetch the user's recent Oura days. Returns "ok", "not_connected",
    "reconnect" (Oura refused the connection) or "unavailable" (try later)."""
    today = today or date.today()
    async with _lock(uid):
        _last_attempt[uid] = time.monotonic()
        status = await connection(conn, uid)
        if status is None:
            return "not_connected"
        if status["needs_reconnect"]:
            return "reconnect"
        try:
            token = await _access_token(conn, client, uid)
            start = await _sync_start(conn, uid, today)
            days = await oura.fetch_days(client, token, start, today)
            await _store_days(conn, uid, days)
            await _set_status(conn, uid, error=None, synced=True)
            return "ok"
        except oura.OuraAuthError as exc:
            log.info("oura: user %s needs to reconnect (%s)", uid, exc)
            await _set_status(conn, uid, error="Oura no longer accepts this connection.", reconnect=True)
            return "reconnect"
        except oura.OuraUnavailable as exc:
            log.info("oura: sync for user %s postponed (%s)", uid, exc)
            await _set_status(conn, uid, error="Oura didn't respond. Zenkai will try again.")
            return "unavailable"
        except oura.OuraError as exc:
            log.warning("oura: sync for user %s failed (%s)", uid, exc)
            await _set_status(conn, uid, error="Oura sent something unexpected. Zenkai will try again.")
            return "unavailable"


def _stale(last_sync_at: str | None) -> bool:
    if not last_sync_at:
        return True
    try:
        return datetime.now() - datetime.fromisoformat(last_sync_at) > timedelta(minutes=STALE_MINUTES)
    except ValueError:
        return True


async def maybe_sync(conn: aiosqlite.Connection, client: httpx.AsyncClient | None, uid: int) -> None:
    """Called on a page load: start a background sync when the last one is
    older than STALE_MINUTES. Never blocks the page, never overlaps a sync,
    and backs off for STALE_MINUTES after any attempt (so an Oura outage isn't
    hammered on every click)."""
    if client is None or not oura.is_configured():
        return
    if time.monotonic() - _last_attempt.get(uid, -1e9) < STALE_MINUTES * 60 or _lock(uid).locked():
        return
    status = await connection(conn, uid)
    if not status or status["needs_reconnect"] or not _stale(status["last_sync_at"]):
        return
    _last_attempt[uid] = time.monotonic()
    task = asyncio.create_task(sync_user(conn, client, uid))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def sync_all(conn: aiosqlite.Connection, client: httpx.AsyncClient) -> None:
    """Every connected user, one after another (the 6-hourly loop)."""
    async with conn.execute("SELECT user_id FROM oura_connections WHERE needs_reconnect = 0") as cur:
        uids = [r["user_id"] for r in await cur.fetchall()]
    for uid in uids:
        await sync_user(conn, client, uid)


async def disconnect(conn: aiosqlite.Connection, client: httpx.AsyncClient | None, uid: int,
                     keep_data: bool) -> None:
    async with _lock(uid):
        async with conn.execute("SELECT access_token FROM oura_connections WHERE user_id = ?", (uid,)) as cur:
            row = await cur.fetchone()
        if row and client is not None:
            await oura.revoke(client, row["access_token"])
        async with write_tx(conn):
            await conn.execute("DELETE FROM oura_connections WHERE user_id = ?", (uid,))
            if not keep_data:
                await conn.execute("DELETE FROM oura_daily WHERE user_id = ?", (uid,))


async def day_for(conn: aiosqlite.Connection, uid: int, day: str) -> dict | None:
    async with conn.execute("SELECT * FROM oura_daily WHERE user_id = ? AND day = ?", (uid, day)) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def today_for(conn: aiosqlite.Connection, uid: int, today: date | None = None) -> dict | None:
    """Today's Oura day, else the most recent one (with is_today False), else None."""
    today = today or date.today()
    async with conn.execute(
        "SELECT * FROM oura_daily WHERE user_id = ? AND day <= ? ORDER BY day DESC LIMIT 1",
        (uid, today.isoformat()),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    d = dict(row)
    d["is_today"] = d["day"] == today.isoformat()
    d["band"] = oura.band(d["readiness_score"])
    return d


# ── Recovery for the coach ───────────────────────────────────────────
# Last 7 days against the last 30 (which include them). A 7-day average needs 3
# days with a value, a 30-day baseline 14, so a few nights off the ring don't
# swing the plan.
RECENT_DAYS, BASELINE_DAYS = 7, 30
_MIN_RECENT, _MIN_BASELINE = 3, 14


def _avg(rows: list[dict], key: str, need: int) -> float | None:
    vals = [r[key] for r in rows if r[key] is not None]
    return sum(vals) / len(vals) if len(vals) >= need else None


def summarise_recovery(rows: list[dict], today: date) -> dict | None:
    """Readiness, sleep, HRV and resting HR trends from oura_daily rows.

    None when there is no Oura day in the last week (not connected, or the ring
    hasn't been worn): stale numbers mustn't steer this week's plan."""
    recent_from = (today - timedelta(days=RECENT_DAYS - 1)).isoformat()
    base_from = (today - timedelta(days=BASELINE_DAYS - 1)).isoformat()
    t = today.isoformat()
    recent = [r for r in rows if recent_from <= r["day"] <= t]
    base = [r for r in rows if base_from <= r["day"] <= t]
    if not recent:
        return None
    today_row = next((r for r in recent if r["day"] == t), None)

    def _vs_baseline(key: str) -> tuple[float, float] | None:
        r7, r30 = _avg(recent, key, _MIN_RECENT), _avg(base, key, _MIN_BASELINE)
        return (r7, r30) if r7 is not None and r30 else None

    readiness = _avg(recent, "readiness_score", _MIN_RECENT)
    sleep = _avg(recent, "sleep_score", _MIN_RECENT)
    hrv = _vs_baseline("hrv_ms")
    rhr = _vs_baseline("resting_hr")
    return {
        "today_readiness": today_row["readiness_score"] if today_row else None,
        "avg_readiness_7d": round(readiness) if readiness is not None else None,
        "avg_readiness_band": oura.band(round(readiness)) if readiness is not None else None,
        "avg_sleep_score_7d": round(sleep) if sleep is not None else None,
        "hrv_vs_baseline_pct": round((hrv[0] / hrv[1] - 1) * 100) if hrv else None,
        "resting_hr_vs_baseline_bpm": round(rhr[0] - rhr[1]) if rhr else None,
    }


async def recovery_for(conn: aiosqlite.Connection, uid: int, today: date | None = None) -> dict | None:
    today = today or date.today()
    async with conn.execute(
        "SELECT day, readiness_score, sleep_score, hrv_ms, resting_hr FROM oura_daily "
        "WHERE user_id = ? AND day BETWEEN ? AND ?",
        (uid, (today - timedelta(days=BASELINE_DAYS - 1)).isoformat(), today.isoformat()),
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return summarise_recovery(rows, today)


async def weekly_recovery(conn: aiosqlite.Connection, uid: int, mondays: list[str]) -> list[dict] | None:
    """Weekly averages for the Analytics chart, one entry per Monday given
    (None values for weeks without data). None when no week has any."""
    async with conn.execute(
        """
        SELECT DATE(day, 'weekday 0', '-6 days') AS week,
               AVG(readiness_score) AS readiness, AVG(sleep_score) AS sleep,
               AVG(hrv_ms) AS hrv, AVG(resting_hr) AS rhr
        FROM oura_daily
        WHERE user_id = ? AND day >= ?
        GROUP BY week
        """,
        (uid, mondays[0]),
    ) as cur:
        by_week = {r["week"]: dict(r) for r in await cur.fetchall()}
    if not by_week:
        return None

    def _r(v):
        return round(v) if v is not None else None
    return [
        {"week_start": m, **{k: _r((by_week.get(m) or {}).get(k)) for k in ("readiness", "sleep", "hrv", "rhr")}}
        for m in mondays
    ]
