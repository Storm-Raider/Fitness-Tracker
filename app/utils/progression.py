"""Weight suggestions between plans: the plan's own progression rule, checked
against what the athlete logged. Deterministic, no model call.

A plan exercise is ready when, in each of its last two finished sessions, at
least the planned number of sets reached the top of the rep range at the target
load or more, none was rated Max (RPE 10), and no set note of it mentions pain.
The step is the one coach_program.progression_for writes: +2.5 kg (+5 kg for a
barbell or machine squat or hinge), one dumbbell (+2.5 kg), +1 rep a set for an
unloaded bodyweight move, +10 s for a timed hold. With no load target (a lift
the athlete hadn't done when the plan was made), it steps up from the lighter
of the two sessions' working weights.

Failure isolation: a malformed target yields no suggestion, never an error."""
import re
from datetime import date, timedelta

import aiosqlite

from app.utils.coach_program import is_hold, is_timed, pattern_of, top_reps
from app.utils.training_profile import has_pain_words

SNOOZE_DAYS = 14
_LOOKBACK_DAYS = 120
_MAX_RPE = 10


def _top_seconds(reps: str) -> int | None:
    """Top of a time range: '30-45s' -> 45, '1 min' -> 60."""
    nums = [int(n) for n in re.findall(r"\d+", str(reps or ""))]
    if not nums:
        return None
    return max(nums) * (60 if "min" in str(reps).lower() else 1)


def _shift(reps: str, by: int) -> str:
    """'8-12' -> '9-13', '30-45s' -> '40-55s': every number moved, the rest kept."""
    return re.sub(r"\d+", lambda m: str(int(m.group()) + by), str(reps))


def load_step(name: str, equipment: str) -> float:
    if equipment in ("Dumbbell", "Kettlebell"):
        return 2.5
    if pattern_of(name) in ("squat", "hinge") and equipment in ("Barbell", "Machine"):
        return 5.0
    return 2.5


def _kind(item: dict) -> str | None:
    """'load', 'reps' or 'secs' for this plan exercise; None when its target can't be checked."""
    t = item.get("target") or {}
    reps = t.get("reps")
    if not isinstance(t.get("sets"), int) or t["sets"] < 1 or not reps:
        return None
    if is_hold(item.get("name", "")) or is_timed(reps):
        return "secs" if _top_seconds(reps) else None
    if top_reps(reps) is None:
        return None
    if (item.get("equipment") or "") == "Bodyweight" and not t.get("load_kg"):
        return "reps"
    return "load"


async def _snoozed(conn: aiosqlite.Connection, uid: int) -> set[int]:
    async with conn.execute(
        "SELECT exercise_id FROM progression_snoozes WHERE user_id = ? AND until > ?",
        (uid, date.today().isoformat()),
    ) as cur:
        return {r["exercise_id"] for r in await cur.fetchall()}


