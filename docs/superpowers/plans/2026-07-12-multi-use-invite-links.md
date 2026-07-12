# Multi-use Invite Links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a single admin-generated invite link be used for a fixed number of signups (admin-chosen at generation time) instead of exactly one.

**Architecture:** Extend the existing `invite_tokens` table with `max_uses`/`uses_count` counter columns (dropping the now-meaningless single-user `used_by` column), switch the token-validity check from "has it been used" to "is the counter under the cap," and increment the counter atomically inside the same guarded `UPDATE` that re-validates the token — so concurrent signups on a nearly-exhausted link can't push the count past `max_uses`.

**Tech Stack:** FastAPI + aiosqlite (SQLite/SQLCipher), Jinja2 templates, HTMX, pytest + pytest-asyncio.

## Global Constraints

- New invite links: **7-day expiry** (was 48 hours), **default `max_uses = 5`**, admin-editable per link, validated to an integer between 1 and 50.
- Existing invite link rows keep one-time-use behavior after migration (`max_uses` defaults to `1` for pre-existing rows).
- No per-signup audit trail — a plain remaining-uses counter only (no new table).
- The shared `_fetch_valid_token` helper (also used by `password_reset_tokens`) must not be touched — invite tokens get their own dedicated validity check.
- `DELETE /invite/{token}` (revoke) keeps its current behavior: hard-delete the row regardless of `uses_count`.
- The invitee-facing `invite_accept.html` page is unchanged — no remaining-uses display there.
- Spec: `docs/superpowers/specs/2026-07-12-multi-use-invite-links-design.md`

---

### Task 1: Schema + migration for `max_uses` / `uses_count`, drop `used_by`

**Files:**
- Modify: `schema.sql:68-75`
- Modify: `app/db.py:235` (append new migrations after this line)
- Test: `tests/test_db.py` (new test, following the existing `test_migration_columns_exist` pattern at line 88)

