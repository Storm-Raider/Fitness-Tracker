# DB Write Lock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serialize the app's three explicit `BEGIN IMMEDIATE` transactions behind a single `asyncio.Lock` so two requests racing to open a transaction on the shared DB connection wait their turn instead of crashing with `sqlite3.OperationalError: cannot start a transaction within a transaction`.

**Architecture:** Add one module-level `asyncio.Lock()` next to the existing shared `_conn` global in `app/db.py`. Each of the three call sites that currently opens `BEGIN IMMEDIATE` (`app/routes/workouts.py`, `app/routes/import_.py`, `app/routes/auth.py`) wraps its existing transaction block in `async with write_lock:` — no other logic changes. No other route is affected; single-statement writes were never at risk.

**Tech Stack:** FastAPI + aiosqlite (SQLite/SQLCipher), Python's `asyncio.Lock`, pytest + pytest-asyncio.

## Global Constraints

- One single global lock — not one per table or route. This app has low real concurrency (single admin, small friend group on a Raspberry Pi); the goal is correctness for the rare multi-statement write, not throughput under load this app won't see.
- No timeout/backoff on lock acquisition — not solving a problem this app doesn't have.
- The lock must be acquired *before* `BEGIN IMMEDIATE` and released only after `COMMIT`/`ROLLBACK` (including on every exception path) at all three call sites.
- No other route changes — ordinary single-statement `await conn.execute(...)` calls elsewhere are untouched.
- Spec: `docs/superpowers/specs/2026-07-12-db-write-lock-design.md`

---

### Task 1: Add `write_lock`, wire it into all three `BEGIN IMMEDIATE` sites, add regression tests

**Files:**
- Modify: `app/db.py:1-28` (add `import asyncio`, add `write_lock` global)
- Modify: `app/routes/workouts.py:318-346` (`add_set`)
- Modify: `app/routes/import_.py:82-139` (`import_csv`, the `POST /import/csv` route)
- Modify: `app/routes/auth.py:1-16`, `:477-514` (`invite_accept_post`)
- Test: `tests/test_auth.py` (re-add the concurrency test removed during the invite-links feature)
- Test: `tests/test_workouts.py` (new concurrency test)

**Interfaces:**
- Produces: `app.db.write_lock` — a module-level `asyncio.Lock()` instance, importable as `from app.db import write_lock`. No other task depends on this (single-task plan), but this is the name every call site uses.

This is one task because the fix isn't real until it's wired into all three sites — the design's whole point is one lock serializing all of them. Splitting into per-site tasks would let a reviewer approve "the lock exists" while some sites still crash under a race, which isn't a safe partial state.

- [ ] **Step 1: Write the failing invite-accept concurrency test**

Add `import asyncio` as the first import in `tests/test_auth.py` (currently starts with `import pytest`, `from unittest.mock import AsyncMock, patch`, `from app.routes.auth import COOKIE_NAME` — add `import asyncio` before all of these).

Add this test to `tests/test_auth.py`, placed right after `test_invite_accept_rejects_past_cap` (search for that test name to find the spot):

```python
@pytest.mark.asyncio
async def test_invite_accept_concurrent_race_leaves_no_orphan_user(anon_client, db_conn):
    await db_conn.execute(
        "INSERT INTO invite_tokens(token, created_by, expires_at, max_uses) "
        "VALUES ('tok-race', 1, datetime('now','localtime','+7 days'), 1)"
    )
    await db_conn.commit()

    async def _accept(username, email):
        return await anon_client.post(
            "/invite/accept/tok-race",
            data={
                "username": username,
                "email": email,
                "password": "password1",
                "password_confirm": "password1",
            },
        )

    resp_a, resp_b = await asyncio.gather(
        _accept("racer_a", "racera@example.com"),
        _accept("racer_b", "racerb@example.com"),
    )
    statuses = sorted([resp_a.status_code, resp_b.status_code])
    # One request wins (redirect to login), the other loses the race (400).
    assert statuses in ([302, 400], [303, 400])

    async with db_conn.execute(
        "SELECT COUNT(*) AS c FROM users WHERE username IN ('racer_a', 'racer_b')"
    ) as cur:
        row = await cur.fetchone()
    assert row["c"] == 1, "exactly one of the two racing signups should have created a user"

    async with db_conn.execute(
        "SELECT uses_count FROM invite_tokens WHERE token = 'tok-race'"
    ) as cur:
        row = await cur.fetchone()
    assert row["uses_count"] == 1
```

