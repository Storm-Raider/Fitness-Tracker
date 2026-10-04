# AI Coach Chat — design

**Date:** 2026-10-04
**Status:** Approved in conversation, awaiting written-spec review
**Builds on:** PR #45 (AI Coach backend moved from Ollama to the Gemini API,
default model `gemini-3.5-flash-lite`). This work stacks on that branch.

## Problem

The AI Coach is a one-shot job: the user picks a goal and days/week, the server
builds a prompt from their training profile, Gemini returns one structured plan,
and it is stored as a `coach_plans` draft. There is no conversation. To change
anything ("swap squats for leg press", "day 2 is too long", "my knee hurts") the
user must regenerate the whole plan and hope. The system prompt was also written
for small local Qwen models: it carries a long worked JSON example that models
copy, and it relies on a catalog-sized schema `enum` that Gemini rejects.

The user asked for the system prompt to be redone for Gemini 3.5 Flash-Lite and
for the coach to work as a **conversational chat with memory**.

## Goals

1. Rewrite the coach's prompts for Gemini 3.5 Flash-Lite (generation and chat
   share one persona and rule set).
2. A chat on the Plan page where the user can **refine the current plan** and
   **ask coaching questions**; the coach knows their training profile.
3. **Memory:** each plan keeps its own saved conversation, and the coach can save
   short **durable notes** (preferences, limits) that carry into every future
   chat and plan.
4. Plan edits **apply instantly, with Undo**.
5. Keep quota use predictable: one Gemini request per chat message.

## Non-goals (v1)

- Replacing the goal/days form — plan generation stays as it is (prompt rewrite
  aside). Chat does not create plans from scratch.
- Removing or reordering days. Asking for fewer days gets a reply telling the
  user to generate a new plan. (Adding a day, at index `len+1`, is supported.)
- Editing a *saved* plan in place (see "Saved plans" — they fork instead).
- Streaming chat replies (a reply takes ~3–5 s; plain JSON is enough).
- Cross-plan single mega-thread, summarisation of old history, per-message
  editing/deleting of chat history.

## User-facing behaviour

On **Plan → AI Routine**, under the plan output, a chat panel appears for any
draft or saved AI plan:

- The user types a message (≤500 chars). The coach replies in a short coaching
  voice. If the message asked for a change, the plan above updates immediately;
  changed days get an "edited" chip and a highlighted border, and the latest
  applied edit shows **Undo**. Undo can be pressed repeatedly to walk back.
- A collapsible **Coach notes** list shows what the coach remembers, each with a
  delete button.
- **Draft plans** are editable. **Saved plans** are read-only in chat: the user
  can ask questions; asking for a change makes the coach point to **Edit as new
  draft**, which forks the plan (below).
- With no `GEMINI_API_KEY`, the chat is disabled with the same notice the
  generator already shows.

## Data model

Appended to the end of `_MIGRATIONS` in `app/db.py` (the list is **append-only**
— never insert in the middle; the runner executes one statement per entry, so
each statement below is its own entry):

```sql
CREATE TABLE IF NOT EXISTS coach_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id      INTEGER NOT NULL REFERENCES coach_plans(id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role         TEXT    NOT NULL CHECK(role IN ('user','model')),
    content      TEXT    NOT NULL,
    plan_before  TEXT,                       -- plan JSON before this model message's edit; NULL if no edit
    changed_days TEXT,                       -- JSON array of 1-based day indexes the edit changed
    undone       INTEGER NOT NULL DEFAULT 0, -- 1 once the edit was undone
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
```

Undo needs no versions table: the latest model message with `plan_before IS NOT
NULL AND undone = 0` is restored into `coach_plans.plan_json` and flagged
`undone = 1`. Deleting a plan cascades its messages. Notes are capped at **20
per user**, **≤120 chars** each, case-insensitively unique.

## API

All under `/coach`, user-scoped (other users' plans 404 like the existing
routes), authenticated like the rest of the app.

| Method & path | Purpose |
|---|---|
| `GET /plans/{id}/chat` | History (`messages`, each with `changed_days`/`undone`), `can_edit` (plan is a draft), `notes`. |
| `POST /plans/{id}/chat` | Body `{message, base_message_id}` (`base_message_id` is the id of the newest message the client has seen, `null` for an empty thread). One Gemini call. Returns `{reply, plan, changed_days, notes_added, message_ids}`. `409` if `base_message_id` ≠ the plan's latest message id (changed in another tab); `429` over the per-user cap; `502` with a friendly `detail` on any Gemini failure (nothing saved). |
| `POST /plans/{id}/chat/undo` | Restore the latest un-undone edit; returns the restored `plan`. `404` if there is nothing to undo. |
| `POST /plans/{id}/fork` | Saved plan → new draft (see below). Returns `{plan_id}`. |
| `GET /notes`, `DELETE /notes/{id}` | View / remove durable notes. |