**Interfaces:**
- Produces: `invite_tokens` table now has columns `max_uses INTEGER NOT NULL DEFAULT 1`, `uses_count INTEGER NOT NULL DEFAULT 0`; `used_by` no longer exists. Later tasks (2, 3) read/write these columns directly via raw SQL — no ORM layer to keep in sync.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_db.py` (new test function, place it right after `test_migration_columns_exist` at line 97):

```python
@pytest.mark.asyncio
async def test_invite_tokens_multi_use_columns():
    conn = await open_db(":memory:")
    try:
        async with conn.execute("PRAGMA table_info(invite_tokens)") as cur:
            columns = {r["name"] for r in await cur.fetchall()}
        for col in ("max_uses", "uses_count"):
            assert col in columns, f"Column '{col}' missing from invite_tokens table"
        assert "used_by" not in columns, "Dead column 'used_by' should have been dropped"
    finally:
        await conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_db.py::test_invite_tokens_multi_use_columns -v`
Expected: FAIL — `max_uses`/`uses_count` not yet in `invite_tokens`, and `used_by` still present.

- [ ] **Step 3: Update `schema.sql`**

Current (`schema.sql:68-75`):

```sql
CREATE TABLE IF NOT EXISTS invite_tokens (
    token      TEXT     PRIMARY KEY,
    created_by INTEGER  NOT NULL REFERENCES users(id),
    created_at DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    expires_at DATETIME NOT NULL,
    used_at    DATETIME NULL,
    used_by    INTEGER  NULL REFERENCES users(id)
);
```

Replace with:

```sql
CREATE TABLE IF NOT EXISTS invite_tokens (
    token      TEXT     PRIMARY KEY,
    created_by INTEGER  NOT NULL REFERENCES users(id),
    created_at DATETIME NOT NULL DEFAULT (datetime('now','localtime')),
    expires_at DATETIME NOT NULL,
    used_at    DATETIME NULL,
    max_uses   INTEGER  NOT NULL DEFAULT 1,
    uses_count INTEGER  NOT NULL DEFAULT 0
);
```

This is what a **fresh** install gets. Existing installs get the same end state via the migration in the next step.

- [ ] **Step 4: Append migrations to `app/db.py`**

Current tail of `_MIGRATIONS` (`app/db.py:229-236`):

```python
    # idx=46 slot was consumed by a second pass of the orphaned-routines cleanup
    # after an index-shift incident. This no-op placeholder preserves index parity
    # so idx=47 is the true new migration on already-migrated databases.
    "SELECT 1",
    # Coach plan lifecycle: 'draft' = generated but not yet confirmed by user;
    # 'saved' = confirmed, routines created. Default keeps existing rows as saved.
    "ALTER TABLE coach_plans ADD COLUMN status TEXT NOT NULL DEFAULT 'saved'",
]
```

Change to (append 3 new entries, do not touch anything above them):

```python
    # idx=46 slot was consumed by a second pass of the orphaned-routines cleanup
    # after an index-shift incident. This no-op placeholder preserves index parity
    # so idx=47 is the true new migration on already-migrated databases.
    "SELECT 1",
    # Coach plan lifecycle: 'draft' = generated but not yet confirmed by user;
    # 'saved' = confirmed, routines created. Default keeps existing rows as saved.
    "ALTER TABLE coach_plans ADD COLUMN status TEXT NOT NULL DEFAULT 'saved'",
    # Multi-use invite links: an invite is valid while uses_count < max_uses.
    # DEFAULT 1 means pre-existing invite rows keep their original
    # one-time-use behavior after this migration runs.
    "ALTER TABLE invite_tokens ADD COLUMN max_uses INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE invite_tokens ADD COLUMN uses_count INTEGER NOT NULL DEFAULT 0",
    # used_by named a single user; multi-use invites can be used by several,
    # so a single FK column no longer makes sense. No audit trail replaces it
    # (see docs/superpowers/specs/2026-07-12-multi-use-invite-links-design.md).
    "ALTER TABLE invite_tokens DROP COLUMN used_by",
]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_db.py::test_invite_tokens_multi_use_columns -v`
Expected: PASS

- [ ] **Step 6: Run the full test_db.py suite to check nothing else broke**

Run: `.venv/bin/pytest tests/test_db.py -v`
Expected: All PASS (in particular `test_seeding_idempotency` and `test_migration_columns_exist`, which exercise the same `_MIGRATIONS` list this task appended to).

- [ ] **Step 7: Commit**

```bash
git add schema.sql app/db.py tests/test_db.py
git commit -m "feat(invite): add max_uses/uses_count columns, drop used_by"
```

---

### Task 2: Invite generation — admin picks `max_uses`, new 7-day expiry

**Files:**
- Modify: `app/routes/auth.py:361-415` (`invite_get`, `invite_post`)
- Test: `tests/test_auth.py` (new tests, existing invite tests must keep passing)

**Interfaces:**
- Consumes: `invite_tokens.max_uses`/`uses_count` columns from Task 1.
- Produces: `POST /invite` now takes a `max_uses` form field and inserts `expires_at = now + 7 days`. Both `invite_get` and `invite_post` select `max_uses, uses_count` alongside the existing columns and filter pending invites on `uses_count < max_uses` (not `used_at IS NULL`). Task 4 (templates) relies on `pending_invites` rows having `max_uses`/`uses_count` keys, and on `invite_post`'s render context having a `max_uses` key.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_auth.py`, right after `test_invite_page_shows_pending_invite` (find it near line 487):

```python
@pytest.mark.asyncio
async def test_invite_create_accepts_custom_max_uses(admin_client, db_conn):
    resp = await admin_client.post(
        "/invite", data={"max_uses": 3}, headers={"Accept": "text/html"}
    )
    assert resp.status_code == 200
    async with db_conn.execute(
        "SELECT max_uses FROM invite_tokens ORDER BY created_at DESC LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    assert row["max_uses"] == 3


@pytest.mark.asyncio
async def test_invite_create_defaults_to_five_uses_and_seven_days(admin_client, db_conn):
    # Explicit empty form body (not an omitted `data=`) so this exercises the
    # route's own Form(5, ...) default rather than any ambiguity in how an
    # entirely bodyless POST gets parsed.
    resp = await admin_client.post("/invite", data={}, headers={"Accept": "text/html"})
    assert resp.status_code == 200
    async with db_conn.execute(
        "SELECT max_uses, "
        "  CAST(ROUND((JULIANDAY(expires_at) - JULIANDAY('now','localtime')) * 24) AS INTEGER) AS hours_left "
        "FROM invite_tokens ORDER BY created_at DESC LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    assert row["max_uses"] == 5
    # Should be just under 7*24=168 hours away, not the old 48.
    assert 160 <= row["hours_left"] <= 168


@pytest.mark.asyncio
async def test_invite_create_rejects_out_of_range_max_uses(admin_client):
    resp = await admin_client.post("/invite", data={"max_uses": 0})
    assert resp.status_code == 422
    resp = await admin_client.post("/invite", data={"max_uses": 51})
    assert resp.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_auth.py::test_invite_create_accepts_custom_max_uses tests/test_auth.py::test_invite_create_defaults_to_five_uses_and_seven_days tests/test_auth.py::test_invite_create_rejects_out_of_range_max_uses -v`
