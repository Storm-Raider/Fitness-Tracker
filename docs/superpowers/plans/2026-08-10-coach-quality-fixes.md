# AI Coach Quality Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three confirmed defects degrading AI Coach plan quality: bodyweight
exercises corrupting e1RM-based profile data, `qwen3`'s hidden reasoning pass
wasting time/context, and the system prompt's worked example getting copied
verbatim into real output.

**Architecture:** Three independent, single-file-plus-test changes. No schema
changes, no new endpoints, no changes to `_build_prompt()`'s downstream
rendering logic — only the data feeding into it and the client/prompt
sending it to Ollama.

**Tech Stack:** Python 3.13, FastAPI, aiosqlite, httpx, pytest + pytest-asyncio
(`asyncio_mode = auto` — plain `def test_...` and `async def test_...` both
run without an explicit `@pytest.mark.asyncio` marker).

## Global Constraints

- `"think": False` in `ollama.chat_json()`'s request payload is unconditional
  — no env var or config toggle. (Spec: Out of scope.)
- The bodyweight-equipment exclusion uses `COALESCE(e.equipment, '') !=
  'Bodyweight'`, never a bare `e.equipment != 'Bodyweight'` — a bare
  inequality against `NULL` evaluates to `NULL` (excluded) in SQLite, which
  would silently drop exercises with no equipment tag. `NULL` equipment must
  stay included.
- Do not modify `_build_prompt()`'s existing `if profile["top_lifts"]:` /
  `if profile.get("stalled"):` guards in `app/routes/coach.py` — they already
  handle an empty list correctly (skip the line).
- The system-prompt fix replaces only the "Example of one well-formed day"
  block in `_SYSTEM_PROMPT`; the new placeholder weights are `100/50/20/8/25`
  kg (not the original `90/55/28/12/30`).
- No retroactive fix for already-cached `_PROFILE_CACHE` entries (30-minute
  TTL) or already-saved `coach_plans` rows built from bad e1RM data.

---

### Task 1: Exclude bodyweight-equipment exercises from e1RM queries

**Files:**
- Modify: `app/utils/training_profile.py:76-89` (top-lifts query)
- Modify: `app/utils/training_profile.py:159-184` (stalled-lifts query)
- Create: `tests/test_training_profile.py`

**Interfaces:**
- Consumes: `app.utils.training_profile.build_profile(conn, uid) -> dict`
  (existing signature, unchanged) and `app.utils.training_profile
  .invalidate_profile(uid)` (existing, unchanged).
- Produces: no new public interface — `build_profile()`'s returned
  `profile["top_lifts"]` and `profile["stalled"]` now exclude
  `Bodyweight`-equipment exercises. Later tasks don't depend on this task's
  internals.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_training_profile.py`:

```python
from datetime import datetime, timedelta

import pytest

from app.utils import training_profile
from app.utils.training_profile import build_profile


@pytest.fixture(autouse=True)
def _reset_profile_cache():
    """build_profile() caches per-uid in a process-global dict — clear it
    around every test so one test's cached profile can't leak into the next."""
    training_profile._PROFILE_CACHE.clear()
    yield
    training_profile._PROFILE_CACHE.clear()


async def _exercise_id(db, name: str) -> int:
    async with db.execute("SELECT id FROM exercises WHERE name = ?", (name,)) as cur:
        row = await cur.fetchone()
        assert row is not None, f"seeded exercise {name!r} not found"
        return row["id"]


async def _log_set(db, workout_id: int, exercise_id: int, reps: int, weight_kg: float) -> None:
    await db.execute(
        "INSERT INTO sets(workout_id, exercise_id, reps, weight_kg, user_id) "
        "VALUES (?, ?, ?, ?, 1)",
        (workout_id, exercise_id, reps, weight_kg),
    )


@pytest.mark.asyncio
async def test_top_lifts_excludes_bodyweight_equipment(db):
    await db.execute(
        "INSERT INTO workouts(id, started_at, ended_at, user_id) "
        "VALUES (1, '2026-08-01 10:00:00', '2026-08-01 10:30:00', 1)"
    )
    crunch_id = await _exercise_id(db, "Crunch")
    bench_id = await _exercise_id(db, "Bench Press")
    # Crunch (Bodyweight equipment) logged with the effective-load convention
    # (weight_kg = bodyweight, no added weight) — must NOT produce an e1RM.
    await _log_set(db, 1, crunch_id, reps=15, weight_kg=61.1)
    # Bench Press (Barbell equipment) — a genuine loaded lift, must still
    # produce an e1RM as before.
    await _log_set(db, 1, bench_id, reps=5, weight_kg=100.0)
    await db.commit()

    profile = await build_profile(db, uid=1)
    names = {lift["name"] for lift in profile["top_lifts"]}

    assert "Crunch" not in names
    assert "Bench Press" in names