## A chat turn

Memory is real multi-turn `contents` (user/model roles), not history pasted into
one prompt.

1. **Context turn (user role):** the athlete profile (the existing
   `build_profile` data rendered by the shared context renderer), the durable
   notes (`ATHLETE NOTES`, delimited as athlete-stated facts), whether the plan
   is editable, the ALLOWED exercise list, and the **current plan JSON** — rebuilt
   every turn so Undo and edits from other turns are always reflected.
2. **History:** the last **20** messages as plain text. Old plan JSON is not
   repeated. A message whose edit was undone is rendered with the suffix
   `[the athlete undid this edit]`.
3. **Final user turn:** the new message, followed by a short reminder of the
   non-negotiables (flagged pain, only allowed exercise names, minimal edits).
4. **System instruction:** the shared coach persona and the chat rules (below).

**Reply schema** (`responseJsonSchema`, no `enum` — Gemini rejects large enums;
field `description`s carry the guidance):

```jsonc
{
  "reply":    "string, <=600 chars, plain text, coach voice, no markdown",
  "days":     [ {"index": 1-based int, "focus": "...", "exercises": [ {name, sets, reps, note} ]} ],
  "remember": [ "string, <=120 chars" ]
}
```

`days` is empty when nothing changes; `remember` is empty unless the athlete
stated a stable preference or limit.

**Applying the patch (server, deterministic):**

1. If the plan is not a draft, ignore `days` entirely.
2. For each returned day with `1 <= index <= min(len(plan.days)+1, 7)`: resolve
   exercise names with the existing `_normalise_plan` matcher (including the
   `[equipment]` tag strip), dropping unknown names. A day left with no valid
   exercises is **not applied**, and the reply gets a one-line note naming the
   exercise that could not be found.
3. Merge by index into a copy of the plan; run `_repair_plan` and
   `_plan_quality_issues` (existing code) over the merged plan.
4. Compute `changed_days` by diffing old vs new (focus + exercise name/sets/reps/
   note) — the model's claim is never trusted. If nothing differs, it is a
   no-change turn.
5. Write atomically: user message, model message (with `plan_before` and
   `changed_days` when changed), the updated `coach_plans.plan_json`, and any
   accepted `remember` notes. A failed Gemini call writes nothing.
6. `remember` entries are stripped, length-capped, de-duplicated and counted
   against the 20-note cap; extras are dropped silently.

## Prompt rewrite (Gemini 3.5 Flash-Lite)

Applies first to generation, then to chat (shared persona and rules).

- **System instruction** = a short persona (a direct, warm strength coach) plus a
  compact list of positively-phrased rules. The existing rules are kept in
  spirit — specificity, compound anchor, progressive overload, one-cue notes
  (≤12 words), 48 h recovery, 10–20 hard sets/muscle/week, proven splits only,
  respect the athlete's flagged pain / RPE / wellness, daily variety, allowed
  exercise names only — but tightened.