Expected: FAIL — route doesn't accept `max_uses` yet, still inserts a 48-hour expiry.

- [ ] **Step 3: Update `invite_get` and `invite_post`**

Current (`app/routes/auth.py:361-415`):

```python
@router.get("/invite", response_class=HTMLResponse)
async def invite_get(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    user=Depends(require_admin),
):
    async with conn.execute(
        "SELECT token, created_at, expires_at FROM invite_tokens "
        "WHERE used_at IS NULL AND expires_at > datetime('now','localtime') "
        "ORDER BY created_at DESC"
    ) as cur:
        pending_invites = [dict(r) for r in await cur.fetchall()]
    base = str(request.base_url).rstrip("/")
    return templates.TemplateResponse(
        request, "invite.html", {"user": dict(user), "pending_invites": pending_invites, "base_url": base}
    )


@router.delete("/invite/{token}")
async def invite_delete(
    token: str,
    conn: aiosqlite.Connection = Depends(get_db),
    _user=Depends(require_admin),
):
    await conn.execute("DELETE FROM invite_tokens WHERE token = ?", (token,))
    await conn.commit()
    return Response(status_code=200)


@router.post("/invite")
async def invite_post(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    user=Depends(require_admin),
):
    token = secrets.token_urlsafe(32)
    await conn.execute(
        "INSERT INTO invite_tokens(token, created_by, expires_at) "
        "VALUES (?, ?, datetime('now','localtime','+48 hours'))",
        (token, user["id"]),
    )
    await conn.commit()
    base = str(request.base_url).rstrip("/")
    invite_url = f"{base}/invite/accept/{token}"
    async with conn.execute(
        "SELECT token, created_at, expires_at FROM invite_tokens "
        "WHERE used_at IS NULL AND expires_at > datetime('now','localtime') "
        "ORDER BY created_at DESC"
    ) as cur:
        pending_invites = [dict(r) for r in await cur.fetchall()]
    return render(request, "invite", {
        "invite_url": invite_url,
        "user": dict(user),
        "pending_invites": pending_invites,
    })
```

Replace with:

```python
@router.get("/invite", response_class=HTMLResponse)
async def invite_get(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    user=Depends(require_admin),
):
    async with conn.execute(
        "SELECT token, created_at, expires_at, max_uses, uses_count FROM invite_tokens "
        "WHERE uses_count < max_uses AND expires_at > datetime('now','localtime') "
        "ORDER BY created_at DESC"
    ) as cur:
        pending_invites = [dict(r) for r in await cur.fetchall()]
    base = str(request.base_url).rstrip("/")
    return templates.TemplateResponse(
        request, "invite.html", {"user": dict(user), "pending_invites": pending_invites, "base_url": base}
    )


@router.delete("/invite/{token}")
async def invite_delete(
    token: str,
    conn: aiosqlite.Connection = Depends(get_db),
    _user=Depends(require_admin),
):
    await conn.execute("DELETE FROM invite_tokens WHERE token = ?", (token,))
    await conn.commit()
    return Response(status_code=200)


@router.post("/invite")
async def invite_post(
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
    user=Depends(require_admin),
    max_uses: int = Form(5, ge=1, le=50),
):
    token = secrets.token_urlsafe(32)
    await conn.execute(
        "INSERT INTO invite_tokens(token, created_by, expires_at, max_uses) "
        "VALUES (?, ?, datetime('now','localtime','+7 days'), ?)",
        (token, user["id"], max_uses),
    )
    await conn.commit()
    base = str(request.base_url).rstrip("/")
    invite_url = f"{base}/invite/accept/{token}"
    async with conn.execute(
        "SELECT token, created_at, expires_at, max_uses, uses_count FROM invite_tokens "
        "WHERE uses_count < max_uses AND expires_at > datetime('now','localtime') "
        "ORDER BY created_at DESC"
    ) as cur:
        pending_invites = [dict(r) for r in await cur.fetchall()]
    return render(request, "invite", {
        "invite_url": invite_url,
        "max_uses": max_uses,
        "user": dict(user),
        "pending_invites": pending_invites,
    })
```

