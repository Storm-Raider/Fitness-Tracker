# Backfill Daily Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user view, create, or edit a Daily Log entry for any past date, not just today.

**Architecture:** One new backend endpoint (`GET /journal/entry?date=YYYY-MM-DD`) returns a given date's entry (or `null`) for on-demand lookup. The frontend adds a date picker and a single unified `loadEntryForDate()` function that fully repopulates the form from either that endpoint or an in-hand history-row object — replacing the current hardcoded-to-today, partial-overwrite `fillFromHistory`.

**Tech Stack:** FastAPI + aiosqlite, Jinja2 templates, vanilla JS (no HTMX on this page), pytest + pytest-asyncio.

## Global Constraints

- No schema change — `daily_logs` already has `UNIQUE(user_id, log_date)` and `POST /journal` already upserts on it correctly for any date.
- The new endpoint validates the date with `date.fromisoformat()`, returning 422 on a malformed string. It does **not** reject future dates.
- Date picker: default value = today, `max` = today (blocks future dates), no `min` (unlimited past range).
- `loadEntryForDate()` must fully clear-and-repopulate every field on every call (day number, weight, workout, all three meals, water, sleep, steps, notes, energy, motivation) — never leave a field showing a stale value from a previously-loaded date.
- The day-number auto-fill from the active challenge's current day count only applies when the date is today AND there's no saved entry for today yet — never for a past date.
- History-row clicks auto-load immediately (no `confirm()` popup) and update the date picker to match.
- Spec: `docs/superpowers/specs/2026-07-13-journal-backfill-design.md`

---

### Task 1: `GET /journal/entry` lookup endpoint

**Files:**
- Modify: `app/routes/journal.py:1-12` (imports), append new route after `save_log` (currently ends at line 108)
- Test: `tests/test_journal.py` (append new tests)

**Interfaces:**
- Produces: `GET /journal/entry?date=YYYY-MM-DD` → `200 {"entry": {...} | null}` on a valid date, `422` on a malformed one. The `entry` dict (when present) has the same keys as a `daily_logs` row: `id, user_id, log_date, day_number, weight_kg, workout, meal_1, meal_2, meal_3, water_l, energy, motivation, sleep_hrs, steps, notes, created_at`. Task 2's frontend code consumes this shape directly — same shape the existing `history`/`today_log` template variables already use.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_journal.py`, after the existing `test_invalid_energy_rejected` (find it near line 74):

```python
@pytest.mark.asyncio
async def test_get_entry_for_existing_date(client, db_conn):
    await client.post("/journal", json=LOG)

    r = await client.get(f"/journal/entry?date={TODAY}")
    assert r.status_code == 200
    entry = r.json()["entry"]
    assert entry is not None
    assert entry["weight_kg"] == 114.0
    assert entry["workout"] == "Level 2 Day 4"
    assert entry["log_date"] == TODAY


@pytest.mark.asyncio
async def test_get_entry_for_date_with_no_log_returns_null(client):
    r = await client.get("/journal/entry?date=2020-01-01")
    assert r.status_code == 200
    assert r.json()["entry"] is None


@pytest.mark.asyncio
async def test_get_entry_rejects_malformed_date(client):
    r = await client.get("/journal/entry?date=not-a-date")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_get_entry_requires_auth(anon_client):
    r = await anon_client.get("/journal/entry?date=2020-01-01")
    assert r.status_code == 302
    assert "/login" in r.headers["location"]


@pytest.mark.asyncio
async def test_get_entry_scoped_to_current_user(client, db_conn):
    # Seed a log for a different user; the `client` fixture's user (id=1)
    # must never see it.
    await db_conn.execute(
        "INSERT INTO users(id, username, password_hash, is_admin) "
        "VALUES (99, 'otheruser', 'x', 0)"
    )
    await db_conn.execute(
        "INSERT INTO daily_logs(user_id, log_date, weight_kg) VALUES (99, '2020-06-01', 200.0)"
    )
    await db_conn.commit()

    r = await client.get("/journal/entry?date=2020-06-01")
    assert r.status_code == 200
    assert r.json()["entry"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_journal.py -k "get_entry" -v`
Expected: FAIL — `/journal/entry` doesn't exist yet, so every request 404s (or the auth-redirect test fails differently since the route isn't registered at all).

- [ ] **Step 3: Add `HTTPException` and `Query` imports**

Current (`app/routes/journal.py:1-6`):

```python
from datetime import date, timedelta

import aiosqlite
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
```

Change to:

```python
from datetime import date, timedelta

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
```

- [ ] **Step 4: Add the new route**

Append this after `save_log` (the file currently ends at line 108, with `save_log` returning `JSONResponse({"ok": True})`):