- [ ] **Step 2: Run it to confirm it fails (crashes) without the lock**

Run: `.venv/bin/pytest tests/test_auth.py::test_invite_accept_concurrent_race_leaves_no_orphan_user -v`
Expected: FAIL. Without the lock, the two concurrent requests can both reach `BEGIN IMMEDIATE` before either commits, and one raises `sqlite3.OperationalError: cannot start a transaction within a transaction` instead of a clean 400 — the `statuses in ([302, 400], [303, 400])` assertion fails because one status is a 500 (unhandled error), not 400. Run this 2-3 times if it doesn't fail on the first try — it depends on real async interleaving, so it may occasionally pass by chance before the fix; a majority-fail result confirms the bug.

- [ ] **Step 3: Write the failing workouts concurrency test**

Add `import asyncio` as the first import in `tests/test_workouts.py` (currently just `import pytest` — add `import asyncio` before it).

Add this test anywhere in `tests/test_workouts.py` (e.g. right after `test_create_workout`):

```python
@pytest.mark.asyncio
async def test_add_set_concurrent_requests_do_not_crash_or_corrupt(client, db_conn):
    resp = await client.post("/workouts", json={"notes": None})
    workout_id = resp.json()["id"]

    async with db_conn.execute(
        "SELECT id FROM exercises WHERE name = 'Bench Press'"
    ) as cur:
        exercise_id = (await cur.fetchone())["id"]

    async def _log_set(weight):
        return await client.post(
            f"/workouts/{workout_id}/sets",
            json={"exercise_id": exercise_id, "reps": 5, "weight_kg": weight},
        )

    resp_a, resp_b = await asyncio.gather(_log_set(100.0), _log_set(50.0))
    assert resp_a.status_code == 201
    assert resp_b.status_code == 201

    async with db_conn.execute(
        "SELECT COUNT(*) AS c, MAX(weight_kg) AS max_kg FROM sets WHERE workout_id = ?",
        (workout_id,),
    ) as cur:
        row = await cur.fetchone()
    assert row["c"] == 2, "both concurrent set-logs should be recorded, none lost or crashed"
    assert row["max_kg"] == 100.0
```

- [ ] **Step 4: Run it to confirm it fails without the lock**

Run: `.venv/bin/pytest tests/test_workouts.py::test_add_set_concurrent_requests_do_not_crash_or_corrupt -v`
Expected: FAIL — one of the two concurrent requests gets a 500 instead of 201, for the same `OperationalError` reason as Step 2. As with Step 2, this depends on real interleaving; run it 2-3 times if it doesn't fail immediately.

- [ ] **Step 5: Add `write_lock` to `app/db.py`**

Current top of `app/db.py`:

```python
import os as _os
import sys as _sys
```

Change to:

```python
import asyncio
import os as _os
import sys as _sys
```

Current (`app/db.py`, right after the module docstring-comment area, where `_conn` is declared):

```python
_conn: aiosqlite.Connection | None = None
```

Change to:

```python
_conn: aiosqlite.Connection | None = None

# Serializes the app's explicit multi-statement transactions (BEGIN IMMEDIATE
# blocks in workouts.py, import_.py, auth.py) so two requests racing to open
# one on the single shared connection wait their turn instead of crashing
# with "cannot start a transaction within a transaction". One global lock,
# not per-table/per-route — this app's real concurrency is low enough that
# serializing the rare multi-statement write is the right amount of locking.
write_lock = asyncio.Lock()
```

- [ ] **Step 6: Wire the lock into `workouts.py`'s `add_set`**

Current (`app/routes/workouts.py:318-346`):