`request: Request` has no default and must stay first; `conn`, `user`, and `max_uses` all carry defaults (`Depends(...)` or `Form(...)`) so their relative order doesn't matter to Python — `max_uses` is placed last simply to keep the two `Depends` params together.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_auth.py::test_invite_create_accepts_custom_max_uses tests/test_auth.py::test_invite_create_defaults_to_five_uses_and_seven_days tests/test_auth.py::test_invite_create_rejects_out_of_range_max_uses -v`
Expected: PASS

- [ ] **Step 5: Run the full invite test block to check nothing regressed**

Run: `.venv/bin/pytest tests/test_auth.py -k invite -v`
Expected: All PASS. (`test_invite_page_shows_pending_invite` and `test_invite_page_empty_state` don't reference `used_at`/`used_by` directly, so the column/filter change doesn't break them.)

- [ ] **Step 6: Commit**

```bash
git add app/routes/auth.py tests/test_auth.py
git commit -m "feat(invite): admin-settable max_uses, 7-day expiry on generate"
```

---

### Task 3: Invite acceptance — validity check + atomic use-counter increment

**Files:**
- Modify: `app/routes/auth.py:359-486` (add `_fetch_valid_invite`, update `invite_accept_get`, `invite_accept_post`)
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `invite_tokens.max_uses`/`uses_count` from Task 1.
- Produces: `_fetch_valid_invite(conn, token) -> aiosqlite.Row` — raises `HTTPException(400, "Invalid or expired invite link")` if the token doesn't exist, is exhausted, or is expired; otherwise returns the row. Used by both accept routes; the shared `_fetch_valid_token` (used by password reset) is untouched.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_auth.py`, right after the existing `test_invite_accept_single_use_token` (find it near line 172):

```python
@pytest.mark.asyncio
async def test_invite_accept_multi_use_allows_up_to_cap(anon_client, db_conn):
    await db_conn.execute(
        "INSERT INTO invite_tokens(token, created_by, expires_at, max_uses) "
        "VALUES ('tok-multi', 1, datetime('now','localtime','+7 days'), 2)"
    )
    await db_conn.commit()

    resp1 = await anon_client.post(
        "/invite/accept/tok-multi",
        data={
            "username": "multiuser1",
            "email": "multi1@example.com",
            "password": "password1",
            "password_confirm": "password1",
        },
    )
    assert resp1.status_code in (302, 303)

    resp2 = await anon_client.post(
        "/invite/accept/tok-multi",
        data={
            "username": "multiuser2",
            "email": "multi2@example.com",
            "password": "password2",
            "password_confirm": "password2",
        },
    )
    assert resp2.status_code in (302, 303)

    async with db_conn.execute(
        "SELECT uses_count, max_uses FROM invite_tokens WHERE token = 'tok-multi'"
    ) as cur:
        row = await cur.fetchone()
    assert row["uses_count"] == 2
    assert row["max_uses"] == 2


@pytest.mark.asyncio
async def test_invite_accept_rejects_past_cap(anon_client, db_conn):
    await db_conn.execute(
        "INSERT INTO invite_tokens(token, created_by, expires_at, max_uses) "
        "VALUES ('tok-cap', 1, datetime('now','localtime','+7 days'), 1)"
    )
    await db_conn.commit()

    await anon_client.post(
        "/invite/accept/tok-cap",
        data={
            "username": "capuser1",
            "email": "cap1@example.com",
            "password": "password1",
            "password_confirm": "password1",
        },
    )
    resp = await anon_client.post(
        "/invite/accept/tok-cap",
        data={
            "username": "capuser2",
            "email": "cap2@example.com",
            "password": "password2",
            "password_confirm": "password2",
        },
    )
    assert resp.status_code == 400

    async with db_conn.execute("SELECT id FROM users WHERE username = 'capuser2'") as cur:
        assert await cur.fetchone() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_auth.py::test_invite_accept_multi_use_allows_up_to_cap tests/test_auth.py::test_invite_accept_rejects_past_cap -v`
Expected: FAIL — the second accept in the first test currently gets rejected (still single-use logic), and the first test therefore fails on `resp2.status_code`.