```python
@router.get("/journal/entry")
async def get_entry_for_date(
    date_str: str = Query(..., alias="date"),
    conn: aiosqlite.Connection = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date; expected YYYY-MM-DD")

    uid = current_user["id"]
    async with conn.execute(
        "SELECT * FROM daily_logs WHERE user_id=? AND log_date=?", (uid, date_str)
    ) as c:
        row = await c.fetchone()
    return JSONResponse({"entry": dict(row) if row else None})
```

Note the parameter is named `date_str` (not `date`) with `Query(..., alias="date")` so the URL query string stays `?date=YYYY-MM-DD` while the Python identifier doesn't shadow the `date` class already imported at the top of this file (which the validation line right above calls as `date.fromisoformat(...)`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_journal.py -k "get_entry" -v`
Expected: all 5 PASS.

- [ ] **Step 6: Run the full journal test file and the full suite**

Run: `.venv/bin/pytest tests/test_journal.py -v`
Expected: all PASS (existing tests + 5 new ones).

Run: `.venv/bin/pytest -q`
Expected: all passing, 0 failed, pristine output (no new warnings).

- [ ] **Step 7: Commit**

```bash
git add app/routes/journal.py tests/test_journal.py
git commit -m "feat(journal): add GET /journal/entry lookup endpoint"
```

---

### Task 2: Date picker + unified load function in `journal.html`

**Files:**
- Modify: `app/templates/journal.html` (full file, 252 lines)
- Test: `tests/test_journal.py` (one new test asserting the date input renders)

**Interfaces:**
- Consumes: `GET /journal/entry?date=...` from Task 1 — returns `{"entry": {...} | null}`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_journal.py`, after the tests added in Task 1:

```python
@pytest.mark.asyncio
async def test_journal_page_has_date_picker(client):
    r = await client.get("/journal", headers={"Accept": "text/html"})
    assert r.status_code == 200
    assert 'id="j-date"' in r.text
    assert f'value="{TODAY}"' in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_journal.py::test_journal_page_has_date_picker -v`
Expected: FAIL — no element with `id="j-date"` exists yet.

- [ ] **Step 3: Update the card header and add the date picker**

Current (`app/templates/journal.html:43-52`):

```html
<div class="card" style="margin-bottom:1.5rem;">
  <div class="section-title" style="margin-bottom:1rem;">Today's entry</div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:0 1rem;">

    <div class="jf-row">
      <label for="j-day">Day number</label>
      <input type="number" id="j-day" min="1" placeholder="e.g. {{ active_day or 1 }}"
             value="{{ today_log.day_number if today_log and today_log.day_number else (active_day or '') }}">
    </div>
```

Replace with:

```html
<div class="card" style="margin-bottom:1.5rem;">
  <div class="section-title" style="margin-bottom:1rem;" id="entry-title">Today's entry</div>

  <div class="jf-row">
    <label for="j-date">Date</label>
    <input type="date" id="j-date" value="{{ today }}" max="{{ today }}"
           onchange="onDateChanged(this.value)">
  </div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:0 1rem;">

    <div class="jf-row">
      <label for="j-day">Day number</label>
      <input type="number" id="j-day" min="1" placeholder="e.g. {{ active_day or 1 }}"
             value="{{ today_log.day_number if today_log and today_log.day_number else (active_day or '') }}">
    </div>
```

- [ ] **Step 4: Replace the script block**

Current (`app/templates/journal.html:167-250`, the entire `<script>` block):

```html
<script>
const TODAY = {{ today | tojson }};

function pick(groupId, btn) {
  document.querySelectorAll('#' + groupId + ' .seg-btn').forEach(b => b.classList.remove('active'));
  btn.classList.toggle('active', true);
}

function segVal(groupId) {
  const active = document.querySelector('#' + groupId + ' .seg-btn.active');
  return active ? active.dataset.val : null;
}

function num(id) {
  const v = parseFloat(document.getElementById(id).value);
  return isNaN(v) ? null : v;
}
function int(id) {
  const v = parseInt(document.getElementById(id).value);
  return isNaN(v) ? null : v;
}
function str(id) {
  const v = document.getElementById(id).value.trim();
  return v || null;
}

