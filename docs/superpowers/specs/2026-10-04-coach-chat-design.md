# AI Coach Chat — design

**Date:** 2026-10-04 (rewritten 2026-10-05 to match the reviewed plan)
**Status:** Reviewed (CEO, engineering x2, design). Implementation in progress, see Delivery.
**Source of truth:** this document is the behaviour and interface contract.
`docs/designs/coach-chat.md` is the review record: every decision with its
rationale (D#, CM#, ER#), the full UI specification (DS-1..DS-10, state table,
mockups) and the amendments. If the two ever disagree, the plan's numbered
amendments win and this document is wrong: fix it.
**Builds on:** the Gemini coach (PR #45), default model `gemini-3.5-flash-lite`.

## Problem

The AI Coach is a one-shot job: pick a goal and days per week, the server builds
a prompt from the training profile, Gemini returns one structured plan, stored as
a `coach_plans` draft. There is no conversation. To change anything ("swap squats
for leg press", "day 2 is too long", "my knee hurts") the user regenerates the
whole plan and hopes.

## Goals

1. A chat on the Plan page that **refines the current draft plan** and **answers
   coaching questions**, grounded in the athlete's training profile.
2. **Memory:** each plan has its own saved conversation, and the athlete can keep
   short **durable notes** (limits, preferences) that carry into every future chat
   and plan. The athlete confirms every note; the model only proposes.
3. Edits **apply instantly and reversibly** (Undo), and every change is shown as a
   diff computed by the server, never the model's description of itself.
4. Predictable quota use and failure behaviour on a small self-hosted install
   (~5 users, one Pi, one uvicorn worker).

## Non-goals (v1)

- Creating plans from scratch (generation stays the form).
- Adding, removing or reordering days: the day count is fixed after generation, so
  `days_per_week` never changes. The coach points to regenerating.
- Editing a **saved** plan, or forking one. Saved plans get read-only Q&A and a
  "Regenerate from this chat" action that prefills the generator's focus note from
  the athlete's recent requests (<= 300 chars).
- Progression suggestions (kept in TODOS.md), streaming replies, a cross-plan
  thread or summarisation, editing or deleting single messages, voice input,
  exporting chat or notes, a "clear conversation" control, per-user caps or usage
  stats, model-written notes without confirmation.

## User-facing behaviour

Layout and visuals are specified in the plan (DS-1..DS-10); in short:

- **Desktop (>= 768px):** the 320px left column of the Plan page swaps between
  *Generate* and *Coach* with a `.seg-toggle`; the plan stays in the wide right
  column. **Mobile:** one 56px composer row above the tab bar opens a bottom sheet.
- The athlete types a message (1-500 chars). The coach replies in a short coaching
  voice. If a change was asked for, the draft updates at once; changed days get an
  EDITED label and a neutral row tint, and the reply carries a server-computed
  change summary. **Undo** walks back the last 3 edits. A toast offers Undo when
  the sheet is closed.
- **Swap:** a per-row action lists up to 6 ranked alternatives; choosing one applies
  it instantly (draft plans only) and is undoable. It writes no chat message.
- **Why:** tapping "?" on a row sends a normal chat message asking why.
- **Prompt chips** (empty thread only) seed common requests.
- **Coach notes:** a collapsible list of what the coach remembers, each deletable.
  When the athlete states a stable limit or preference the coach *proposes* a note
  and the UI shows "Remember: ...? Yes / No"; only Yes saves it. Cap 20 notes,
  <= 120 chars each, case-insensitively unique. A full list says so honestly.
- **Feedback chip:** the coach may propose a value for the existing plan feedback
  (`too_easy`, `just_right`, `too_hard`, `skipped_often`); Yes calls the existing
  endpoint and shows the old value before replacing it.
- **Saved plans** open read-only in the same panel (questions only).
- **First use:** a blocking privacy card (messages and notes go to Google); the
  acknowledgement is stored per user.
- **Disabled states:** no `GEMINI_API_KEY` uses the generator's existing notice; the
  kill switch hides the panel; at the daily cap the composer is disabled with
  "Coach is resting until tomorrow. Undo and swaps still work."

## Data model

Appended to the end of `_MIGRATIONS` in `app/db.py` (**append-only**; the runner
executes one statement per entry, so each statement is its own entry, all
idempotent with `IF NOT EXISTS`):

```sql
CREATE TABLE IF NOT EXISTS coach_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id      INTEGER NOT NULL REFERENCES coach_plans(id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role         TEXT    NOT NULL CHECK(role IN ('user','model')),
    content      TEXT    NOT NULL,
    changed_days TEXT,                        -- JSON array of 1-based day indexes
    changes      TEXT,                        -- JSON server-computed change summary
    undone       INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
)
CREATE INDEX IF NOT EXISTS idx_coach_messages_plan ON coach_messages(plan_id, id)
CREATE TABLE IF NOT EXISTS coach_notes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    text           TEXT    NOT NULL,
    source_plan_id INTEGER REFERENCES coach_plans(id) ON DELETE SET NULL,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(user_id, text COLLATE NOCASE)
)
ALTER TABLE coach_plans ADD COLUMN updated_at TEXT            -- last activity; drives the 7-day purge only
ALTER TABLE coach_plans ADD COLUMN rev INTEGER NOT NULL DEFAULT 0   -- compare-and-set token
ALTER TABLE coach_plans ADD COLUMN undo_json TEXT             -- last 3: [{message_id|null, plan_json, label}]
CREATE TABLE IF NOT EXISTS coach_usage (
    day   TEXT PRIMARY KEY,                   -- America/Los_Angeles date
    count INTEGER NOT NULL DEFAULT 0
)
ALTER TABLE user_settings ADD COLUMN coach_chat_ack_at TEXT
```

Deleting a plan cascades its messages. Drafts purge after 7 days without activity
(`COALESCE(updated_at, created_at)`, localtime). A failing non-benign migration is
recorded as applied and never retried by `init_db`, hence the migration test below.

## API

All under `/coach`, authenticated, user-scoped. The paths below are final for chat,
undo, notes, ack and usage (PR #50) and swap (PR #51). Errors are
`{detail, kind}`. Another user's plan is a 404 on `GET .../chat` and a 409 "replaced"
on the writes (a missing plan and someone else's are indistinguishable by design).

| Endpoint | Purpose |
|---|---|
| `GET /plans/{id}/chat` | `{plan, rev, status, can_edit, messages, notes, note_cap, feedback, enabled, at_cap, acked, max_message_chars, has_undo, undo_label}` (last 100 messages). Also how a saved plan opens read-only. |
| `POST /plans/{id}/chat` | `{message, base_rev}` -> `{reply, plan, rev, changed_days, changes, propose_note, notes_full, feedback: {value, current} \| null, message_ids, has_undo, undo_label}`. `base_rev` is required for drafts and ignored for saved plans. |
| `POST /plans/{id}/undo` | `{base_rev}` -> restored plan and new rev. |
| `GET /plans/{id}/swap?day=&idx=[&base_rev=]` | Up to 6 ranked alternatives `{exercise_id, name, equipment, muscle, category}` for one exercise of a draft: same primary muscle, not already on that day, not used on another day first, most similar name first, preferred equipment, staples; anything a painful area (profile flag, said in the thread, or a note) rules out is left out. |
| `POST /plans/{id}/swap` | `{base_rev, day, idx, exercise_id}` -> `{plan, rev, changed_days, changes, swapped: {day, idx, from, to}, has_undo, undo_label}`. Keeps sets and reps, clears the note, pushes an undo entry with no message id, writes no chat message, spends no model request (works at the cap and with the kill switch on, no privacy ack needed). Drafts only; 409 `saved` / `stale` / `replaced`, 422 `invalid` / `duplicate`. |
| `GET /notes`, `POST /notes`, `DELETE /notes/{id}` | List; confirm a proposed note (`{text, source_plan_id?}`: trimmed to one line of <= 120 chars, duplicates quietly accepted as 200, 409 `notes_full` at 20, 503 with the kill switch); delete. |
| `POST /chat/ack` | The athlete accepted the privacy note (stored per user; chat returns 403 `ack_required` until then). |
| `POST /plans/{id}/confirm` | Existing; now `{base_rev, title}`: 409 on a stale rev, and the posted title is saved (today the title box is cosmetic server-side). |
| `GET /usage` | Admin only: today's count against the cap, per-kind counters since boot. |

Errors map from `GeminiError.kind` (`not_configured, auth, quota, rate_limited,
timeout, unreachable, blocked, empty, malformed, bad_request`) to 503/429/502 with
fixed user-facing copy (plan, state table). A stale `base_rev` is 409 ("plan changed
in another tab"); a plan id that no longer exists is the same 409 ("this plan was
replaced, reload"); a saved plan on an edit endpoint is 409 ("chat and swap edit
drafts; use Regenerate"); a second message while one is running is 409 ("still
working on your last message").

## A chat turn

1. **Gate (no model call):** kill switch (503), ownership, plan exists, `base_rev`
   matches, per-user single-flight, daily cap not reached, a limiter slot within 5 s
   (semaphore of 2, else 429 "busy"), message 1-500 chars.
2. **Context**, built fresh every turn (profile build ~2 ms), ordered stable-first
   and volatile-last so implicit prefix caching works: system instruction, ALLOWED
   exercise catalog, athlete context (the same renderer generation uses, via
   `coach_plan.athlete_context`), durable notes as quoted data, then the **current
   plan JSON** and the history (last 20 messages **and** <= 6,000 characters; a
   message whose edit was undone is suffixed `[the athlete undid this edit]`). The
   allowed list always includes the exercises the athlete names (this message or
   earlier ones) and the plan's own: the per-muscle cap would otherwise leave real
   library exercises off it and the model, told to use only listed names, would
   quietly substitute others (found by the live eval).
3. **Final user turn:** the new message, then a short task line restating this
   week's hard constraints (pain, from the profile's flags or from what the athlete
   said in this message or earlier in the thread, with the area's off-limits
   movements; the fixed day count). A small model honours the end of the message better than the
   middle: the generation eval showed a flagged knee ignored 3 of 3 times until it
   was restated there.
4. **One model request** through `chat_turn_json`. A turn issues at most 2 requests
   across every retry reason, within a 30 s end-to-end budget (20 s per attempt);
   only fast failures (429/5xx/malformed) retry, and only with >= 10 s left; never
   after a timeout.
5. **Reply schema** (`responseJsonSchema`, no enum):
   `{ reply, days: [{index, focus, exercises: [{name, sets, reps, note}]}], propose_note | null, feedback | null }`.
   `days` holds **only changed days**, indexes `1..len(days)`; it is empty when
   nothing changes.
6. **Apply (server, deterministic):** ignore `days` on a saved plan; reject an index
   outside `1..len`; resolve names with `normalise_plan` (tag strip, unknown names
   dropped); a day left with no valid exercise is not applied and the reply says
   which name was not found; merge into a copy; remove within-day duplicates
   (generation's weekly-repeat repair is deliberately NOT run here: it would silently
   swap exercises on days the athlete never mentioned and overwrite their notes);
   **diff old vs new to produce `changed_days` and `changes`**, never trusting the
   model's own account.
7. **Final write, one `write_tx`:** compare-and-set on `rev` first (rowcount 0 ->
   commit, 409, nothing written), then both messages, the new plan, the undo entry
   and `rev + 1`. The user message is persisted only together with the reply, so a
   failed turn leaves no rows (the client keeps the text). The usage counter delta
   flushes in a `finally`.

Undo pops the top `undo_json` entry (<= 3), marks its message `undone`, re-validates
exercise ids against the library (missing ones dropped with a notice) and bumps `rev`.
A corrupt entry is 409 "Can't restore this edit", logged, row untouched.

## Concurrency and transactions

- One shared autocommit connection: `conn.commit` is a no-op and every writer uses
  `write_tx(conn)` (write lock + `BEGIN IMMEDIATE`, non-reentrant, compare-and-set
  first, `WriteConflict` -> 409). **Done in PR #47.**
- Never hold the write lock across the network: the model call finishes before
  `write_tx` starts.
- `rev` is the only stale-tab token; `updated_at` is for the purge.

## Prompts

- **Generation prompt: done in PR #49.** Rules live once in the system prompt; the
  user message is data plus a one-line task; no worked example; athlete-written text
  is marked as data that cannot change the rules; flagged pain and the request are
  restated in the task line.
- **Chat** shares the persona and `athlete_context`, and adds its own rules: edit
  only when asked and change as few days as possible; never program through flagged
  pain; ask a clarifying question instead of guessing (return no `days`); never
  invent exercises; no medical diagnosis (calm, direct, points to a professional for
  persistent pain); decline a day-count change and point to regenerating; propose a
  note only for a stable limit or preference the athlete states; safety rules
  outrank notes.
- Durable notes also reach the **generation** prompt as quoted data.
- Optional `GEMINI_CHAT_MODEL` (defaults to `GEMINI_MODEL`) lets chat use a
  stronger model; the chat route resolves it and `model=` is threaded through the
  client.

## Limits and kill switches

- **Daily cap:** one app-wide counter, authoritative in memory on the hot path
  (incremented before each HTTP request via the client's `on_request` callback, so
  retries and failures count), persisted as a delta to `coach_usage` and seeded at
  startup and on day rollover. Day key is the America/Los_Angeles date (Google's
  quota resets at Pacific midnight). `COACH_AI_MAX_PER_DAY` is set to about 80% of
  the real limit. At the cap: 429 "coach is resting until tomorrow"; swap, undo and
  notes keep working. Generation stops at the cap too.
- **Chat limiter** is separate from the generation lock. **`COACH_CHAT_ENABLED`**
  (default true) off: chat and note-confirm return 503 and the panel is hidden;
  swap, undo and notes view/delete keep working.
- No per-user hourly cap, no 80% notice, no per-user stats.

## Security and privacy

- All model text, change summaries, notes, history and exercise names render as
  plain text (`createElement` + `textContent`). `tests/test_chat_js_safety.py`
  fails on HTML-string APIs in the files that render them (**done in PR #48**);
  `/qa` runs an XSS payload pass for live-DOM behaviour.
- Custom exercise names are allowlisted at creation (letters, digits, spaces,
  `- ' ( ) / + & . ,`, max 60) and sanitised when the catalog is built (hostile names
  are left out of prompts with a logged warning); CSV import keeps names raw so
  repeat imports still dedupe, but invalidates the catalog caches.
- Notes, history and the focus request are athlete-authored data in quoted blocks;
  the server, not the model, decides what can change (draft only, valid exercises,
  index bounds).
- **Privacy:** chat messages, notes and the plan are sent to Google (on the unpaid
  tier Google may use them; the exact Gemini API terms wording must be verified
  before the notice copy is final). They are stored in the app's database, deleted
  with their plan (messages) or individually (notes). Blocking first-use card;
  README, CHANGELOG and `.env.example` extended.

## Testing

- **Unit (pure `coach_chat.py`):** patch merge, index bounds, unknown-name drop,
  empty-day rejection, diff and no-change detection, undo stack (3), note rules,
  history windowing and the undone suffix, task-line constraints.
- **Endpoint (Gemini mocked):** ownership 404s, draft vs saved, `days` ignored on
  saved plans, every 409 and 429 above, kill switch, cap at 100% (including the
  **generation** path via `on_request`), a failing call leaves no rows, notes cap and
  dedupe, plan delete cascades, `asyncio.gather` races modelled on
  `tests/test_workouts.py`.
- **Migrations:** on a fresh DB the error rows equal exactly the frozen benign set
  `{0, 10, 38, 48, 49, 51}`; no error at any new index; new tables, columns and
  indexes exist on a fresh DB **and** on one built from the pre-chat schema.
- **Live eval (manual, never CI):** `scripts/coach_eval.py`. Generation scenarios and
  chat turns (12 scenarios each; PR #49 and #50). Recorded baseline on
  `gemini-3.5-flash-lite`: 22/24, all 6 safety scenarios clean; known weak spots:
  a fatigued muscle on Day 1 and a noise-prone "make it 2 days" decline.
  Gate: >= 80% overall and no safety regression against the baseline.
- **Browser (`/qa`) and a real iPhone:** DOM behaviour, the sheet and keyboard. The
  iPhone check gates PR4a, because headless Chrome cannot reproduce iOS keyboard
  behaviour.

## Delivery

| Step | What | Status |
|---|---|---|
| #45 | Gemini backend, default `gemini-3.5-flash-lite` | merged |
| R (#46) | Plan logic moved to `app/utils/coach_plan.py` | merged |
| W (#47) | `write_tx`, no-op commit, coach writers atomic | merged |
| UI (#48) | `PlanView`/`PlanState`, `sheet.js`, `showActionToast`, `.pill`, `.seg-toggle` | merged |
| PR1 (#49) | Generation prompt rewrite + live eval + baseline (11/12) | merged |
| PR2 (#50) | Chat backend: migrations, `kind` errors, `chat_turn_json`, turn pipeline, undo, notes, daily cap, limiter, kill switch, `/coach/usage`, name allowlist, chat eval scenarios | merged; eval gate met; **before deploy: you set `COACH_AI_MAX_PER_DAY` from the real quota** |
| PR3 (#51) | Swap endpoint (list ranked alternatives, apply, undoable) | merged |
| PR4a (#52) | Chat UI core: panel, transcript, change summaries, privacy card, Undo, notes, saved-plan read-only | merged; privacy copy final (ER-20); **before go-live: real-iPhone check** |
| PR4b | Row menu, swap picker, prompt chips, feedback chip, Why-tap | merged |

**Deploy checklist for every PR with migrations or transaction changes:** check
`systemctl list-timers` and pause the auto-deploy timer if installed, run
`scripts/backup.py`, dry-run migrations on a copy of the production database, merge,
restart, verify `/health`, one generation, one chat turn and one Undo, resume the
timer. Tests, README, CHANGELOG and the privacy copy ship with the PR that
introduces each behaviour.

## Decided details

| Detail | Decision |
|---|---|
| History sent to the model | last 20 messages and <= 6,000 characters |
| Message / reply length | 1-500 chars in / <= 600 chars out |
| Durable notes | <= 20 per user, <= 120 chars, confirmed by the athlete |
| Undo depth | last 3 |
| Max days | 7 (existing limit), fixed after generation |
| Reply format | single JSON response, no streaming |
| Chat budget | 30 s end to end, 20 s per attempt, <= 2 requests per turn |
| Stale-tab token | integer `coach_plans.rev` |