```python
    # PR detection + INSERT wrapped in BEGIN IMMEDIATE (prevents async interleaving race)
    async with conn.execute("BEGIN IMMEDIATE"):
        pass
    try:
        async with conn.execute(
            "SELECT MAX(weight_kg) AS max_kg FROM sets WHERE exercise_id = ? AND user_id = ?",
            (body.exercise_id, uid),
        ) as cur:
            prior_row = await cur.fetchone()
        prior_max = prior_row["max_kg"] if prior_row and prior_row["max_kg"] else None

        # A timed hold stores reps=0 so it's excluded from the kg-volume sum
        # (weight × reps); its progression metric is duration, not volume.
        is_time = body.duration_seconds is not None
        reps_to_store = 0 if is_time else body.reps

        async with conn.execute(
            "INSERT INTO sets(workout_id, exercise_id, reps, weight_kg, added_weight_kg, "
            "duration_seconds, notes, rpe, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (workout_id, body.exercise_id, reps_to_store, body.weight_kg, body.added_weight_kg,
             body.duration_seconds, body.notes, body.rpe, uid),
        ) as cur:
            set_id = cur.lastrowid

        await conn.execute("COMMIT")
    except Exception:
        await conn.execute("ROLLBACK")
        raise
```

Replace with (indent the existing body one level deeper inside `async with write_lock:`, no other changes):

```python
    # PR detection + INSERT wrapped in BEGIN IMMEDIATE (prevents async interleaving race)
    # and in write_lock (prevents two requests both reaching BEGIN IMMEDIATE
    # on the shared connection before either commits).
    async with write_lock:
        async with conn.execute("BEGIN IMMEDIATE"):
            pass
        try:
            async with conn.execute(
                "SELECT MAX(weight_kg) AS max_kg FROM sets WHERE exercise_id = ? AND user_id = ?",
                (body.exercise_id, uid),
            ) as cur:
                prior_row = await cur.fetchone()
            prior_max = prior_row["max_kg"] if prior_row and prior_row["max_kg"] else None

            # A timed hold stores reps=0 so it's excluded from the kg-volume sum
            # (weight × reps); its progression metric is duration, not volume.
            is_time = body.duration_seconds is not None
            reps_to_store = 0 if is_time else body.reps

            async with conn.execute(
                "INSERT INTO sets(workout_id, exercise_id, reps, weight_kg, added_weight_kg, "
                "duration_seconds, notes, rpe, user_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (workout_id, body.exercise_id, reps_to_store, body.weight_kg, body.added_weight_kg,
                 body.duration_seconds, body.notes, body.rpe, uid),
            ) as cur:
                set_id = cur.lastrowid

            await conn.execute("COMMIT")
        except Exception:
            await conn.execute("ROLLBACK")
            raise
```

Add the import: current `app/routes/workouts.py` has `from app.db import get_db`. Change to `from app.db import get_db, write_lock`.

- [ ] **Step 7: Wire the lock into `import_.py`'s bulk import**

Current (`app/routes/import_.py:82-139`):

```python
    await conn.execute("BEGIN IMMEDIATE")
    try:
        current_workout_id: int | None = None
        current_workout_key: str | None = None

        for row in rows:
            exercise_name = (row.get("Exercise Name") or "").strip()
            weight_raw = (row.get("Weight") or "").strip()
            reps_raw = (row.get("Reps") or "").strip()

            if not exercise_name or not weight_raw or not reps_raw:
                skipped += 1
                continue

            try:
                weight = float(weight_raw)
                reps = int(float(reps_raw))
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail=f"Non-numeric weight or reps in row: {dict(row)}",
                )

            weight_unit = (row.get("Weight Unit") or "kg").strip().lower()
            if weight_unit == "lbs":
                weight = _lbs_to_kg(weight)

            workout_date, workout_name = _workout_key(row, fmt)
            row_key = f"{workout_date}:{workout_name}"

            if row_key != current_workout_key:
                started_at = workout_date or datetime.now().isoformat()
                async with conn.execute(
                    "INSERT INTO workouts(started_at, ended_at, notes, user_id) "
                    "VALUES (?, ?, ?, ?)",
                    (started_at, started_at, f"Imported: {workout_name}", uid),
                ) as cur:
                    current_workout_id = cur.lastrowid
                current_workout_key = row_key

            exercise_id = await get_or_create_exercise(conn, exercise_name)
            set_notes = (row.get("Notes") or "").strip() or None

            await conn.execute(
                "INSERT INTO sets(workout_id, exercise_id, reps, weight_kg, notes, user_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (current_workout_id, exercise_id, reps, weight, set_notes, uid),
            )
            imported += 1

        await conn.execute("COMMIT")
    except HTTPException:
        await conn.execute("ROLLBACK")
        raise
    except Exception as exc:
        await conn.execute("ROLLBACK")
        logger.exception("CSV import failed: %s", exc)
        raise HTTPException(status_code=500, detail="Import failed — transaction rolled back")

    return {"imported": imported, "skipped": skipped}
```

