import asyncio
from datetime import datetime, timezone

import pytest

from app.db import write_tx
from app.utils import coach_budget as cb


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


# ── the quota day is the Pacific calendar date ───────────────────────

def test_la_day_rolls_over_at_pacific_midnight_in_summer_and_winter():
    assert cb.la_day(utc(2026, 10, 5, 6, 59)) == "2026-10-04"     # PDT = UTC-7
    assert cb.la_day(utc(2026, 10, 5, 7, 0)) == "2026-10-05"
    assert cb.la_day(utc(2026, 12, 1, 7, 59)) == "2026-11-30"     # PST = UTC-8
    assert cb.la_day(utc(2026, 12, 1, 8, 0)) == "2026-12-01"


def test_la_day_falls_back_to_fixed_offset_without_tz_data(monkeypatch):
    import zoneinfo

    def missing(name):
        raise zoneinfo.ZoneInfoNotFoundError(name)

    monkeypatch.setattr(cb, "_TZ", None)
    monkeypatch.setattr(zoneinfo, "ZoneInfo", missing)
    assert cb.la_day(utc(2026, 12, 1, 7, 59)) == "2026-11-30"
    assert cb.la_day(utc(2026, 12, 1, 8, 0)) == "2026-12-01"
    monkeypatch.setattr(cb, "_TZ", None)      # do not leak the fallback zone into other tests