async function saveLog() {
  const btn = document.getElementById('save-btn');
  const msg = document.getElementById('save-msg');
  btn.disabled = true;

  const payload = {
    log_date: TODAY,
    day_number: int('j-day'),
    weight_kg: num('j-weight'),
    workout: str('j-workout'),
    meal_1: str('j-meal1'),
    meal_2: str('j-meal2'),
    meal_3: str('j-meal3'),
    water_l: num('j-water'),
    energy: segVal('seg-energy'),
    motivation: segVal('seg-motivation'),
    sleep_hrs: num('j-sleep'),
    steps: int('j-steps'),
    notes: str('j-notes'),
  };

  const r = await fetch('/journal', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });

  btn.disabled = false;
  if (r.ok) {
    msg.textContent = '✓ Saved';
    msg.style.color = 'var(--success)';
    msg.style.display = 'inline';
    setTimeout(() => { msg.style.display = 'none'; }, 2500);
  } else {
    msg.textContent = 'Failed — try again';
    msg.style.color = 'var(--danger)';
    msg.style.display = 'inline';
  }
}

function fillFromHistory(entry) {
  if (entry.log_date === TODAY) return; // already shown
  if (!confirm('Load entry from ' + entry.log_date + ' into today\'s form?')) return;
  if (entry.day_number) document.getElementById('j-day').value = entry.day_number;
  if (entry.weight_kg)  document.getElementById('j-weight').value = entry.weight_kg;
  if (entry.workout)    document.getElementById('j-workout').value = entry.workout;
  if (entry.meal_1)     document.getElementById('j-meal1').value = entry.meal_1;
  if (entry.meal_2)     document.getElementById('j-meal2').value = entry.meal_2;
  if (entry.meal_3)     document.getElementById('j-meal3').value = entry.meal_3;
  if (entry.water_l)    document.getElementById('j-water').value = entry.water_l;
  if (entry.sleep_hrs)  document.getElementById('j-sleep').value = entry.sleep_hrs;
  if (entry.steps)      document.getElementById('j-steps').value = entry.steps;
  if (entry.notes)      document.getElementById('j-notes').value = entry.notes;
  if (entry.energy)     { document.querySelectorAll('#seg-energy .seg-btn').forEach(b => b.classList.toggle('active', b.dataset.val===entry.energy)); }
  if (entry.motivation) { document.querySelectorAll('#seg-motivation .seg-btn').forEach(b => b.classList.toggle('active', b.dataset.val===entry.motivation)); }
  window.scrollTo({top:0, behavior:'smooth'});
}
</script>
```

Replace with:

```html
<script>
const TODAY = {{ today | tojson }};
const ACTIVE_DAY = {{ active_day | tojson }};

function pick(groupId, btn) {
  document.querySelectorAll('#' + groupId + ' .seg-btn').forEach(b => b.classList.remove('active'));
  btn.classList.toggle('active', true);
}

function segVal(groupId) {
  const active = document.querySelector('#' + groupId + ' .seg-btn.active');
  return active ? active.dataset.val : null;
}

function num(id) {
  const v = parseFloat(document.getElementById(id).value);
  return isNaN(v) ? null : v;
}
function int(id) {
  const v = parseInt(document.getElementById(id).value);
  return isNaN(v) ? null : v;
}
function str(id) {
  const v = document.getElementById(id).value.trim();
  return v || null;
}

async function saveLog() {
  const btn = document.getElementById('save-btn');
  const msg = document.getElementById('save-msg');
  btn.disabled = true;

  const payload = {
    log_date: document.getElementById('j-date').value,
    day_number: int('j-day'),
    weight_kg: num('j-weight'),
    workout: str('j-workout'),
    meal_1: str('j-meal1'),
    meal_2: str('j-meal2'),
    meal_3: str('j-meal3'),
    water_l: num('j-water'),
    energy: segVal('seg-energy'),
    motivation: segVal('seg-motivation'),
    sleep_hrs: num('j-sleep'),
    steps: int('j-steps'),
    notes: str('j-notes'),
  };

  const r = await fetch('/journal', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });

  btn.disabled = false;
  if (r.ok) {
    msg.textContent = '✓ Saved';
    msg.style.color = 'var(--success)';
    msg.style.display = 'inline';
    setTimeout(() => { msg.style.display = 'none'; }, 2500);
  } else {
    msg.textContent = 'Failed — try again';
    msg.style.color = 'var(--danger)';
    msg.style.display = 'inline';
  }
}