Replace with (indent the existing body one level deeper inside `async with write_lock:`, no other changes):

```python
    async with write_lock:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            current_workout_id: int | None = None
            current_workout_key: str | None = None

            for row in rows:
                exercise_name = (row.get("Exercise Name") or "").strip()
                weight_raw = (row.get("Weight") or "").strip()
                reps_raw = (row.get("Reps") or "").strip()

                if not exercise_name or not weight_raw or not reps_raw:
                    skipped += 1
                    continue

                try:
                    weight = float(weight_raw)
                    reps = int(float(reps_raw))
                except ValueError:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Non-numeric weight or reps in row: {dict(row)}",
                    )

                weight_unit = (row.get("Weight Unit") or "kg").strip().lower()
                if weight_unit == "lbs":
                    weight = _lbs_to_kg(weight)

                workout_date, workout_name = _workout_key(row, fmt)
                row_key = f"{workout_date}:{workout_name}"

                if row_key != current_workout_key:
                    started_at = workout_date or datetime.now().isoformat()
                    async with conn.execute(
                        "INSERT INTO workouts(started_at, ended_at, notes, user_id) "
                        "VALUES (?, ?, ?, ?)",
                        (started_at, started_at, f"Imported: {workout_name}", uid),
                    ) as cur:
                        current_workout_id = cur.lastrowid
                    current_workout_key = row_key

                exercise_id = await get_or_create_exercise(conn, exercise_name)
                set_notes = (row.get("Notes") or "").strip() or None

                await conn.execute(
                    "INSERT INTO sets(workout_id, exercise_id, reps, weight_kg, notes, user_id) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (current_workout_id, exercise_id, reps, weight, set_notes, uid),
                )
                imported += 1

            await conn.execute("COMMIT")
        except HTTPException:
            await conn.execute("ROLLBACK")
            raise
        except Exception as exc:
            await conn.execute("ROLLBACK")
            logger.exception("CSV import failed: %s", exc)
            raise HTTPException(status_code=500, detail="Import failed — transaction rolled back")

    return {"imported": imported, "skipped": skipped}
```

Note `return {"imported": imported, "skipped": skipped}` stays OUTSIDE (after) the `async with write_lock:` block, same as it was outside the try/except before — only the transaction body moves inside the lock, not the final return.

Add the import: current `app/routes/import_.py` has `from app.db import get_db`. Change to `from app.db import get_db, write_lock`.

- [ ] **Step 8: Wire the lock into `auth.py`'s `invite_accept_post`**

Current (`app/routes/auth.py:477-514`):

```python
    hashed = _hash_password(password)
    async with conn.execute("BEGIN IMMEDIATE"):
        pass
    try:
        async with conn.execute(
            "INSERT INTO users(username, password_hash, is_admin, email) VALUES (?, ?, 0, ?)",
            (username, hashed, email),
        ) as cur:
            new_user_id = cur.lastrowid
        update_cur = await conn.execute(
            "UPDATE invite_tokens "
            "SET uses_count = uses_count + 1, used_at = datetime('now','localtime') "
            "WHERE token = ? AND uses_count < max_uses AND expires_at > datetime('now','localtime')",
            (token,),
        )
        if update_cur.rowcount == 0:
            # Someone else claimed the last remaining slot between our
            # _fetch_valid_invite check and this update — don't leave a user
            # row behind with no valid invite backing it. The rollback for
            # this and any other unexpected error is handled uniformly by
            # the catch-all `except Exception` below.
            raise HTTPException(status_code=400, detail="Invalid or expired invite link")
        await conn.execute("COMMIT")
    except aiosqlite.IntegrityError as exc:
        await conn.execute("ROLLBACK")
        msg = str(exc)
        if "email" in msg:
            errors["email"] = "An account with that email already exists"
        else:
            errors["username"] = "Username is already taken"
        return templates.TemplateResponse(
            request, "invite_accept.html",
            {"token": token, "errors": errors, "form": {"username": username, "email": email}},
            status_code=200,
        )
    except Exception:
        await conn.execute("ROLLBACK")
        raise

    return RedirectResponse(url=f"/login?username={username}", status_code=302)
```

