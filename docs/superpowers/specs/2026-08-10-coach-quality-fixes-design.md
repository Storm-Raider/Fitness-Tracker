# AI Coach Quality Fixes — design

**Date:** 2026-08-10
**Status:** Approved, ready for implementation plan
**Tracks:** Ad-hoc user report ("the coach is really bad") surfaced right after
switching the AI Coach's Ollama backend from a local `qwen2.5:3b` install to
`qwen3:8b` running on a networked machine.

## Problem

The user reported the AI Coach's generated plans got noticeably worse and
asked for the coach's instructions to be "revamped." Reproducing a real
generation against the actual profile-building and prompt code (not a
synthetic test) turned up three distinct, independently-confirmed root
causes — none of which is really about prompt wording quality, and none of
which is specific to the model swap except #2:

### 1. e1RM math applied to bodyweight exercises (pre-existing, unrelated to the model swap)

`app/utils/training_profile.py`'s top-lifts query estimates a 1-rep max with
the Epley formula (`weight_kg * (1 + reps / 30)`) for every exercise where
`s.weight_kg > 0`. For loaded lifts (Barbell/Dumbbell/Cable/Machine) that's
correct. But the app intentionally stores `weight_kg = bodyweight + added
weight` for bodyweight exercises (see `app/routes/workouts.py`'s
`weight_kg` field comment: "effective load (bodyweight + added for BW
sets)") — so a plain Crunch with no added weight still has `weight_kg ≈
60`. Verified against live data: a real athlete's logged Crunches, Reverse
Crunches, Bicycle Crunches, and Russian Twists produced fictional "1RMs" of
80-92kg, which the coach prompt then dutifully echoed as real prescriptions
(e.g. a generated note read `"Plank" — "BW+10 kg — add 5 sec/week to 90,
then +10 kg"`, mixing a duration-based hold with a weight-progression cue).
This is a data-correctness bug that would affect any model, including the
one previously in use — it just hadn't been raised before now. The same
Epley formula is reused in the "stalled lifts" query and has the identical
bug.

### 2. Hidden reasoning overhead on qwen3 (model-swap-specific)

`qwen3:8b` is a reasoning model — by default Ollama has it think through a
response before answering, returned in a separate `message.thinking` field
that `ollama.chat_json()` already ignores (so it isn't corrupting parsed
JSON), but it isn't free: measured directly against the athlete's real
Ollama server, a trivial request took 40s (36s of load+reasoning) with
thinking enabled vs. 3.2s with `think: false` passed. The app's context-size
budget (`num_ctx`, computed in `coach.py`) and generation timeouts were
tuned assuming no hidden reasoning pass, so real generations run slower and
some of the context budget goes to reasoning the app never surfaces or
uses.

### 3. System-prompt worked example gets copied verbatim (contributing, not root cause)

`coach.py`'s `_SYSTEM_PROMPT` includes one fully-worked example day with
concrete numbers (e.g. `"@ 28 kg — increase by 2 kg when hitting 12"` for an
Incline Dumbbell Press). A real generation against a live athlete profile
reused those exact numbers — `28 kg` and `12 kg` — for the same-shaped
exercises in its own output, rather than computing loads from the athlete's
actual profile data. The model is pattern-matching the example's specific
numbers, not just its structure.

## Design

### Fix 1: exclude bodyweight-equipment exercises from e1RM queries

File: `app/utils/training_profile.py`

Both the top-lifts query and the stalled-lifts query add
`COALESCE(e.equipment, '') != 'Bodyweight'` to their `WHERE` clause. Using
`COALESCE(..., '')` keeps exercises with a `NULL` equipment value included
(same null-safety pattern already used elsewhere in this file, e.g. the
preferred-equipment query) — only exercises explicitly tagged `Bodyweight`
are excluded.

Top-lifts query (was lines 77-89):

```python
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

Stalled-lifts query (was lines 161-184):

```python
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

