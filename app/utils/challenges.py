"""
Challenge evaluation — lazy, grace-aware reset with no background job.

The completion/reset state of an attempt is derived on every page load:
  - A day is complete when all its NON-optional rules are satisfied.
  - A "workout" rule is satisfied if a workout or cardio was logged that day
    (or it was manually ticked).
  - Locked days are start .. today-2 (yesterday stays editable — the 1-day
    grace). The first incomplete locked day breaks the run → status 'failed'.
  - Reaching the final day with it complete → status 'completed'.

evaluate_attempt() persists any status change. The grid/today helpers build the
view model the templates render.
"""

import json
from datetime import date, timedelta

import aiosqlite

from app.data.challenges import CHALLENGE_INDEX

GRACE_DAYS = 1  # yesterday can still be completed today


def today_local() -> date:
    """Local 'today' as a date — overridable in tests via monkeypatch."""
    import datetime as _dt
    return _dt.date.today()


async def training_dates(conn: aiosqlite.Connection, uid: int) -> set[str]:
    """ISO dates on which the user logged a workout or cardio session."""
    dates: set[str] = set()
    async with conn.execute(
        "SELECT DISTINCT DATE(started_at,'localtime') AS d FROM workouts WHERE user_id=?", (uid,)
    ) as c:
        dates.update(r["d"] for r in await c.fetchall() if r["d"])
    async with conn.execute(
        "SELECT DISTINCT logged_date AS d FROM cardio_logs WHERE user_id=?", (uid,)
    ) as c:
        dates.update(r["d"] for r in await c.fetchall() if r["d"])
    return dates


async def checkins_for(conn: aiosqlite.Connection, attempt_id: int) -> dict[str, dict]:
    """{day_date: {rule_key: bool}} for an attempt."""
    out: dict[str, dict] = {}
    async with conn.execute(
        "SELECT day_date, rules_json FROM challenge_checkins WHERE attempt_id=?", (attempt_id,)
    ) as c:
        for r in await c.fetchall():
            try:
                out[r["day_date"]] = json.loads(r["rules_json"])
            except (json.JSONDecodeError, TypeError):
                out[r["day_date"]] = {}
    return out


def rule_done(rule: dict, day_str: str, checks: dict, train_dates: set[str]) -> bool:
    val = bool(checks.get(rule["key"]))
    if rule["kind"] == "workout":
        return val or (day_str in train_dates)
    return val


def day_complete(template: dict, day_str: str, checks: dict, train_dates: set[str]) -> bool:
    """`checks` is the full {day: {rule: bool}} map for the attempt."""
    day_checks = checks.get(day_str, {})
    return all(
        rule_done(r, day_str, day_checks, train_dates)
        for r in template["rules"] if not r.get("optional")
    )


async def evaluate_attempt(
    conn: aiosqlite.Connection,
    attempt: dict,
    today: date | None = None,
    train_dates: set[str] | None = None,
) -> dict:
    """
    Resolve an attempt's current state, flipping active → failed/completed and
    persisting the change. Returns a view dict used by routes/templates.
    """
    template = CHALLENGE_INDEX.get(attempt["template_key"])
    today = today or today_local()
    if train_dates is None:
        train_dates = await training_dates(conn, attempt["user_id"])
    checks = await checkins_for(conn, attempt["id"])

    start = date.fromisoformat(attempt["started_on"])
    total = attempt["total_days"]
    last_day = start + timedelta(days=total - 1)
    day_n = (today - start).days + 1
    status = attempt["status"]
    ended_on = attempt["ended_on"]

    if template and status == "active":
        locked_end = min(today - timedelta(days=1 + GRACE_DAYS), last_day)
        failed_on = None
        d = start
        while d <= locked_end:
            ds = d.isoformat()
            if not day_complete(template, ds, checks, train_dates):
                failed_on = ds
                break
            d += timedelta(days=1)

        if failed_on:
            status, ended_on = "failed", failed_on
        elif today >= last_day and day_complete(template, last_day.isoformat(), checks, train_dates):
            status, ended_on = "completed", last_day.isoformat()

        if status != attempt["status"]:
            await conn.execute(
                "UPDATE challenge_attempts SET status=?, ended_on=? WHERE id=?",
                (status, ended_on, attempt["id"]),
            )
            await conn.commit()

    return {
        "id": attempt["id"],
        "template_key": attempt["template_key"],
        "title": attempt["title"],
        "total_days": total,
        "status": status,
        "started_on": attempt["started_on"],
        "ended_on": ended_on,
        "day_n": max(1, min(day_n, total)) if status == "completed" else day_n,
        "_template": template,
        "_checks": checks,
        "_train_dates": train_dates,
    }


def today_rules(view: dict, day: date) -> list[dict]:
    """Rule rows for a given editable day (today/yesterday) with done flags."""
    template = view["_template"]
    ds = day.isoformat()
    day_checks = view["_checks"].get(ds, {})
    rows = []
    for r in template["rules"]:
        rows.append({
            **r,
            "done": rule_done(r, ds, day_checks, view["_train_dates"]),
            # workout rule auto-satisfied (vs manually ticked) — for UI hinting
            "auto": r["kind"] == "workout" and ds in view["_train_dates"],
        })
    return rows


def day_cells(view: dict, today: date) -> list[dict]:
    """Calendar grid: one cell per challenge day with its state."""
    template = view["_template"]
    start = date.fromisoformat(view["started_on"])
    cells = []
    for i in range(view["total_days"]):
        d = start + timedelta(days=i)
        ds = d.isoformat()
        if d > today:
            state = "future"
        elif day_complete(template, ds, view["_checks"], view["_train_dates"]):
            state = "done"
        elif d == today:
            state = "today"
        elif d == today - timedelta(days=GRACE_DAYS):
            state = "grace"      # yesterday, still completable
        else:
            state = "missed"
        cells.append({"day": i + 1, "date": ds, "state": state})
    return cells


def rules_done_count(view: dict, day: date) -> tuple[int, int]:
    """(#required rules done, #required rules) for a day — for the progress ring."""
    template = view["_template"]
    ds = day.isoformat()
    day_checks = view["_checks"].get(ds, {})
    required = [r for r in template["rules"] if not r.get("optional")]
    done = sum(1 for r in required if rule_done(r, ds, day_checks, view["_train_dates"]))
    return done, len(required)