@pytest.mark.asyncio
async def test_stalled_excludes_bodyweight_equipment(db):
    crunch_id = await _exercise_id(db, "Crunch")

    def _at(days_ago: int) -> tuple[str, str]:
        start = datetime.now() - timedelta(days=days_ago)
        end = start + timedelta(minutes=30)
        return start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")

    # 4 sessions: two in the 28-84-day-ago "prior" window, two in the last
    # 28 days ("recent"), all logged with the same reps/weight so recent_1rm
    # == prior_1rm (a flat, "stalled" e1RM trend). This would qualify as
    # "stalled" under the old query, and must be excluded once Bodyweight
    # equipment is filtered out.
    sessions = [_at(80), _at(50), _at(20), _at(5)]
    for i, (started, ended) in enumerate(sessions, start=1):
        await db.execute(
            "INSERT INTO workouts(id, started_at, ended_at, user_id) VALUES (?, ?, ?, 1)",
            (i, started, ended),
        )
        await _log_set(db, i, crunch_id, reps=15, weight_kg=61.1)
    await db.commit()

    profile = await build_profile(db, uid=1)

    assert "Crunch" not in profile["stalled"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_training_profile.py -v`
Expected: both tests FAIL — `test_top_lifts_excludes_bodyweight_equipment`
fails because `"Crunch"` is present in `names`; `test_stalled_excludes_bodyweight_equipment`
fails because `"Crunch"` is present in `profile["stalled"]`.

- [ ] **Step 3: Fix the top-lifts query**

In `app/utils/training_profile.py`, replace the top-lifts query block
(currently lines 76-89):

```python
    # Top estimated 1RMs (Epley) for loaded lifts.
    async with conn.execute(
        """
        SELECT e.name, MAX(ROUND(s.weight_kg * (1 + s.reps / 30.0), 1)) AS e1rm
        FROM sets s
        JOIN exercises e ON e.id = s.exercise_id
        WHERE s.user_id = ? AND s.weight_kg > 0
        GROUP BY s.exercise_id
        ORDER BY e1rm DESC
        LIMIT 8
        """,
        (uid,),
    ) as cur:
        top_lifts = [dict(r) for r in await cur.fetchall()]
```

with:

```python
    # Top estimated 1RMs (Epley) for loaded lifts. Bodyweight-equipment
    # exercises are excluded — this app stores weight_kg = bodyweight +
    # added weight for those (see workouts.py), so applying the Epley
    # formula to them produces a fictional "1RM" from bodyweight alone.
    async with conn.execute(
        """
        SELECT e.name, MAX(ROUND(s.weight_kg * (1 + s.reps / 30.0), 1)) AS e1rm
        FROM sets s
        JOIN exercises e ON e.id = s.exercise_id
        WHERE s.user_id = ? AND s.weight_kg > 0
          AND COALESCE(e.equipment, '') != 'Bodyweight'
        GROUP BY s.exercise_id
        ORDER BY e1rm DESC
        LIMIT 8
        """,
        (uid,),
    ) as cur:
        top_lifts = [dict(r) for r in await cur.fetchall()]
```

- [ ] **Step 4: Fix the stalled-lifts query**

In `app/utils/training_profile.py`, replace the stalled-lifts query block
(currently lines 159-184):

```python
    # Stalled exercises — no meaningful 1RM progress in the last 28 days
    # vs. the 28–84-day window before that.
    async with conn.execute(
        """
        SELECT e.name,
               COUNT(DISTINCT DATE(w.started_at,'localtime')) AS session_count,
               MAX(CASE WHEN DATE(w.started_at,'localtime') >= DATE('now','-28 days')
                        THEN ROUND(s.weight_kg * (1.0 + s.reps / 30.0), 1) END) AS recent_1rm,
               MAX(CASE WHEN DATE(w.started_at,'localtime') <  DATE('now','-28 days')
                        AND  DATE(w.started_at,'localtime') >= DATE('now','-84 days')
                        THEN ROUND(s.weight_kg * (1.0 + s.reps / 30.0), 1) END) AS prior_1rm
        FROM sets s
        JOIN exercises e ON e.id = s.exercise_id
        JOIN workouts w ON w.id = s.workout_id AND w.ended_at IS NOT NULL
        WHERE s.user_id = ?
        GROUP BY s.exercise_id
        HAVING recent_1rm IS NOT NULL
           AND prior_1rm IS NOT NULL
           AND session_count >= 4
           AND recent_1rm <= prior_1rm * 1.02
        ORDER BY (prior_1rm - recent_1rm) DESC
        LIMIT 6
        """,
        (uid,),
    ) as cur:
        stalled = [r["name"] for r in await cur.fetchall()]
```

with:

```python
    # Stalled exercises — no meaningful 1RM progress in the last 28 days
    # vs. the 28–84-day window before that. Bodyweight-equipment exercises
    # are excluded for the same reason as the top-lifts query above.
    async with conn.execute(
        """
        SELECT e.name,
               COUNT(DISTINCT DATE(w.started_at,'localtime')) AS session_count,
               MAX(CASE WHEN DATE(w.started_at,'localtime') >= DATE('now','-28 days')
                        THEN ROUND(s.weight_kg * (1.0 + s.reps / 30.0), 1) END) AS recent_1rm,
               MAX(CASE WHEN DATE(w.started_at,'localtime') <  DATE('now','-28 days')
                        AND  DATE(w.started_at,'localtime') >= DATE('now','-84 days')
                        THEN ROUND(s.weight_kg * (1.0 + s.reps / 30.0), 1) END) AS prior_1rm
        FROM sets s
        JOIN exercises e ON e.id = s.exercise_id
        JOIN workouts w ON w.id = s.workout_id AND w.ended_at IS NOT NULL
        WHERE s.user_id = ?
          AND COALESCE(e.equipment, '') != 'Bodyweight'
        GROUP BY s.exercise_id
        HAVING recent_1rm IS NOT NULL
           AND prior_1rm IS NOT NULL
           AND session_count >= 4
           AND recent_1rm <= prior_1rm * 1.02
        ORDER BY (prior_1rm - recent_1rm) DESC
        LIMIT 6
        """,
        (uid,),
    ) as cur:
        stalled = [r["name"] for r in await cur.fetchall()]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_training_profile.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Run the full coach test suite to check for regressions**

Run: `pytest tests/test_coach.py -v`
Expected: PASS (no failures — this task doesn't change `_build_prompt()`'s
handling of `top_lifts`/`stalled`, only which rows reach it)

- [ ] **Step 7: Commit**

```bash
git add app/utils/training_profile.py tests/test_training_profile.py
git commit -m "fix(coach): exclude bodyweight exercises from e1RM/stalled queries"
```

---

### Task 2: Disable Ollama's hidden reasoning pass

**Files:**
- Modify: `app/utils/ollama.py:93-103` (the `payload` dict in `chat_json()`)
- Create: `tests/test_ollama.py`

**Interfaces:**
- Consumes: `app.utils.ollama.chat_json(system, user, schema, *, model=None,
  temperature=0.4, timeout=240.0, num_ctx=None, on_tokens=None) -> dict`
  (existing signature, unchanged).
- Produces: no interface change — every call to `chat_json()` now sends
  `"think": False` in its request payload. No caller-visible behavior change
  for non-reasoning models (they ignore the field).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ollama.py`:

```python
import json as _json

import pytest

from app.utils import ollama


class _FakeResponse:
    """Mimics httpx.Response for the non-streaming chat_json() path."""

    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Mimics httpx.AsyncClient for the non-streaming path and records the
    JSON body passed to post() so the test can assert on it."""

    last_payload = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None):
        _FakeAsyncClient.last_payload = json
        return _FakeResponse({"message": {"content": '{"ok": "ok"}'}})


@pytest.mark.asyncio
async def test_chat_json_disables_thinking_non_streaming(monkeypatch):
    monkeypatch.setattr(ollama.httpx, "AsyncClient", _FakeAsyncClient)

    result = await ollama.chat_json(
        "system prompt", "user prompt",
        {"type": "object", "properties": {"ok": {"type": "string"}}, "required": ["ok"]},
    )

    assert result == {"ok": "ok"}
    assert _FakeAsyncClient.last_payload["think"] is False


class _FakeStreamResponse:
    """Mimics the httpx streaming Response yielded inside `async with
    client.stream(...)`, emitting one complete Ollama-shaped JSON line."""

    def __init__(self, content: str):
        self.status_code = 200
        self._content = content

    async def aiter_lines(self):
        yield _json.dumps({"message": {"content": self._content}, "done": True})

    async def aread(self):
        return b""


class _FakeStreamCM:
    def __init__(self, content: str):
        self._resp = _FakeStreamResponse(content)

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *args):
        return False


class _FakeAsyncClientStreaming:
    """Mimics httpx.AsyncClient for the streaming path (on_tokens set) and
    records the JSON body passed to stream() so the test can assert on it."""

    last_payload = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, method, url, json=None):
        _FakeAsyncClientStreaming.last_payload = json
        return _FakeStreamCM('{"ok": "ok"}')


@pytest.mark.asyncio
async def test_chat_json_disables_thinking_streaming(monkeypatch):
    monkeypatch.setattr(ollama.httpx, "AsyncClient", _FakeAsyncClientStreaming)

    async def _on_tokens(count):
        pass

    result = await ollama.chat_json(
        "system prompt", "user prompt",
        {"type": "object", "properties": {"ok": {"type": "string"}}, "required": ["ok"]},
        on_tokens=_on_tokens,
    )

    assert result == {"ok": "ok"}
    assert _FakeAsyncClientStreaming.last_payload["think"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ollama.py -v`
Expected: both tests FAIL with `KeyError: 'think'` (the payload dict has no
`"think"` key yet).

- [ ] **Step 3: Add `think: False` to the request payload**

In `app/utils/ollama.py`, replace the `payload` dict (currently lines 93-103):

```python
    payload = {
        "model": model,
        "stream": on_tokens is not None,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "format": schema,
        "keep_alive": -1,
        "options": opts,
    }
```

with:

```python
    payload = {
        "model": model,
        "stream": on_tokens is not None,
        # Disable hidden reasoning on models that support it (e.g. qwen3).
        # Nothing in this app reads message.thinking — every caller wants a
        # fast, structured JSON answer. Non-reasoning models ignore this
        # field entirely, so it's safe to send unconditionally.
        "think": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "format": schema,
        "keep_alive": -1,
        "options": opts,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ollama.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the full coach test suite to check for regressions**

Run: `pytest tests/test_coach.py -v`
Expected: PASS (no failures — `coach.py`'s tests mock `ollama.chat_json`
directly, not the httpx layer, so they're unaffected by this change)

- [ ] **Step 6: Commit**

```bash
git add app/utils/ollama.py tests/test_ollama.py
git commit -m "fix(coach): disable Ollama's hidden reasoning pass (think: false)"
```

---

### Task 3: Stop the system prompt's worked example from being copied verbatim

**Files:**
- Modify: `app/routes/coach.py:478-511` (`_SYSTEM_PROMPT`)
- Modify: `tests/test_coach.py` (add one test)

**Interfaces:**
- Consumes: none from earlier tasks.
- Produces: no interface change — `coach._SYSTEM_PROMPT` remains a module-level
  `str` constant; only its content changes.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_coach.py` (append at the end of the file):

```python
def test_system_prompt_forbids_copying_example_numbers():
    assert "placeholders, not real prescriptions" in coach._SYSTEM_PROMPT
    assert "Never reuse these exact weights" in coach._SYSTEM_PROMPT
    # The old example's specific weight/rep combination must not survive —
    # a real generation against live data reused these numbers verbatim.
    assert '"@ 28 kg — increase by 2 kg when hitting 12"' not in coach._SYSTEM_PROMPT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_coach.py::test_system_prompt_forbids_copying_example_numbers -v`
Expected: FAIL — `_SYSTEM_PROMPT` doesn't yet contain
`"placeholders, not real prescriptions"`.

- [ ] **Step 3: Rewrite the worked example in `_SYSTEM_PROMPT`**

In `app/routes/coach.py`, replace the `_SYSTEM_PROMPT` constant (currently
lines 478-511) in full:

```python
_SYSTEM_PROMPT = (
    "You are an elite strength & conditioning coach with 20 years of experience "
    "programming for powerlifters, bodybuilders, and general-population clients.\n\n"
    "Your ONLY job is to read the athlete's real logged data — their actual "
    "movements, estimated 1RMs, weekly muscle volumes, recovery state, and goal — "
    "and produce a specific, personalised weekly plan. A plan that could apply to "
    "anyone is a failed plan.\n\n"
    "Non-negotiable principles:\n"
    "- SPECIFICITY: reference the athlete's actual lifts and loads. If they squat "
    "120 kg e1RM, write '4×4 @ 100 kg' not '4×5'. If their chest is undertrained, "
    "every session in a full-body split includes a chest movement.\n"
    "- COMPOUND ANCHOR: every session opens with 1–2 heavy compound lifts from "
    "the allowed list, in order of loading demand.\n"
    "- PROGRESSIVE OVERLOAD: every exercise note specifies exactly how to progress "
    "(weight increment, rep target, or deload trigger).\n"
    "- BREVITY: each note is ONE cue of at most 12 words — never a paragraph.\n"
    "- RECOVERY: 48 h minimum between heavy loading of the same primary muscle.\n"
    "- VOLUME BALANCE: target 10–20 hard sets per primary muscle per week; "
    "undertrained muscles receive proportionally more work.\n"
    "- PROVEN SPLITS: Full Body, Upper/Lower, Push/Pull/Legs only.\n"
    "- VARIETY: every day in the week must be distinct — never return two days "
    "with the same exercise list, and rotate movement variations across the week "
    "rather than repeating one exercise on most days.\n\n"
    "You ONLY use exercise names from the provided ALLOWED list, spelled exactly. "
    "You return your answer strictly as JSON matching the schema — zero prose outside the JSON.\n\n"
    "Example of one well-formed day (follow this JSON SHAPE only — the "
    "numbers below are placeholders, not real prescriptions. Never reuse "
    "these exact weights, reps, or wording in your actual answer; every "
    "load and rep target must come from the athlete's own profile data "
    "above):\n"
    '{"focus": "Push", "exercises": ['
    '{"name": "Bench Press", "sets": 4, "reps": "5", "note": "@ 100 kg — add 2.5 kg when all reps clean"}, '
    '{"name": "Overhead Press", "sets": 3, "reps": "8", "note": "@ 50 kg — add 1 rep/week to 10, then +2.5 kg"}, '
    '{"name": "Incline Dumbbell Press", "sets": 3, "reps": "10-12", "note": "@ 20 kg — increase by 2 kg when hitting 12"}, '
    '{"name": "Lateral Raise", "sets": 3, "reps": "15", "note": "@ 8 kg — slow eccentric, increase when form is solid"}, '
    '{"name": "Tricep Pushdown", "sets": 3, "reps": "12", "note": "@ 25 kg — add 2.5 kg every 2 weeks"}'
    "]}"
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_coach.py::test_system_prompt_forbids_copying_example_numbers -v`
Expected: PASS

- [ ] **Step 5: Run the full coach test suite to check for regressions**

Run: `pytest tests/test_coach.py -v`
Expected: PASS (no failures — no other test asserts on `_SYSTEM_PROMPT`'s
exact content)

- [ ] **Step 6: Commit**

```bash
git add app/routes/coach.py tests/test_coach.py
git commit -m "fix(coach): stop system prompt's example numbers from being copied verbatim"
```

---

### Task 4: Full regression pass and manual verification

**Files:** none (verification only)

**Interfaces:** none — this task verifies Tasks 1-3 together.

- [ ] **Step 1: Run the full test suite**

Run: `pytest -v`
Expected: PASS, 0 failures (all pre-existing tests plus the 5 new tests
from Tasks 1-3: 2 in `test_training_profile.py`, 2 in `test_ollama.py`, 1
added to `test_coach.py`)

- [ ] **Step 2: Manually regenerate a real plan against the live Ollama server**

This step needs the live networked Ollama server reachable (the one at
`OLLAMA_URL` in `.env`) and must run after this branch is merged and the
`fitstorm` service restarted (Python route/util changes require a restart —
no hot reload in production). From the project root, with the venv active
and the same env vars loaded as production:

```bash
python3 -c "
import asyncio
import app.db as db
from app.routes.coach import build_profile, _exercise_catalog, _build_prompt, _plan_schema, _catalog_names, _SYSTEM_PROMPT
from app.utils import ollama

async def main():
    conn = await db.open_db('fittrack.db')
    uid = 2
    profile = await build_profile(conn, uid)
    catalog = await _exercise_catalog(conn, uid, profile.get('preferred_equipment'))
    prompt = _build_prompt('general', 3, profile, catalog, '')
    allowed_names = _catalog_names(catalog)
    schema = _plan_schema(3, 5, 8, allowed_names)
    raw = await ollama.chat_json(_SYSTEM_PROMPT, prompt, schema, timeout=280.0, temperature=0.2, num_ctx=8192)
    import json
    print(json.dumps(raw, indent=2))
    await conn.close()

asyncio.run(main())
"
```

Expected: the printed plan's exercise notes reference loads that plausibly
derive from the athlete's actual profile (not the old example's 90/55/28/12/30
kg pattern), no note mixes a duration unit with a kg progression cue for a
bodyweight/core exercise, and the profile's `top_lifts`/`stalled` sections no
longer contain names of exercises tagged `Bodyweight` equipment (Plank,
Crunch, Bicycle Crunch, Reverse Crunch, Russian Twist, Push-up, Pull-up,
etc. — cross-check against `app/data/exercises.py` if in doubt).

- [ ] **Step 3: Report results**

No commit for this task — it's verification only. Report the full test
suite pass/fail count and a summary of the manual generation's output
quality (or any remaining issues found) back before moving to finishing the
branch.