- **Remove the worked JSON example** ("Exercise A–E"). It costs tokens, was the
  source of copy-the-example failures, and is replaced by schema `description`
  fields (e.g. note: "one cue, ≤12 words, includes the load and how to
  progress"). The test that guards against example copying is replaced by a test
  that the prompt contains no example block.
- **Order for a small model:** athlete context and allowed list first, the task
  last, then a short reminder of the safety rules (pain flags), because
  constraints stated only at the top get dropped.
- **Voice:** the plan `summary` and exercise `note`s, and all chat replies, are in
  a coaching voice — second person, direct, encouraging, concise, no emojis, no
  markdown.
- **Chat-only rules:** edit only when asked, change as few days as possible,
  never program through flagged pain, ask a clarifying question instead of
  guessing (returning no `days`), never invent exercises, give no medical
  diagnosis (suggest seeing a professional for persistent pain), say so when a
  request needs a new plan (fewer days), and `remember` only stable preferences
  and limits the athlete states.
- Durable notes are also injected into the **generation** prompt (`ATHLETE
  NOTES`), so they shape future plans.

## Saved plans: fork

A saved plan has real routines (one per day), so editing it in place would mean
rewriting routines the user may have changed or be mid-session on. Instead
`POST /plans/{id}/fork`:

- creates a new `coach_plans` row, `status='draft'`, titled `"<title> (edited)"`,
  same goal/days/model, `plan_json` copied **without** `routine_ids`;
- copies the last 20 messages with `plan_before`/`changed_days` cleared and
  `undone = 0` (a fresh Undo history);
- deletes the user's existing unconfirmed draft first — the app allows one draft
  at a time — which is why the UI confirms before forking;
- leaves the saved plan and its routines untouched; confirming the new draft uses
  the existing `confirm` flow and creates new routines.

## UI

Plan page, AI Routine tab, `app/templates/plan.html`. `DESIGN.md` is read before
building and followed: blue (`--accent`) for interactive chrome and the "edited"
highlight, never gold; no light mode; numbers in JetBrains Mono via `.num`;
primary actions ≥56 px tall (Send), other controls ≥40 px; mobile-first. Components:
message thread, composer, Undo on the latest applied edit, collapsible Coach
notes with delete, read-only banner + **Edit as new draft** for saved plans.

## Limits and failure handling

- **Per-user cap:** 30 messages/hour by default (`COACH_CHAT_MAX_PER_HOUR`),
  counted per attempt in an in-memory sliding window (resets on restart —
  acceptable for a small self-hosted app); over the cap → `429` with the retry
  time.
- **Serial API calls:** chat calls take the existing `_GEN_LOCK` so generation and
  chat never run concurrently (keeps Gemini per-minute limits calm). The existing
  queue-depth cap does not apply to chat; the per-user cap does.
- **Quota:** each chat message is exactly one Gemini request (plus the client's
  transient-error retries). On a small free-tier quota, chat will use it up
  faster than generation; the README documents this.
- **Gemini errors / no key / quota:** inline error bubble, nothing saved, input
  restored. Messages are validated server-side (1–500 chars, non-blank).
- **Concurrency:** `base_message_id` mismatch → `409` ("this plan changed in
  another tab — reload").
- **Prompt injection hygiene:** notes and history are athlete-authored text
  placed in clearly delimited blocks and described as data; the server, not the
  model, enforces what can change (draft-only, valid exercises, index bounds).

## Privacy

Chat messages, durable notes, and the plan are sent to Google (as the generation
prompt already is). They are stored in the app's encrypted database, deleted with
their plan (messages) or individually by the user (notes). The README and the
privacy note in `.env.example` / CHANGELOG are extended.

## Testing

- **Unit:** patch merge, index bounds, unknown-name drop, empty-day rejection,
  changed-day diff, no-change detection, Undo chain, note rules (strip, cap 120,
  dedupe case-insensitively, cap 20), history windowing and the undone-suffix,
  prompt has no example block / correct section order.
- **Endpoint (Gemini mocked):** ownership 404s, draft vs saved behaviour, `days`
  ignored on saved plans, per-user cap `429`, `409` on stale `base_message_id`,
  undo with nothing to undo `404`, fork copies history / drops `routine_ids` /
  replaces the draft, notes endpoints, plan delete cascades messages, and a
  failing Gemini call leaves no rows.
- **Migrations:** new entries are appended at the end; a fresh DB and an
  already-migrated DB both end up with the tables.
- **Live (manual, real model, on a copy of the production DB):** a scripted set of
  realistic turns — a swap, a day edit, a pain report, a question, a request for
  fewer days, a stated preference to remember — checking valid output, minimal
  diffs, safe behaviour, and that the rewritten generation prompt still yields
  complete, valid, non-repetitive plans.

## Delivery

Three PRs, in order, each stacked on the previous (the first on PR #45):

1. **Generation prompt rewrite** (the originally requested change): new shared
   persona/rules, no worked example, schema descriptions, section order, coaching
   voice. Verified live on `gemini-3.5-flash-lite`. Independent of chat.
2. **Chat backend:** migrations, endpoints, turn pipeline, notes, fork, caps, and
   the notes block in the generation prompt.
3. **Chat UI** on the Plan page.

## Open details, decided

| Detail | Decision |
|---|---|
| History window | Last 20 messages |
| Message length | 1–500 chars |
| Reply length | ≤600 chars |
| Durable notes | ≤20 per user, ≤120 chars each |
| Per-user cap | 30/hour, `COACH_CHAT_MAX_PER_HOUR` |
| Max days | 7 (existing limit) |
| Reply format | Single JSON response (no SSE) |
| Undo | Unlimited, newest-first, via `plan_before` on messages |