No changes needed downstream — `_build_prompt()` in `coach.py` already
handles an empty `top_lifts`/`stalled` list gracefully (the `if
profile["top_lifts"]:` / `if profile.get("stalled"):` guards skip the line
entirely), so an athlete who has only ever logged bodyweight work simply
gets no 1RM/stalled-lift lines instead of fictional ones.

### Fix 2: disable Ollama's hidden reasoning pass

File: `app/utils/ollama.py`

Add `"think": False` to the request payload in `chat_json()` (the `payload`
dict built around line 93):

```python
    payload = {
        "model": model,
        "stream": on_tokens is not None,
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

This is unconditional, not configurable — nothing in the app reads
`message.thinking` today, and every one of `chat_json()`'s four call sites
(three in `coach.py`, one in `ollama.py`'s own `warm_up()`) wants a fast,
structured JSON answer, not a reasoning trace. Non-reasoning models (like
the previous `qwen2.5` default) silently ignore an unsupported `think`
field, so this is safe regardless of which model is configured.

### Fix 3: stop the system prompt's example from being copied verbatim

File: `app/routes/coach.py`, `_SYSTEM_PROMPT` (lines 478-511)

Two changes to the existing worked example:

1. Replace the example's concrete weights with values deliberately shaped
   to look like placeholders rather than plausible real prescriptions —
   round, obviously-illustrative numbers spread far apart so nothing reads
   as "the" answer to copy.
2. Add one explicit rule stating the example is for shape only and its
   numbers must never appear in real output.

New closing block of `_SYSTEM_PROMPT` (replaces the existing "Example of
one well-formed day" paragraph):

```python
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
```

(Round numbers — 100/50/20/8/25 — replace the original 90/55/28/12/30 so
nothing here happens to coincide with a real athlete's computed target and
get mistaken for "the" answer; the actual anti-copy instruction is what
does the real work.)

## Edge cases

- **Athlete has logged only bodyweight work:** `top_lifts` and `stalled`
  become empty lists after the equipment filter. Already handled — see Fix
  1's note on existing `if` guards in `_build_prompt()`.
- **`equipment` is `NULL` for some exercise:** treated as "not Bodyweight"
  and kept in the results, matching this file's existing null-safety
  pattern (`COALESCE(e.equipment, 'Other')` in the preferred-equipment
  query a few lines below).
- **Non-reasoning models (e.g. a future switch back to `qwen2.5`):** the
  `think` field is simply unused/ignored by Ollama for models that don't
  support reasoning — confirmed no error path needs to branch on model
  capability.
- **Profile cache:** entries already cached (30-minute TTL, see
  `_PROFILE_CACHE`) may still hold stale/bad e1RM values for up to 30
  minutes after deploy. Not worth an explicit cache-bust for a TTL this
  short.

## Testing

- `tests/test_training_profile.py` (new file):
  - `top_lifts` excludes a `Bodyweight`-equipment exercise even when its
    logged `weight_kg` is > 0.
  - `top_lifts` still includes `Barbell`/`Dumbbell`/`Cable`/`Machine`/
    `Kettlebell` exercises as before (no regression).
  - `stalled` excludes a `Bodyweight`-equipment exercise from its
    plateau check even when its Epley numbers would otherwise qualify.
- `tests/test_ollama.py` (new file):
  - `chat_json()`'s outgoing POST payload includes `"think": False` (mock
    `httpx.AsyncClient` and assert on the captured JSON body), covering
    both the streaming and non-streaming code paths.
- `tests/test_coach.py`:
  - `_SYSTEM_PROMPT` contains the anti-copy instruction text (substring
    check) and no longer contains the original example's specific numbers
    (90/55/28/12/30) as this exact combination.

## Out of scope

- A broader rewrite of the AI Coach's rules, scoring, or prompt structure
  beyond these three fixes.
- Any UI/env-var toggle for reasoning behavior — `think: false` is
  unconditional.
- Retroactively correcting already-generated/saved `coach_plans` rows built
  from the bad e1RM data before this fix ships.
- Re-tuning the `num_ctx` sizing formula in `coach.py` beyond what
  disabling hidden reasoning already buys back.