- [ ] **Step 3: Add `_fetch_valid_invite` and update both accept routes**

Current (`app/routes/auth.py:359-486`, the whole "Invite" section):

```python
# ── Invite ────────────────────────────────────────────────────────────────────

@router.get("/invite", response_class=HTMLResponse)
```

Insert the new helper directly above that `@router.get("/invite", ...)` line, right after the section comment:

```python
# ── Invite ────────────────────────────────────────────────────────────────────

async def _fetch_valid_invite(conn: aiosqlite.Connection, token: str) -> aiosqlite.Row:
    """Like _fetch_valid_token, but for the multi-use invite_tokens table:
    valid while uses_count < max_uses (not used_at IS NULL) and not expired."""
    async with conn.execute(
        "SELECT * FROM invite_tokens WHERE token = ? AND uses_count < max_uses"
        " AND expires_at > datetime('now','localtime')",
        (token,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="Invalid or expired invite link")
    return row


@router.get("/invite", response_class=HTMLResponse)
```

Then, in `invite_accept_get` (currently):

```python
@router.get("/invite/accept/{token}", response_class=HTMLResponse)
async def invite_accept_get(
    token: str,
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
):
    await _fetch_valid_token(conn, "invite_tokens", token, "Invalid or expired invite link")
    return templates.TemplateResponse(
        request, "invite_accept.html",
        {"token": token, "errors": {}, "form": {}},
    )
```

Change the validity check line to:

```python
@router.get("/invite/accept/{token}", response_class=HTMLResponse)
async def invite_accept_get(
    token: str,
    request: Request,
    conn: aiosqlite.Connection = Depends(get_db),
):
    await _fetch_valid_invite(conn, token)
    return templates.TemplateResponse(
        request, "invite_accept.html",
        {"token": token, "errors": {}, "form": {}},
    )
```

Then, in `invite_accept_post` (currently):

```python
@router.post("/invite/accept/{token}")
async def invite_accept_post(
    token: str,
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    conn: aiosqlite.Connection = Depends(get_db),
):
    await _fetch_valid_token(conn, "invite_tokens", token, "Invalid or expired invite link")

    email = email.strip().lower()
```

Change the validity check line to:

```python
@router.post("/invite/accept/{token}")
async def invite_accept_post(
    token: str,
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    conn: aiosqlite.Connection = Depends(get_db),
):
    await _fetch_valid_invite(conn, token)

    email = email.strip().lower()
```

Finally, replace the unconditional single-use `UPDATE` (currently):

```python
    hashed = _hash_password(password)
    try:
        async with conn.execute(
            "INSERT INTO users(username, password_hash, is_admin, email) VALUES (?, ?, 0, ?)",
            (username, hashed, email),
        ) as cur:
            new_user_id = cur.lastrowid
        await conn.execute(
            "UPDATE invite_tokens "
            "SET used_at = datetime('now','localtime'), used_by = ? "
            "WHERE token = ?",
            (new_user_id, token),
        )
        await conn.commit()
    except aiosqlite.IntegrityError as exc:
```

with the atomic, cap-guarded increment:

```python
    hashed = _hash_password(password)
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
            # row behind with no valid invite backing it.
            await conn.rollback()
            raise HTTPException(status_code=400, detail="Invalid or expired invite link")
        await conn.commit()
    except aiosqlite.IntegrityError as exc:
```

Note `new_user_id` is no longer referenced after the `UPDATE` (it was only used for the dropped `used_by` column) — that's fine, it's still needed for the `cur.lastrowid` read itself and isn't otherwise unused-variable-flagged since nothing lints that here; leave it as-is for clarity at the call site.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_auth.py::test_invite_accept_multi_use_allows_up_to_cap tests/test_auth.py::test_invite_accept_rejects_past_cap -v`
Expected: PASS

- [ ] **Step 5: Run the full auth test suite**

Run: `.venv/bin/pytest tests/test_auth.py -v`
Expected: All PASS, including the pre-existing `test_invite_accept_single_use_token` (an invite inserted without `max_uses` still defaults to `max_uses=1`, so it still behaves as single-use) and `test_invite_accept_expired_token_returns_400`.

- [ ] **Step 6: Commit**

```bash
git add app/routes/auth.py tests/test_auth.py
git commit -m "feat(invite): atomically cap accepts at max_uses, add _fetch_valid_invite"
```

---

