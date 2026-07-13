# Serialize explicit transactions behind a single write lock — design

**Date:** 2026-07-12
**Status:** Approved, ready for implementation plan
**Tracks:** TODOS.md TODO-EL-5

## Problem

`app/db.py` keeps one module-global `aiosqlite.Connection` (`_conn`) for the
entire process — every request goes through the same connection object, with
no per-request connection and no lock serializing writers. Three call sites
open an explicit multi-statement transaction with `BEGIN IMMEDIATE`:

- `app/routes/workouts.py:318-345` (PR detection + set insert)
- `app/routes/import_.py:82-...` (bulk CSV import)
- `app/routes/auth.py` (`invite_accept_post`, added by the multi-use invite
  links feature)

If two requests both reach their own `BEGIN IMMEDIATE` on the shared
connection before either `COMMIT`s or `ROLLBACK`s, the second raises
`sqlite3.OperationalError: cannot start a transaction within a transaction`
— an unhandled 500 — instead of waiting and then proceeding (or, for
invite-accept, gracefully losing whatever race it was guarding against).

This was discovered via a synthetic `asyncio.gather`-based concurrency test
written while implementing multi-use invite links (2026-07-12,
`docs/superpowers/plans/2026-07-12-multi-use-invite-links.md`, PR #30). That
test was removed at the time as out of scope for that feature; this spec is
the follow-up fix.

## Goal

Two (or more) requests that each try to open an explicit transaction on the
shared connection must serialize — the second one waits for the first's
transaction to close, rather than crashing.

## Fix

Add one module-level lock in `app/db.py`, alongside the existing `_conn`
global:

```python
write_lock = asyncio.Lock()
```

A single global lock, not one per table or route. This app has genuinely low
concurrency (single admin, small friend group on a Raspberry Pi) — the goal
is to serialize the rare multi-statement writes, not to maximize throughput
under load this app will never see. One lock is simplest to reason about and
cannot deadlock against itself (there's nothing to order against).

Routes import it directly (`from app.db import write_lock`) — it's a plain
synchronization primitive, not something that needs FastAPI's dependency
injection or request scoping.

### Applying it to the three call sites

Each site currently opens its transaction with `BEGIN IMMEDIATE`, does its
writes, then `COMMIT`s on success or `ROLLBACK`s in an `except` clause. The
fix is mechanical and identical at each site: wrap the existing
`try/BEGIN IMMEDIATE.../COMMIT/except.../ROLLBACK` block in
`async with write_lock:`, indenting the existing code with no logic changes.
The lock is acquired *before* `BEGIN IMMEDIATE` and released only after
`COMMIT`/`ROLLBACK` (including on the exception paths), so a second request
cannot reach its own `BEGIN IMMEDIATE` until the first request's transaction
is fully closed.

No other route needs to change. The vast majority of routes only ever run
single, non-transactional `await conn.execute(...)` calls — those are
already atomic at the SQLite level and are not affected by, or in contention
with, this lock.

## Testing

- Re-add the synthetic concurrency test removed during the invite-links
  feature: `asyncio.gather` firing two simultaneous `POST
  /invite/accept/{token}` requests against a 1-use invite. Before this fix it
  deterministically crashed with `OperationalError`; after this fix it must
  pass cleanly — one request succeeds, the other is rejected with the
  existing "Invalid or expired invite link" 400 (from the pre-existing
  cap-guard logic in `invite_accept_post`), and exactly one user row exists.
- Add an equivalent concurrency test for `workouts.py`'s add-set endpoint:
  fire two simultaneous set-logs for the same exercise via `asyncio.gather`
  and assert neither request crashes. This also exercises a correctness
  property beyond just "no crash": the second request's PR calculation
  (`MAX(weight_kg)`) must reflect the first request's committed set, not a
  stale pre-lock read — the lock's serialization guarantees this as a side
  effect of closing the crash.
- No dedicated concurrency test for `import_.py` — bulk CSV import is an
  admin-only, one-at-a-time operation in practice. It's covered by the same
  lock mechanism as the other two sites; a dedicated test would be testing
  the identical mechanism a second time for a scenario this app doesn't
  realistically produce.

## Edge cases

- **Deadlock:** not possible — a single lock with no nested acquisition
  across these three call sites (none of them call into one another).
- **Lock held across `await` points:** intentional. The whole point is that
  the lock stays held for the duration of the transaction, including its
  `await conn.execute(...)` calls, so a second request genuinely waits
  rather than interleaving with the first's transaction.
- **No timeout/backoff:** not adding one. Given this app's real traffic (a
  handful of concurrent users at most) and millisecond-scale transactions,
  there's no practical risk of a request hanging noticeably long, and adding
  timeout/backoff machinery would be solving a problem this app doesn't have.
- **Existing single-statement writes:** untouched. They don't open explicit
  transactions, so they never contend with this lock.

## Out of scope

- Moving off the single shared connection to a per-request connection pool
  (the more invasive alternative considered and rejected — bigger
  architectural change, real risk of new bugs given this app's WAL mode +
  SQLCipher setup, for a problem this app's actual usage may never trigger
  at the scale a pool would matter).
- Per-table or per-route lock granularity (unnecessary at this app's scale;
  see "Fix" above).
- Timeout/backoff on lock acquisition (see "Edge cases" above).
