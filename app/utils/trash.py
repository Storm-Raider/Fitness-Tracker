"""
Recoverable deletes ("recycle bin").

Rather than a `deleted_at` column on every table — which would force a
`WHERE deleted_at IS NULL` filter onto every one of the app's analytical
queries over `sets` (stats, PRs, dashboard, coach, achievements, export), and
silently miscount if any were missed — deletes here CAPTURE the full row(s)
into `deleted_items` and remove them from the live tables. Every existing query
stays correct untouched. Undo re-inserts the captured rows.

Each delete returns a (token, label); POST /undo/{token} restores it. Captured
items older than TRASH_TTL_DAYS are purged opportunistically on each delete.
"""

import json
import uuid

import aiosqlite

TRASH_TTL_DAYS = 7


class RestoreError(Exception):
    """Raised when a captured item can no longer be restored cleanly."""


async def _columns(conn: aiosqlite.Connection, table: str) -> list[str]:
    async with conn.execute(f"PRAGMA table_info({table})") as cur:
        return [r["name"] for r in await cur.fetchall()]


async def _row(conn, table: str, row_id: int, uid: int) -> dict | None:
    async with conn.execute(
        f"SELECT * FROM {table} WHERE id = ? AND user_id = ?", (row_id, uid)
    ) as cur:
        r = await cur.fetchone()
    return dict(r) if r else None


async def _exists(conn, table: str, row_id, uid: int) -> bool:
    async with conn.execute(
        f"SELECT 1 FROM {table} WHERE id = ? AND user_id = ?", (row_id, uid)
    ) as cur:
        return await cur.fetchone() is not None


async def _exercise_name(conn, exercise_id) -> str | None:
    if exercise_id is None:
        return None
    async with conn.execute("SELECT name FROM exercises WHERE id = ?", (exercise_id,)) as cur:
        r = await cur.fetchone()
    return r["name"] if r else None


def _insert(table: str, row: dict, cols: list[str]) -> tuple[str, list]:
    """Build an INSERT from a captured row, skipping `id` (so it gets a fresh
    one) and any column that no longer exists in the table."""
    keys = [k for k in row if k in cols and k != "id"]
    placeholders = ", ".join("?" for _ in keys)
    sql = f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({placeholders})"
    return sql, [row[k] for k in keys]


async def _purge_old(conn, uid: int) -> None:
    await conn.execute(
        "DELETE FROM deleted_items WHERE user_id = ? "
        "AND deleted_at < datetime('now', 'localtime', ?)",
        (uid, f"-{TRASH_TTL_DAYS} days"),
    )


async def _store(conn, uid: int, kind: str, label: str, payload: dict) -> str:
    token = uuid.uuid4().hex
    await _purge_old(conn, uid)
    await conn.execute(
        "INSERT INTO deleted_items (token, user_id, kind, label, payload) VALUES (?,?,?,?,?)",
        (token, uid, kind, label, json.dumps(payload)),
    )
    return token


# ── Capture + delete ─────────────────────────────────────────────────
# Callers verify ownership/existence first; these capture the row(s), delete
# them, and return (undo_token, human_label). They do NOT commit — the caller
# commits so the delete + capture land atomically.

async def soft_delete_set(conn, uid: int, set_id: int) -> tuple[str, str]:
    row = await _row(conn, "sets", set_id, uid)
    label = (await _exercise_name(conn, row["exercise_id"]) or "Set") if row else "Set"
    await conn.execute("DELETE FROM sets WHERE id = ? AND user_id = ?", (set_id, uid))
    token = await _store(conn, uid, "set", f"{label} set", {"set": row})
    return token, f"{label} set"


async def soft_delete_cardio(conn, uid: int, log_id: int) -> tuple[str, str]:
    row = await _row(conn, "cardio_logs", log_id, uid)
    label = (await _exercise_name(conn, row["exercise_id"]) or "Cardio") if row else "Cardio"
    await conn.execute("DELETE FROM cardio_logs WHERE id = ? AND user_id = ?", (log_id, uid))
    token = await _store(conn, uid, "cardio", f"{label} session", {"cardio": row})
    return token, f"{label} session"


async def soft_delete_workout(conn, uid: int, workout_id: int) -> tuple[str, str]:
    workout = await _row(conn, "workouts", workout_id, uid)
    async with conn.execute(
        "SELECT * FROM sets WHERE workout_id = ? AND user_id = ?", (workout_id, uid)
    ) as cur:
        sets = [dict(r) for r in await cur.fetchall()]
    async with conn.execute(
        "SELECT * FROM cardio_logs WHERE workout_id = ? AND user_id = ?", (workout_id, uid)
    ) as cur:
        cardio = [dict(r) for r in await cur.fetchall()]

    # Delete children before the parent (no ON DELETE CASCADE in the schema).
    await conn.execute("DELETE FROM sets WHERE workout_id = ? AND user_id = ?", (workout_id, uid))
    await conn.execute("DELETE FROM cardio_logs WHERE workout_id = ? AND user_id = ?", (workout_id, uid))
    await conn.execute("DELETE FROM workouts WHERE id = ? AND user_id = ?", (workout_id, uid))

    n = len(sets)
    label = f"Workout ({n} set{'s' if n != 1 else ''})" if n else "Workout"
    token = await _store(conn, uid, "workout", label,
                         {"workout": workout, "sets": sets, "cardio": cardio})
    return token, label


# ── Restore ──────────────────────────────────────────────────────────

async def restore(conn, uid: int, kind: str, payload: dict) -> str:
    if kind == "set":
        row = payload["set"]
        if not await _exists(conn, "workouts", row.get("workout_id"), uid):
            raise RestoreError("The workout this set belonged to is gone.")
        sql, vals = _insert("sets", row, await _columns(conn, "sets"))
        await conn.execute(sql, vals)
        return "Set restored"

    if kind == "cardio":
        row = payload["cardio"]
        if row.get("workout_id") and not await _exists(conn, "workouts", row["workout_id"], uid):
            raise RestoreError("The workout this cardio belonged to is gone.")
        sql, vals = _insert("cardio_logs", row, await _columns(conn, "cardio_logs"))
        await conn.execute(sql, vals)
        return "Cardio session restored"

    if kind == "workout":
        workout = payload["workout"]
        sql, vals = _insert("workouts", workout, await _columns(conn, "workouts"))
        async with conn.execute(sql, vals) as cur:
            new_wid = cur.lastrowid
        set_cols = await _columns(conn, "sets")
        for s in payload.get("sets", []):
            child = {**s, "workout_id": new_wid}
            sql, vals = _insert("sets", child, set_cols)
            await conn.execute(sql, vals)
        cardio_cols = await _columns(conn, "cardio_logs")
        for c in payload.get("cardio", []):
            child = {**c, "workout_id": new_wid}
            sql, vals = _insert("cardio_logs", child, cardio_cols)
            await conn.execute(sql, vals)
        return "Workout restored"

    raise RestoreError("Unknown item type")