# ── configuration ────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [(None, 300), ("50", 50), ("  7 ", 7), ("0", 300), ("-4", 300), ("lots", 300), ("", 300)])
def test_daily_cap_parsing(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("COACH_AI_MAX_PER_DAY", raising=False)
    else:
        monkeypatch.setenv("COACH_AI_MAX_PER_DAY", raw)
    assert cb.daily_cap() == expected


@pytest.mark.parametrize("raw,on", [(None, True), ("true", True), ("1", True), ("false", False), ("0", False),
                                     (" OFF ", False), ("no", False), ("yes", True)])
def test_kill_switch(monkeypatch, raw, on):
    if raw is None:
        monkeypatch.delenv("COACH_CHAT_ENABLED", raising=False)
    else:
        monkeypatch.setenv("COACH_CHAT_ENABLED", raw)
    assert cb.chat_enabled() is on


# ── the counter ──────────────────────────────────────────────────────

def test_reserve_counts_up_to_the_cap_then_refuses(monkeypatch):
    cb.reset()
    monkeypatch.setenv("COACH_AI_MAX_PER_DAY", "3")
    for _ in range(3):
        cb.reserve()
    assert cb.count_today() == 3 and cb.at_cap()
    with pytest.raises(cb.DailyCapReached):
        cb.reserve()
    assert cb.count_today() == 3        # a refused request is not counted


@pytest.mark.asyncio
async def test_concurrent_callers_cannot_overshoot_the_cap(monkeypatch):
    cb.reset()
    monkeypatch.setenv("COACH_AI_MAX_PER_DAY", "10")
    outcomes = []

    async def one():
        await asyncio.sleep(0)
        try:
            await cb.on_request()
            outcomes.append("ok")
        except cb.DailyCapReached:
            outcomes.append("capped")

    await asyncio.gather(*(one() for _ in range(40)))
    assert outcomes.count("ok") == 10 and outcomes.count("capped") == 30


def test_a_new_pacific_day_resets_the_count(monkeypatch):
    cb.reset()
    monkeypatch.setenv("COACH_AI_MAX_PER_DAY", "2")
    today = {"day": "2026-10-04"}
    monkeypatch.setattr(cb, "la_day", lambda now=None: today["day"])
    cb.reserve()
    cb.reserve()
    with pytest.raises(cb.DailyCapReached):
        cb.reserve()
    today["day"] = "2026-10-05"
    cb.reserve()                          # next day: fresh allowance
    assert cb.count_today() == 1


# ── persistence ──────────────────────────────────────────────────────

async def _stored(db, day=None):
    async with db.execute("SELECT count FROM coach_usage WHERE day = ?", (day or cb.la_day(),)) as c:
        row = await c.fetchone()
    return row["count"] if row else None


@pytest.mark.asyncio
async def test_flush_persists_only_the_delta(db):
    await cb.ensure_loaded(db)
    for _ in range(3):
        cb.reserve()
    await cb.flush(db)
    assert await _stored(db) == 3
    await cb.flush(db)                    # nothing new: no change
    assert await _stored(db) == 3
    cb.reserve()
    cb.reserve()
    await cb.flush(db)
    assert await _stored(db) == 5


@pytest.mark.asyncio
async def test_the_mark_advances_only_after_commit(db):
    await cb.ensure_loaded(db)
    for _ in range(4):
        cb.reserve()
    with pytest.raises(RuntimeError):
        async with write_tx(db):
            await cb.persist(db)
            raise RuntimeError("the turn's write failed")      # rolled back: the delta is NOT in the table
    assert await _stored(db) is None
    await cb.flush(db)                                          # so the next flush still carries all 4
    assert await _stored(db) == 4


@pytest.mark.asyncio
async def test_the_turns_own_transaction_can_carry_the_delta(db):
    await cb.ensure_loaded(db)
    cb.reserve()
    cb.reserve()
    async with write_tx(db):
        token = await cb.persist(db)
        await db.execute("INSERT INTO coach_notes(user_id, text) VALUES (1, 'rides along')")
    cb.confirm(token)
    assert await _stored(db) == 2
    await cb.flush(db)
    assert await _stored(db) == 2         # confirm() moved the mark: no double count


@pytest.mark.asyncio
async def test_ensure_loaded_seeds_from_the_table_after_a_restart(db):
    await db.execute("INSERT INTO coach_usage(day, count) VALUES (?, 7)", (cb.la_day(),))
    cb.reset()                            # a restart
    await cb.ensure_loaded(db)
    assert cb.count_today() == 7
    cb.reserve()
    await cb.flush(db)
    assert await _stored(db) == 8         # only the one new request was added


@pytest.mark.asyncio
async def test_flush_never_raises(db, monkeypatch):
    cb.reserve()

    async def boom(conn):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(cb, "persist", boom)
    await cb.flush(db)                    # logged, not raised


# ── limiter and single flight ────────────────────────────────────────

@pytest.mark.asyncio
async def test_third_concurrent_turn_waits_then_is_told_busy(monkeypatch):
    cb.reset()
    monkeypatch.setattr(cb, "SLOT_WAIT_SECONDS", 0.05)
    async with cb.chat_slot():
        async with cb.chat_slot():
            with pytest.raises(cb.ChatBusy):
                async with cb.chat_slot():
                    pass
        async with cb.chat_slot():       # a slot freed up
            pass


@pytest.mark.asyncio
async def test_a_waiting_turn_gets_a_slot_when_one_frees(monkeypatch):
    cb.reset()
    monkeypatch.setattr(cb, "SLOT_WAIT_SECONDS", 2.0)
    got = []

    async def holder():
        async with cb.chat_slot():
            await asyncio.sleep(0.05)

    async def waiter():
        async with cb.chat_slot():
            got.append(True)

    await asyncio.gather(holder(), holder(), waiter())
    assert got == [True]


def test_single_flight_per_user_and_released_after_errors():
    cb.reset()
    with cb.single_flight(1):
        with pytest.raises(cb.AlreadyWorking):
            with cb.single_flight(1):
                pass
        with cb.single_flight(2):         # a different user is independent
            pass
    with pytest.raises(ValueError):
        with cb.single_flight(1):
            raise ValueError()
    with cb.single_flight(1):             # released even though the body raised
        pass


def test_report_has_no_user_text_and_counts_outcomes(monkeypatch):
    cb.reset()
    monkeypatch.setenv("COACH_AI_MAX_PER_DAY", "9")
    cb.reserve()
    cb.record("ok")
    cb.record("ok")
    cb.record("quota")
    assert cb.report() == {"day": cb.la_day(), "count": 1, "cap": 9, "persisted": 0,
                           "outcomes_since_boot": {"ok": 2, "quota": 1}}