### Task 4: Templates — max-uses input, Uses column, updated copy

**Files:**
- Modify: `app/templates/invite.html` (full file, 73 lines)
- Modify: `app/templates/invite_partial.html` (full file, 51 lines)

**Interfaces:**
- Consumes: `pending_invites` rows now carry `max_uses`/`uses_count` (Task 2); `invite_post`'s render context now carries a top-level `max_uses` value (Task 2).

- [ ] **Step 1: Update `app/templates/invite.html`**

Current full file:

```html
{% extends "base.html" %}
{% block title %}Invite User — Zenkai{% endblock %}

{% block content %}
<div class="page-hd">
  <div>
    <h1>Invite a new user</h1>
    <div class="subtitle">One-time registration links</div>
  </div>
</div>

<div class="grid-2" style="align-items:start;">
  <div>
    <div class="section-title" style="margin-bottom:0.75rem;">Generate link</div>
    <div class="card">
      <p style="font-size:0.875rem; color:var(--muted); margin-bottom:1.25rem;">
        Generate a one-time invite link. It expires after 48 hours.
      </p>
      <button class="btn btn-primary"
              style="width:100%;"
              hx-post="/invite"
              hx-target="#invite-result"
              hx-swap="innerHTML">
        Generate invite link
      </button>
      <div id="invite-result" style="margin-top:1rem;"></div>
    </div>
  </div>

  <div>
    <div class="section-title" style="margin-bottom:0.75rem;">Pending invites</div>
    <div id="pending-invites-card" class="card" style="padding:0; overflow:hidden;">
      <table>
        <thead>
          <tr>
            <th>Token</th>
            <th>Created</th>
            <th>Expires</th>
            <th></th>
          </tr>
        </thead>
        <tbody id="pending-invites-body">
          {% if pending_invites %}
            {% for inv in pending_invites %}
            <tr>
              <td>
                <code style="font-family:var(--mono); font-size:0.8rem; color:var(--muted);">…{{ inv.token[-8:] }}</code>
              </td>
              <td style="font-size:0.8rem; color:var(--muted);">{{ inv.created_at[:16] }}</td>
              <td style="font-size:0.8rem; color:var(--muted);">{{ inv.expires_at[:16] }}</td>
              <td style="text-align:right;">
                <button class="btn btn-ghost"
                        style="font-size:0.8rem; color:var(--danger);"
                        hx-delete="/invite/{{ inv.token }}"
                        hx-target="closest tr"
                        hx-swap="outerHTML"
                        hx-confirm="Revoke this invite link?">
                  Revoke
                </button>
              </td>
            </tr>
            {% endfor %}
          {% else %}
          <tr id="pending-invites-empty">
            <td colspan="4" style="padding:1rem; color:var(--muted); font-size:0.875rem;">No pending invites.</td>
          </tr>
          {% endif %}
        </tbody>
      </table>
    </div>
  </div>
</div>
{% endblock %}
```

Replace with:

```html
{% extends "base.html" %}
{% block title %}Invite User — Zenkai{% endblock %}

{% block content %}
<div class="page-hd">
  <div>
    <h1>Invite a new user</h1>
    <div class="subtitle">Shareable registration links</div>
  </div>
</div>

<div class="grid-2" style="align-items:start;">
  <div>
    <div class="section-title" style="margin-bottom:0.75rem;">Generate link</div>
    <div class="card">
      <p style="font-size:0.875rem; color:var(--muted); margin-bottom:1.25rem;">
        Generate an invite link good for multiple signups. It expires after 7 days.
      </p>
      <div class="form-group">
        <label for="invite-max-uses">Max uses</label>
        <input type="number" id="invite-max-uses" name="max_uses" value="5" min="1" max="50" style="height:44px;">
      </div>
      <button class="btn btn-primary"
              style="width:100%;"
              hx-post="/invite"
              hx-include="#invite-max-uses"
              hx-target="#invite-result"
              hx-swap="innerHTML">
        Generate invite link
      </button>
      <div id="invite-result" style="margin-top:1rem;"></div>
    </div>
  </div>

  <div>
    <div class="section-title" style="margin-bottom:0.75rem;">Pending invites</div>
    <div id="pending-invites-card" class="card" style="padding:0; overflow:hidden;">
      <table>
        <thead>
          <tr>
            <th>Token</th>
            <th>Created</th>
            <th>Expires</th>
            <th>Uses</th>
            <th></th>
          </tr>
        </thead>
        <tbody id="pending-invites-body">
          {% if pending_invites %}
            {% for inv in pending_invites %}
            <tr>
              <td>
                <code style="font-family:var(--mono); font-size:0.8rem; color:var(--muted);">…{{ inv.token[-8:] }}</code>
              </td>
              <td style="font-size:0.8rem; color:var(--muted);">{{ inv.created_at[:16] }}</td>
              <td style="font-size:0.8rem; color:var(--muted);">{{ inv.expires_at[:16] }}</td>
              <td class="num" style="font-size:0.8rem; color:var(--muted);">{{ inv.uses_count }} / {{ inv.max_uses }}</td>
              <td style="text-align:right;">
                <button class="btn btn-ghost"
                        style="font-size:0.8rem; color:var(--danger);"
                        hx-delete="/invite/{{ inv.token }}"
                        hx-target="closest tr"
                        hx-swap="outerHTML"
                        hx-confirm="Revoke this invite link?">
                  Revoke
                </button>
              </td>
            </tr>
            {% endfor %}
          {% else %}
          <tr id="pending-invites-empty">
            <td colspan="5" style="padding:1rem; color:var(--muted); font-size:0.875rem;">No pending invites.</td>
          </tr>
          {% endif %}
        </tbody>
      </table>
    </div>
  </div>
</div>
{% endblock %}
```