async def _last_two(conn: aiosqlite.Connection, uid: int, ex_ids: list[int]) -> dict[int, list[list[dict]]]:
    """Per exercise, the sets of its last two finished sessions (newest first)."""
    marks = ",".join("?" * len(ex_ids))
    since = (date.today() - timedelta(days=_LOOKBACK_DAYS)).isoformat()
    async with conn.execute(
        f"""
        SELECT s.exercise_id, s.workout_id, s.weight_kg, s.reps, s.rpe, s.notes,
               s.added_weight_kg, s.duration_seconds
        FROM sets s JOIN workouts w ON w.id = s.workout_id
        WHERE s.user_id = ? AND w.user_id = ? AND w.ended_at IS NOT NULL
          AND DATE(w.started_at) >= ? AND s.exercise_id IN ({marks})
        ORDER BY w.started_at DESC, w.id DESC, s.id
        """,
        (uid, uid, since, *ex_ids),
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    out: dict[int, list[list[dict]]] = {}
    order: dict[int, list[int]] = {}
    for r in rows:
        seen = order.setdefault(r["exercise_id"], [])
        if r["workout_id"] not in seen:
            if len(seen) == 2:
                continue
            seen.append(r["workout_id"])
            out.setdefault(r["exercise_id"], []).append([])
        out[r["exercise_id"]][seen.index(r["workout_id"])].append(r)
    return out


def _check(item: dict, kind: str, sessions: list[list[dict]]) -> dict | None:
    t = item["target"]
    n, reps = t["sets"], t["reps"]
    if len(sessions) < 2:
        return None
    for sets in sessions:
        if any((s["rpe"] or 0) >= _MAX_RPE or (s["notes"] and has_pain_words(s["notes"])) for s in sets):
            return None
    base = {"exercise_id": item["exercise_id"], "name": item["name"], "kind": kind, "reps": reps}

    if kind == "secs":
        top = _top_seconds(reps)
        if all(sum(1 for s in sets if (s["duration_seconds"] or 0) >= top) >= n for sets in sessions):
            return {**base, "to_reps": _shift(reps, 10)}
        return None

    top = top_reps(reps)
    if kind == "reps":
        if all(sum(1 for s in sets if (s["reps"] or 0) >= top) >= n for sets in sessions):
            return {**base, "to_reps": _shift(reps, 1)}
        return None

    # Loaded: a bodyweight move's load is what was added to it.
    def load(s):
        return (s["added_weight_kg"] or 0) if item.get("equipment") == "Bodyweight" else (s["weight_kg"] or 0)

    target = t.get("load_kg")
    if target:
        ref = float(target)
    else:
        # The working weight of a session: the n-th heaviest set that reached the top.
        working = []
        for sets in sessions:
            loads = sorted((load(s) for s in sets if (s["reps"] or 0) >= top), reverse=True)
            if len(loads) < n:
                return None
            working.append(loads[n - 1])
        ref = min(working)
        if ref <= 0:
            return None
    if all(sum(1 for s in sets if (s["reps"] or 0) >= top and load(s) >= ref - 0.01) >= n for sets in sessions):
        to = round((ref + load_step(item["name"], item.get("equipment") or "")) * 2) / 2
        return {**base, "from_kg": ref, "to_kg": to}
    return None


async def check_items(conn: aiosqlite.Connection, uid: int, items: list[dict]) -> list[dict | None]:
    """A suggestion (or None) for each plan exercise, in order. Each item is
    {exercise_id, name, equipment, target}; a target is the plan's {sets, reps,
    load_kg, ...}. Exercises snoozed with Not yet get None."""
    kinds = []
    for item in items:
        try:
            kinds.append(_kind(item))
        except (TypeError, ValueError, AttributeError):
            kinds.append(None)
    if not any(kinds):
        return [None] * len(items)
    snoozed = await _snoozed(conn, uid)
    ids = sorted({i["exercise_id"] for i, k in zip(items, kinds) if k and i["exercise_id"] not in snoozed})
    history = await _last_two(conn, uid, ids) if ids else {}
    return [
        _check(item, kind, history.get(item["exercise_id"], []))
        if kind and item["exercise_id"] not in snoozed else None
        for item, kind in zip(items, kinds)
    ]


async def ready(conn: aiosqlite.Connection, uid: int, items: list[dict]) -> dict[int, dict]:
    """check_items keyed by exercise id (the first item of an exercise wins)."""
    out: dict[int, dict] = {}
    for r in await check_items(conn, uid, items):
        if r and r["exercise_id"] not in out:
            out[r["exercise_id"]] = r
    return out


async def snooze(conn: aiosqlite.Connection, uid: int, exercise_id: int) -> None:
    """Not yet: hide this exercise's suggestion for SNOOZE_DAYS (the caller commits)."""
    until = (date.today() + timedelta(days=SNOOZE_DAYS)).isoformat()
    await conn.execute(
        "INSERT INTO progression_snoozes(user_id, exercise_id, until) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, exercise_id) DO UPDATE SET until = excluded.until",
        (uid, exercise_id, until),
    )


def apply_to_target(target: dict, suggestion: dict) -> dict:
    """The target after Apply: a heavier load, or the rep or time range moved up."""
    t = dict(target)
    if suggestion["kind"] == "load":
        t["load_kg"] = suggestion["to_kg"]
    else:
        t["reps"] = suggestion["to_reps"]
    return t