Replace with (indent the existing `try/except` block one level deeper inside `async with write_lock:`; the final `return RedirectResponse(...)` stays outside/after, same as `import_.py`'s pattern in Step 7):

```python
    hashed = _hash_password(password)
    async with write_lock:
        async with conn.execute("BEGIN IMMEDIATE"):
            pass
        try:
            async with conn.execute(
                "INSERT INTO users(username, password_hash, is_admin, email) VALUES (?, ?, 0, ?)",
                (username, hashed, email),
            ) as cur:
                new_user_id = cur.lastrowid
            update_cur = await conn.execute(
                "UPDATE invite_tokens "
                "SET uses_count = uses_count + 1, used_at = datetime('now','localtime') "
                "WHERE token = ? AND uses_count < max_uses AND expires_at > datetime('now','localtime')",
                (token,),
            )
            if update_cur.rowcount == 0:
                # Someone else claimed the last remaining slot between our
                # _fetch_valid_invite check and this update — don't leave a user
                # row behind with no valid invite backing it. The rollback for
                # this and any other unexpected error is handled uniformly by
                # the catch-all `except Exception` below.
                raise HTTPException(status_code=400, detail="Invalid or expired invite link")
            await conn.execute("COMMIT")
        except aiosqlite.IntegrityError as exc:
            await conn.execute("ROLLBACK")
            msg = str(exc)
            if "email" in msg:
                errors["email"] = "An account with that email already exists"
            else:
                errors["username"] = "Username is already taken"
            return templates.TemplateResponse(
                request, "invite_accept.html",
                {"token": token, "errors": errors, "form": {"username": username, "email": email}},
                status_code=200,
            )
        except Exception:
            await conn.execute("ROLLBACK")
            raise

    return RedirectResponse(url=f"/login?username={username}", status_code=302)
```

Note the `return templates.TemplateResponse(...)` inside the `except aiosqlite.IntegrityError` branch stays exactly where it is (inside the `async with write_lock:` block, inside the `except`) — `return` inside an `async with` still releases the lock correctly on the way out, same as any other exit path. Only the final `return RedirectResponse(...)` at the very end (the success path) is outside the lock.

Add the import: current `app/routes/auth.py` has `from app.db import get_db`. Change to `from app.db import get_db, write_lock`.

- [ ] **Step 9: Run the two new tests to confirm they pass**

Run: `.venv/bin/pytest tests/test_auth.py::test_invite_accept_concurrent_race_leaves_no_orphan_user tests/test_workouts.py::test_add_set_concurrent_requests_do_not_crash_or_corrupt -v`
Expected: both PASS. Run each 3 times in a row to check for flakiness (`.venv/bin/pytest tests/test_auth.py::test_invite_accept_concurrent_race_leaves_no_orphan_user -v` three separate times, then the same for the workouts test) — both should pass consistently now that the lock removes the crash window entirely (not just narrows it).

- [ ] **Step 10: Run the full test suite once**

Run: `.venv/bin/pytest -q`
Expected: all tests passing (280 from before this task, plus the 2 new ones = 282), 0 failed, pristine output (no new warnings).

- [ ] **Step 11: Commit**

```bash
git add app/db.py app/routes/workouts.py app/routes/import_.py app/routes/auth.py tests/test_auth.py tests/test_workouts.py
git commit -m "fix(db): serialize BEGIN IMMEDIATE transactions behind a write lock"
```

---

## Self-Review Notes

- **Spec coverage:** The spec's "Fix" section (single global `write_lock`, applied to all three call sites) is Steps 5-8. The spec's "Testing" section (re-add the invite-accept concurrency test, add a workouts.py concurrency test, skip a dedicated import_.py test) is Steps 1-4 and 9. The spec's "Edge cases" section (no deadlock risk, lock held across awaits, no timeout, single global not per-table) are design properties satisfied by the mechanical wiring in Steps 6-8, not separate code to write. The "lock lifetime across tests" edge case is satisfied by construction — `write_lock` is created once at import time and every use goes through `async with`, so no test-specific handling is needed.
- **Placeholder scan:** every step has literal code or an exact command with expected output; no TBDs.
- **Type consistency:** `write_lock` is defined once (Step 5) and referenced by the identical name in Steps 6-8; no renamed variants.
