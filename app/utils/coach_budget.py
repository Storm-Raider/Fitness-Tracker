"""Quota, concurrency and kill-switch state for the AI coach.

Everything here is process-local on purpose: the app runs one uvicorn worker on one
host, so plain module state is both correct and the cheapest thing that works.

* Daily request counter. ONE app-wide count of Gemini requests per America/Los_Angeles
  day (Google's free-tier quota resets at Pacific midnight). It is authoritative IN
  MEMORY on the hot path: gemini.py awaits `on_request` before every HTTP request
  (retries included, failures included), which calls `reserve()` synchronously, so the
  cap can never be overshot by concurrent callers and no database write sits on the
  request path. The count is persisted as a delta to `coach_usage` inside the writer's
  own transaction and `persisted_mark` advances only after that COMMIT; a crash can
  undercount by at most the turn in flight.
* Chat limiter: at most 2 chat turns talk to Gemini at once; a third waits up to 5 s,
  then is told the coach is busy. Separate from the generation lock.
* Single flight: one chat turn per user at a time.
* Kill switch: COACH_CHAT_ENABLED (default on) turns chat off; swap, undo and notes
  viewing keep working.

asyncio primitives bind to a loop, and pytest-asyncio gives each test its own, so
`reset()` (called from conftest like `clear_db()`) drops all of it.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from datetime import datetime, timedelta, timezone

DEFAULT_DAILY_CAP = 300  # placeholder until the real free-tier limit is checked (ai.dev/rate-limit)
SLOT_WAIT_SECONDS = 5.0
LIMITER_SLOTS = 2


class DailyCapReached(Exception):
    """The app-wide daily Gemini request cap is used up."""


class ChatBusy(Exception):
    """No chat limiter slot became free within SLOT_WAIT_SECONDS."""


class AlreadyWorking(Exception):
    """This user already has a chat turn in flight."""


# ── configuration ────────────────────────────────────────────────────

def daily_cap() -> int:
    raw = os.environ.get("COACH_AI_MAX_PER_DAY", "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_DAILY_CAP
    return value if value > 0 else DEFAULT_DAILY_CAP


def chat_enabled() -> bool:
    return os.environ.get("COACH_CHAT_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


# ── the day key ──────────────────────────────────────────────────────

_TZ = None


def _pacific():
    global _TZ
    if _TZ is None:
        try:
            from zoneinfo import ZoneInfo
            _TZ = ZoneInfo("America/Los_Angeles")
        except Exception:  # no tz database on this host
            logging.warning("coach budget: no tz data for America/Los_Angeles; using fixed UTC-8 "
                            "(install the tzdata package for the exact quota-day boundary)")
            _TZ = timezone(timedelta(hours=-8))
    return _TZ


def la_day(now: datetime | None = None) -> str:
    """The America/Los_Angeles calendar date, as YYYY-MM-DD."""
    return (now or datetime.now(timezone.utc)).astimezone(_pacific()).strftime("%Y-%m-%d")


# ── the daily counter ────────────────────────────────────────────────

class _State:
    def __init__(self):
        self.day: str | None = None
        self.count = 0
        self.mark = 0           # how much of `count` is already in coach_usage
        self.loaded = False
        self.kinds: dict[str, int] = {}   # outcome counters since boot, for /coach/usage


_s = _State()
_sem: asyncio.Semaphore | None = None
_inflight: set[int] = set()


def reset() -> None:
    """Forget everything (tests; asyncio primitives are loop-bound)."""
    global _s, _sem
    _s = _State()
    _sem = None
    _inflight.clear()


def _roll_day() -> None:
    today = la_day()
    if _s.day != today:
        _s.day, _s.count, _s.mark = today, 0, 0


async def ensure_loaded(conn) -> None:
    """Seed today's count from coach_usage once per process (and per day). Lazy,
    because the test suite never runs the app lifespan."""
    _roll_day()
    if _s.loaded:
        return
    async with conn.execute("SELECT count FROM coach_usage WHERE day = ?", (_s.day,)) as cur:
        row = await cur.fetchone()
    if not _s.loaded:                      # another task may have loaded while we awaited
        _s.count = max(_s.count, row["count"] if row else 0)
        _s.mark = row["count"] if row else 0
        _s.loaded = True


def reserve() -> None:
    """Count one Gemini request, or raise DailyCapReached. Synchronous on purpose:
    check-and-increment must not interleave with another task."""
    _roll_day()
    if _s.count >= daily_cap():
        raise DailyCapReached()
    _s.count += 1


async def on_request() -> None:
    """The callback handed to gemini.chat_turn_json(on_request=...)."""
    reserve()


def at_cap() -> bool:
    _roll_day()
    return _s.count >= daily_cap()


async def persist(conn) -> tuple[str, int]:
    """Write the not-yet-persisted delta. Call INSIDE the caller's write_tx, then pass the
    returned token to confirm() after that transaction commits."""
    _roll_day()
    day, count = _s.day, _s.count
    delta = count - _s.mark
    if delta > 0:
        await conn.execute(
            "INSERT INTO coach_usage(day, count) VALUES (?, ?) "
            "ON CONFLICT(day) DO UPDATE SET count = count + excluded.count",
            (day, delta),
        )
    return day, count


def confirm(token: tuple[str, int]) -> None:
    """The transaction that carried `token` committed: advance persisted_mark."""
    day, count = token
    if day == _s.day:
        _s.mark = max(_s.mark, count)


async def flush(conn) -> None:
    """Persist the delta in its own transaction. Never raises: losing a counter
    update must not turn a finished turn into an error."""
    from app.db import write_tx
    try:
        async with write_tx(conn):
            token = await persist(conn)
        confirm(token)
    except Exception:
        logging.warning("coach budget: could not persist the usage counter", exc_info=True)


def count_today() -> int:
    _roll_day()
    return _s.count


# ── chat limiter and single flight ───────────────────────────────────

def _semaphore() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(LIMITER_SLOTS)
    return _sem


@contextlib.asynccontextmanager
async def chat_slot():
    """One of LIMITER_SLOTS concurrent chat turns; ChatBusy after SLOT_WAIT_SECONDS."""
    sem = _semaphore()
    try:
        await asyncio.wait_for(sem.acquire(), SLOT_WAIT_SECONDS)
    except asyncio.TimeoutError:
        raise ChatBusy() from None
    try:
        yield
    finally:
        sem.release()


@contextlib.contextmanager
def single_flight(user_id: int):
    """At most one chat turn per user at a time, so one user can hold at most one slot."""
    if user_id in _inflight:
        raise AlreadyWorking()
    _inflight.add(user_id)
    try:
        yield
    finally:
        _inflight.discard(user_id)


# ── observability ────────────────────────────────────────────────────

def record(kind: str) -> None:
    _s.kinds[kind] = _s.kinds.get(kind, 0) + 1


def report() -> dict:
    """What GET /coach/usage returns (admin only): today's count against the cap and the
    outcome counters since boot. Never any user text."""
    _roll_day()
    return {"day": _s.day, "count": _s.count, "cap": daily_cap(),
            "persisted": _s.mark, "outcomes_since_boot": dict(sorted(_s.kinds.items()))}