// Fully clears and repopulates every field from `entry` (or blanks them all
// if `entry` is null) — never leaves a stale value from a previously-loaded
// date sitting in a field the new entry doesn't have.
function populateForm(dateStr, entry) {
  document.getElementById('j-day').value =
    (entry && entry.day_number) ? entry.day_number
    : (dateStr === TODAY && ACTIVE_DAY) ? ACTIVE_DAY
    : '';
  document.getElementById('j-weight').value = (entry && entry.weight_kg) || '';
  document.getElementById('j-workout').value = (entry && entry.workout) || '';
  document.getElementById('j-meal1').value = (entry && entry.meal_1) || '';
  document.getElementById('j-meal2').value = (entry && entry.meal_2) || '';
  document.getElementById('j-meal3').value = (entry && entry.meal_3) || '';
  document.getElementById('j-water').value = (entry && entry.water_l) || '';
  document.getElementById('j-sleep').value = (entry && entry.sleep_hrs) || '';
  document.getElementById('j-steps').value = (entry && entry.steps) || '';
  document.getElementById('j-notes').value = (entry && entry.notes) || '';
  document.querySelectorAll('#seg-energy .seg-btn').forEach(b =>
    b.classList.toggle('active', !!entry && b.dataset.val === entry.energy));
  document.querySelectorAll('#seg-motivation .seg-btn').forEach(b =>
    b.classList.toggle('active', !!entry && b.dataset.val === entry.motivation));

  document.getElementById('entry-title').textContent =
    dateStr === TODAY ? "Today's entry" : ('Entry for ' + dateStr);
}

// The single entry point for every date change: date-picker onchange (no
// entry in hand yet, so look it up) and history-row clicks (entry already
// in hand, no lookup needed) both funnel through here.
async function loadEntryForDate(dateStr, entry) {
  document.getElementById('j-date').value = dateStr;
  if (entry !== undefined) {
    populateForm(dateStr, entry);
    window.scrollTo({top:0, behavior:'smooth'});
    return;
  }
  const r = await fetch('/journal/entry?date=' + encodeURIComponent(dateStr));
  const data = r.ok ? await r.json() : {entry: null};
  populateForm(dateStr, data.entry);
}

function onDateChanged(dateStr) {
  loadEntryForDate(dateStr);
}

function fillFromHistory(entry) {
  loadEntryForDate(entry.log_date, entry);
}
</script>
```

Note `populateForm`'s day-number line: it only falls back to `ACTIVE_DAY` when `dateStr === TODAY` — for any other date with no entry, `entry.day_number` is falsy and `dateStr === TODAY` is false, so the ternary lands on `''` (blank), exactly matching the spec's requirement that a past date never gets today's challenge-day prefilled.

Note the initial page load already renders correct data for `TODAY` server-side (`today_log`, `active_day` are unchanged from before) — `populateForm`/`loadEntryForDate` only run on user interaction (date change or history click) after that initial render, so there's no redundant fetch on page load.

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_journal.py::test_journal_page_has_date_picker -v`
Expected: PASS

- [ ] **Step 6: Run the full journal test file and the full suite**

Run: `.venv/bin/pytest tests/test_journal.py -v`
Expected: all PASS.

Run: `.venv/bin/pytest -q`
Expected: all passing, 0 failed, pristine output.

- [ ] **Step 7: Manual verification**

This app runs on a Raspberry Pi via systemd (`fitstorm.service`) with no hot-reload for Python route changes (Task 1 touched `app/routes/journal.py`), but template-only changes (this task) are picked up on the next request without a restart. Since Task 1's restart already covers this task's route dependency, only a browser check is needed here:

1. Log in, go to `/journal`.
2. Confirm a date field appears above "Day number", defaulting to today, and the section title reads "Today's entry".
3. Pick a past date that has a history entry (or click that entry's row in the History list below) — confirm the form immediately repopulates with that date's data (no confirm popup) and the title changes to "Entry for YYYY-MM-DD".
4. Pick a past date with no entry — confirm every field goes blank (including day number — it should NOT show today's active challenge day).
5. Edit a field and click Save — confirm it saves successfully, then reload `/journal` and re-pick that date to confirm the edit persisted.
6. Pick today again — confirm the title reverts to "Today's entry" and today's real data reloads.

- [ ] **Step 8: Commit**

```bash
git add app/templates/journal.html tests/test_journal.py
git commit -m "feat(journal): date picker + unified load for backfilling past entries"
```

---

## Self-Review Notes

- **Spec coverage:** The endpoint (Task 1) and its validation/auth/scoping cover the spec's "Backend" section. The date picker, unified `loadEntryForDate`/`populateForm`, title update, day-number auto-fill restriction, and history-row behavior change (Task 2) cover the spec's "Frontend" section. The spec's "Edge cases" (no dirty-check, upsert already correct) require no code — confirmed nothing in either task needs to add a dirty-check, and Task 1 doesn't touch `POST /journal`'s upsert at all.
- **Placeholder scan:** every step has literal code or an exact command with expected output; no TBDs.
- **Type consistency:** `loadEntryForDate(dateStr, entry)` is defined once (Task 2, Step 4) and called consistently — `onDateChanged` passes only `dateStr` (triggering the lookup branch), `fillFromHistory` passes both `dateStr` and `entry` (skipping the lookup). `populateForm(dateStr, entry)` is a private helper only called from within `loadEntryForDate`, not from template markup directly.