Changes: subtitle copy, generate-panel copy + new max-uses input + `hx-include` on the button, one new `<th>Uses</th>` / `<td>` column, `colspan` bumped from 4 to 5 on the empty-state row.

- [ ] **Step 2: Update `app/templates/invite_partial.html`**

Current full file:

```html
<div style="margin-top: 0.25rem;">
  <p style="font-size: 0.8rem; color: var(--muted); margin-bottom: 0.5rem;">Share this link — expires in 48 hours, single use:</p>
  <div style="display: flex; gap: 0.5rem; align-items: center;">
    <input type="text" id="invite-url" value="{{ invite_url | e }}" readonly
           style="flex: 1; font-size: 0.8rem; background: var(--bg); cursor: text;">
    <button class="btn btn-ghost"
            style="flex-shrink: 0;"
            onclick="var btn=this;navigator.clipboard.writeText(document.getElementById('invite-url').value).then(function(){btn.textContent='Copied!';setTimeout(function(){btn.textContent='Copy';},2000)})"
            type="button">
      Copy
    </button>
  </div>
</div>

<div id="pending-invites-card" hx-swap-oob="innerHTML" class="card" style="padding:0; overflow:hidden;">
  <table>
    <thead>
      <tr>
        <th>Token</th>
        <th>Created</th>
        <th>Expires</th>
        <th></th>
      </tr>
    </thead>
    <tbody id="pending-invites-body">
      {% for inv in pending_invites %}
      <tr>
        <td>
          <code style="font-family:var(--mono); font-size:0.8rem; color:var(--muted);">…{{ inv.token[-8:] }}</code>
        </td>
        <td style="font-size:0.8rem; color:var(--muted);">{{ inv.created_at[:16] }}</td>
        <td style="font-size:0.8rem; color:var(--muted);">{{ inv.expires_at[:16] }}</td>
        <td style="text-align:right;">
          <button class="btn btn-ghost"
                  style="font-size:0.8rem; color:var(--danger);"
                  hx-delete="/invite/{{ inv.token }}"
                  hx-target="closest tr"
                  hx-swap="outerHTML"
                  hx-confirm="Revoke this invite link?">
            Revoke
          </button>
        </td>
      </tr>
      {% else %}
      <tr id="pending-invites-empty">
        <td colspan="4" style="padding:1rem; color:var(--muted); font-size:0.875rem;">No pending invites.</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
```

Replace with:

```html
<div style="margin-top: 0.25rem;">
  <p style="font-size: 0.8rem; color: var(--muted); margin-bottom: 0.5rem;">Share this link — expires in 7 days, up to {{ max_uses }} signups:</p>
  <div style="display: flex; gap: 0.5rem; align-items: center;">
    <input type="text" id="invite-url" value="{{ invite_url | e }}" readonly
           style="flex: 1; font-size: 0.8rem; background: var(--bg); cursor: text;">
    <button class="btn btn-ghost"
            style="flex-shrink: 0;"
            onclick="var btn=this;navigator.clipboard.writeText(document.getElementById('invite-url').value).then(function(){btn.textContent='Copied!';setTimeout(function(){btn.textContent='Copy';},2000)})"
            type="button">
      Copy
    </button>
  </div>
</div>

<div id="pending-invites-card" hx-swap-oob="innerHTML" class="card" style="padding:0; overflow:hidden;">
  <table>
    <thead>
      <tr>
        <th>Token</th>
        <th>Created</th>
        <th>Expires</th>
        <th>Uses</th>
        <th></th>
      </tr>
    </thead>
    <tbody id="pending-invites-body">
      {% for inv in pending_invites %}
      <tr>
        <td>
          <code style="font-family:var(--mono); font-size:0.8rem; color:var(--muted);">…{{ inv.token[-8:] }}</code>
        </td>
        <td style="font-size:0.8rem; color:var(--muted);">{{ inv.created_at[:16] }}</td>
        <td style="font-size:0.8rem; color:var(--muted);">{{ inv.expires_at[:16] }}</td>
        <td class="num" style="font-size:0.8rem; color:var(--muted);">{{ inv.uses_count }} / {{ inv.max_uses }}</td>
        <td style="text-align:right;">
          <button class="btn btn-ghost"
                  style="font-size:0.8rem; color:var(--danger);"
                  hx-delete="/invite/{{ inv.token }}"
                  hx-target="closest tr"
                  hx-swap="outerHTML"
                  hx-confirm="Revoke this invite link?">
            Revoke
          </button>
        </td>
      </tr>
      {% else %}
      <tr id="pending-invites-empty">
        <td colspan="5" style="padding:1rem; color:var(--muted); font-size:0.875rem;">No pending invites.</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
```

- [ ] **Step 3: Run the full invite test block once more (templates aren't covered by these tests directly, but a route regression would show up here)**

Run: `.venv/bin/pytest tests/test_auth.py -k invite -v`
Expected: All PASS.

- [ ] **Step 4: Commit**

```bash
git add app/templates/invite.html app/templates/invite_partial.html
git commit -m "feat(invite): max-uses input and Uses column in invite UI"
```

---

### Task 5: Manual verification on the live app

This app runs on a Raspberry Pi via systemd (`fitstorm.service`) with no hot-reload for Python route changes — Tasks 1–3 touched `app/db.py` and `app/routes/auth.py`, so the service needs a restart before these changes are live. Template-only edits (Task 4) are picked up on next request without a restart, but restarting once at the end covers everything.

- [ ] **Step 1: Restart the service**

Run: `sudo systemctl restart fitstorm` (requires an interactive password — if you can't run `sudo` non-interactively, ask the user to run it)

- [ ] **Step 2: Confirm it's back up**

Run: `systemctl is-active fitstorm && curl -sf http://localhost:8000 -o /dev/null && echo up`
Expected: `active` then `up`

- [ ] **Step 3: Manually generate a 2-use invite and accept it twice**

As an admin, visit `/invite`, set "Max uses" to 2, click "Generate invite link", copy the link. In a private/incognito window (or after signing out), open the link and complete signup with a first test account. Reload `/invite` as admin — confirm the pending row now reads "1 / 2" instead of dropping off the list. Open the same link again and complete signup with a second test account — confirm it succeeds and the invite now reads "2 / 2" and has dropped off the pending list (or a third attempt with the same link returns the "Invalid or expired invite link" page).

- [ ] **Step 4: Clean up test accounts**

Delete the two test user accounts created in Step 3 (via direct DB access or however existing test/QA accounts have been cleaned up in this project) so they don't clutter the real user list.

---

## Self-Review Notes

- **Spec coverage:** Data model (Task 1), route behavior including the atomic race-guard (Tasks 2–3), UI changes (Task 4), and the manual edge-case walkthrough (Task 5) all map directly to the spec's corresponding sections. The spec's "existing rows keep max_uses=1" requirement is covered by the migration's `DEFAULT 1` plus the untouched `test_invite_accept_single_use_token` test continuing to pass.
- **Placeholder scan:** No TBDs; every step has literal code or an exact command with expected output.
- **Type consistency:** `_fetch_valid_invite(conn, token)` signature is defined once in Task 3 and not referenced with a different name elsewhere. `max_uses`/`uses_count` column names are identical across schema.sql, db.py, auth.py, and both templates.
