# TODOS

Deferred work from engineering + design reviews. Each item has enough context to pick up cold.

---

## TODO-stats-1: /stats Analytics Page

**What:** Build a dedicated `/stats` route and template with sparkline charts — weekly volume trend, top exercises by frequency, avg session duration over time, and muscle group coverage for the current week.

**Why:** The dashboard has good at-a-glance numbers. `/stats` is for the user who wants to see the arc of their training — not just this week, but the last 12 weeks, which exercises dominate, and which muscle groups are being neglected. The dashboard stays clean; `/stats` becomes the deep-dive destination.

**What already exists (do not rebuild):**
- `app/utils/charts.py` — `generate_sparkline(values, labels, color, width, height, unit)` fully built, used by metrics and exercises routes. Zero callers from dashboard context — free to use.
- `app/utils/heatmap.py` — heatmap generator already in use
- `exercise_muscles` table — 314 rows, muscle/is_primary, ready for muscle-coverage queries
- `streak.py` — `max_streak()` already exists

**Scope (minimum viable /stats):**
1. New route `GET /stats` in `app/routes/` (or add to dashboard.py)
2. Template `app/templates/stats.html` — card grid + sparkline charts
3. Nav link added to `base.html` alongside Dashboard, Workouts, Exercises
4. Queries:
   - Weekly volume (last 12 weeks): `GROUP BY strftime('%Y-%W', started_at)`
   - Top 5 exercises by set count (all time): `GROUP BY exercise_id ORDER BY COUNT(*) DESC LIMIT 5`
   - Muscle coverage this week: `JOIN exercise_muscles WHERE DATE(w.started_at) >= date('now','-6 days')`
5. Wire `generate_sparkline(weekly_volumes, weekly_labels, unit="kg")` for the volume trend chart

**Where to start:** `app/routes/` — add `stats.py`. Copy query pattern from `dashboard.py`. Call `generate_sparkline()` for the weekly volume trend (it returns an inline SVG — same as the heatmap).

**Depends on:** nothing — the FitStorm rename was reversed (2026-09-09); Zenkai is the retained product name. No schema changes needed.

**Effort:** M (human ~2h / CC ~15min)

**SHIPPED 2026-05-17** — `/stats` route + template. Weekly volume sparkline (last 12 weeks), top 5 exercises by set count, muscle coverage this week (primary/secondary chips). Nav link added. 134/134 passing.

---

## TODO-1: Create DEVIATIONS.md

**What:** Create a `DEVIATIONS.md` file in the project root documenting intentional divergences from the original spec files.

**Why:** `rules.yaml` says "Follow backend_spec.yaml strictly" and "do not modify api_contract.yaml." The approved implementation deliberately deviates from these specs. DEVIATIONS.md records the conscious choices so a future reviewer (or future-you) understands why the code doesn't match the spec files — not a mistake, an intentional decision.

**Deviations to document:**
1. `aiosqlite` instead of SQLAlchemy ORM (tasks.yaml specifies SQLAlchemy)
   - Reason: Pi RAM + simplicity. SQLAlchemy adds ~30MB overhead; raw aiosqlite is ~5MB.
2. Tailscale instead of JWT + bcrypt (tasks.yaml has `implement_auth` task)
   - Reason: Solo-user Pi app. Tailscale is the trust boundary. JWT adds latency + complexity with no security benefit for single-user.
3. Base path `/` instead of `/api/v1/` (api_contract.yaml specifies `/api/v1/`)
   - Reason: HTMX app served from root. API consumers can use root paths directly.
4. HTMX + Jinja2 added (backend_spec.yaml describes API-only backend)
   - Reason: Accepted in /office-hours; HTMX-first makes the app immediately usable from day one.

**Where to start:** Create `DEVIATIONS.md` before the first implementation PR. One section per deviation.

**Depends on:** Nothing. Create before first commit.

**SHIPPED 2026-05-16** — `DEVIATIONS.md` exists and is current. Deviation #2 updated to reflect v0.3.0 auth reality (bcrypt + HMAC session tokens, not "no auth layer").

---

## TODO-2: Write README Before Code

**What:** Write `README.md` with one-command install, screenshot placeholder, and feature list. This is the "README-first" requirement from the design doc.

**Why:** The design doc specifies: "Find 3 people before writing a line of code. Show them the README." The README is the validation artifact. Writing it before the code forces clarity on what the app actually does and who it's for.

**Minimum README content:**
1. One-command install: `docker compose up -d`
2. Screenshot placeholder (replace with real screenshot before community outreach)
3. Feature list: log workout → view history → see PRs → export CSV
4. System requirements: Raspberry Pi (or any Linux), Docker, Tailscale
5. One question for testers: "Would you run this?" (not "do you like it")

**Where to start:** `README.md` in project root. Use the approved mockup HTML files as reference for the screenshot (open in browser, screenshot at ~1440px width for dashboard, ~375px for mobile form).

**Depends on:** Nothing. Write before any implementation task.

**SHIPPED 2026-05-17** — README updated: exercise count 105 → 120, routines 14 → 12, /stats page added to features and API table. All five TODO-2 requirements present.

---

## TODO-D1: Create DESIGN.md

**What:** Extract the UI Design System section from the design doc into a standalone `DESIGN.md` file committed with the first template file.

**Why:** The color tokens, typography choices, interaction spec, and touch target sizes need to be in the repo alongside the templates, not in a gstack artifact file. Any developer (or future-you) should find the design system by looking at the repo, not at `~/.gstack/`.

**Content to extract from design doc:**
- Color tokens (CSS custom properties)
- Typography (Geist/IBM Plex, sizes, weights)
- Layout spec (two-panel desktop, single-column mobile)
- Interaction states table
- Touch targets table (52px steppers, 56px Log Set, 44px nav)
- Accessibility notes (contrast ratios, ARIA landmarks)

**Where to start:** `DESIGN.md` in project root. Source: `~/.gstack/projects/Fitness-Tracker/stormraider-unknown-design-20260501-154826.md` sections "UI Design System" through "Accessibility."

**Depends on:** First template file being created (commit DESIGN.md alongside `app/templates/base.html`).

**SHIPPED 2026-05-16** — Full DESIGN.md written from live `base.html` (not from stale gstack artifact). Correct fonts (Barlow + Syne + JetBrains Mono), correct color tokens, layout spec, components reference, touch targets table, interaction states table, accessibility notes.

---

## TODO-D2: HTMX Swap Target Map

**What:** Document `hx-target` element IDs, `hx-swap` strategies, and which template partial is returned for each HTMX action.

**Why:** HTMX partial-update bugs (wrong target, wrong swap strategy) are the most common source of confusing visual bugs. Speccing them upfront prevents the "set logs but wrong part of page updates" class of issue.

**Actions to document:**
1. **Log Set** (`POST /workouts/{id}/sets`) → which element gets updated, which partial
2. **Delete set** (`DELETE /workouts/{id}/sets/{sid}`) → which element gets removed
3. **Exercise selection** (datalist) → no HTMX (client-side only); last-session context read from JSON
4. **Dashboard workout list refresh** → `hx-target`, `hx-swap`, `hx-trigger`
5. **Finish Workout** → redirect or page reload?

**Where to start:** Add an "HTMX Swap Targets" table to the design doc (or to DESIGN.md once it exists). One row per HTMX action.

**Depends on:** Nothing. Can be specced before implementation.

**SHIPPED 2026-05-16** — "HTMX Interaction Map" section added to DESIGN.md. Documents 3 true HTMX usages (invite generate, delete workout, delete metric) and 8 vanilla fetch() interactions on the workout form. Includes rationale for why the workout form uses fetch() instead of HTMX.

---

## TODO-v2-A: Pending Invite List for Admins

**What:** On the `/invite` admin page, show a list of outstanding (unused, non-expired) invite tokens — URL, created timestamp, and a Revoke button that DELETEs the token.

**Why:** Deferred from v0.3.0 design review (decision 1A). The generator-only design (copy link, no list) was chosen to ship faster. Once multi-user is live and admins are actually issuing invites, the list becomes operationally valuable: admins need to see what's outstanding and revoke stale tokens.

**Scope:**
1. Add `GET /invite` → render `invite.html` with query: `SELECT * FROM invite_tokens WHERE used_at IS NULL AND expires_at > datetime('now') ORDER BY created_at DESC`
2. `invite.html`: table with columns Token (last 8 chars), Created, Expires, Revoke (button → `DELETE /invite/{token}`)
3. `DELETE /invite/{token}` → admin-only, hard-delete row from `invite_tokens`
4. Empty state: "No pending invites" message

**Where to start:** After v0.3.0 ships and at least one real invite has been issued. Don't build until the basic invite flow is validated.

**Depends on:** v0.3.0 multi-user auth shipped and working.

**SHIPPED 2026-05-17** — GET /invite queries pending tokens and passes them to template. DELETE /invite/{token} hard-deletes (admin-only). invite.html shows table with token suffix, created/expires timestamps, and HTMX Revoke button. Empty state "No pending invites." 4 new tests, 138/138 passing.

---

## TODO-v2-B: Add user_id/username to Webhook Payloads

**What:** Include `user_id` and `username` in both `pr_achieved` and `session_complete` webhook payloads.

**Why:** With multi-user, webhook consumers (Home Assistant automations, Slack bots, n8n) cannot tell which user hit a PR or finished a session. The payload currently has no identity context. Ambiguous at any user count above 1.

**Current payload gap:**
- `pr_achieved`: `{exercise_name, weight_kg, reps}` — no user identity
- `session_complete`: `{workout_id, sets, volume_kg}` — no user identity

**Fix:** Add `{"user_id": N, "username": "..."}` to both payloads. Read from `current_user` (already available in those route handlers after the multi-user migration).

**Where to start:** `app/routes/workouts.py`, `_fire_webhook` call sites. Bump webhook schema version if consumers rely on the payload structure.

**Depends on:** v0.3.0 multi-user auth shipped (user_id is available in route context after that PR).

**SHIPPED 2026-05-16** — `user_id` and `username` added to both payloads. Two new tests assert the fields on mocked webhook calls. 117/117 passing.

---

## TODO-v2-1: Alembic Migration Setup

**What:** Add Alembic to the project for managing schema migrations when v2 features are implemented.

**Why:** V1 includes a `user_id` scaffold on workouts/sets/body_metrics (INTEGER NULL DEFAULT 1) precisely because v2 will add a `users` table and FK constraints. When v2 arrives, the migration from single-user to multi-user requires:
1. `CREATE TABLE users (id, username, ...)` 
2. `ALTER TABLE workouts ADD CONSTRAINT user_id REFERENCES users(id)` (SQLite doesn't support ADD CONSTRAINT — requires table rebuild)
3. Seeding existing data as `user_id = 1`

Without Alembic, this is a manual `sqlite3` operation on the Pi's live database file.

**Where to start:** `alembic init migrations` in project root. Create initial migration for current schema. Add `alembic upgrade head` to the Docker entrypoint (or run manually before `docker compose up`).

**Depends on:** v2 scope decision (auth, multi-user). Don't add before v2 planning is confirmed.

---

## TODO-EL-1: Fix N+1 query in list_routines

**What:** Rewrite `GET /routines` (`app/routes/routines.py`) to fetch all routine exercises across all routines in a single query instead of one query per routine.

**Why:** The current implementation issues 1 query to fetch routines, then N queries (one per routine) to fetch exercises + muscles. With 14 pre-built global routines always present, every workout form load triggers 15+ DB queries. Negligible at single-user Pi scale (~0.75ms) but grows linearly with routine count.

**Context:** Pre-existing N+1, considered for bundling in the cascading dropdowns PR (2026-05-15) but deferred to keep scope clean (eng review D2). The per-routine query now includes a muscles LEFT JOIN (shipped with cascading dropdowns), so the single-query fix must also carry the muscles aggregation.

**Where to start:** `app/routes/routines.py` — replace the for-loop with a single cross-routine query:
```sql
SELECT r.id, r.name, re.order_idx,
       e.id AS ex_id, e.name AS ex_name,
       json_group_array(json_object('name', em.muscle, 'is_primary', em.is_primary))
           FILTER (WHERE em.muscle IS NOT NULL) AS muscles
FROM routines r
LEFT JOIN routine_exercises re ON re.routine_id = r.id
LEFT JOIN exercises e ON e.id = re.exercise_id
LEFT JOIN exercise_muscles em ON em.exercise_id = e.id
WHERE r.user_id = ? OR r.user_id IS NULL
GROUP BY r.id, e.id
ORDER BY r.name, re.order_idx
```
Then assemble routines + exercises in Python (two-pass: build routine dict, append exercises). Test order_idx preservation carefully.

**Depends on:** Nothing. Can be done independently as a follow-on PR.

**SHIPPED 2026-05-16** — Single cross-routine query replaces the N+1 for-loop. Python assembly builds `routines_map` dict in one pass; routines with no exercises get an empty `exercises: []` via the `ex_id IS NOT NULL` guard. 115/115 passing.

---

## TODO-EL-2: Guard delete button on global routines in frontend

**Status: VERIFIED NO-OP (2026-05-16)**

Routines in the current frontend are rendered as `<option>` elements inside a `<select>` dropdown only. No delete buttons exist in any template for routine entries. The DELETE endpoint has the correct backend guard (`WHERE id = ? AND user_id = ?`). Nothing to change until a routine management UI with per-row delete buttons is added.

Re-action if a dedicated routine management page is built: add `user_id` to `GET /routines` response and conditionally hide delete buttons when `user_id` is null.

**Depends on:** A future routine management UI being built.

---

## TODO-EL-3: Migrate exercise detail page from muscle_primary/muscle_secondary to exercise_muscles table

**SHIPPED (already done — confirmed 2026-06-03)** — `GET /exercises/{id}` already joins `exercise_muscles` and builds `exercise.muscles` list. Template reads `exercise.muscles`. No action needed.

**What:** Update `GET /exercises/{id}` (`app/routes/exercises.py`) and `exercise_detail.html` to read muscle data from the `exercise_muscles` join table instead of the `muscle_primary`/`muscle_secondary` string columns on the exercises table.

**Why:** The cascading dropdown feature (2026-05-15) introduces the `exercise_muscles` table as the normalized source of truth. The exercise detail page still reads from the legacy string columns — these two sources will drift if exercises.py data changes. Migrating the detail page completes the normalization.

**Scope:**
1. Update `GET /exercises/{id}` SELECT to join with `exercise_muscles`: `SELECT e.id, e.name, e.category, e.equipment, e.cue, em.muscle, em.is_primary FROM exercises e LEFT JOIN exercise_muscles em ON em.exercise_id = e.id WHERE e.id = ?`
2. Group muscle rows in Python into `{"muscles": [{name, is_primary}], ...}` before passing to template
3. Update `exercise_detail.html` chips to render from `exercise.muscles` list instead of `exercise.muscle_primary` string

**Where to start:** `app/routes/exercises.py:31-40` (the exercise detail GET route). After doing this, run the exercise detail QA manually for Bench Press.

**Depends on:** Cascading dropdown feature shipped ✓ (2026-05-15) — `exercise_muscles` table exists and is seeded with 314 rows. Ready to action.

**SHIPPED 2026-05-16** — Route rewritten to LEFT JOIN exercise_muscles, Python groups muscle rows into list, template chips iterate over `exercise.muscles`. Two new tests added. 115/115 passing.

---

## TODO-EL-4: Remove muscle_primary and muscle_secondary columns from exercises table

**SHIPPED (already done — confirmed 2026-06-03)** — `DROP COLUMN muscle_primary` and `DROP COLUMN muscle_secondary` migrations already exist in `_MIGRATIONS` in `app/db.py` (indices 28-29). Columns are already removed from the live DB.

**What:** Once TODO-EL-3 ships and the exercise detail page reads from `exercise_muscles`, the `muscle_primary` and `muscle_secondary` columns on the `exercises` table become dead. Remove them: add a migration to `_MIGRATIONS` in `app/db.py` — SQLite doesn't support DROP COLUMN before 3.35; for older Pi SQLite, this requires a table rebuild.

**Why:** Dead columns that stay in the schema forever confuse future contributors and get queried accidentally. Raised by outside voice in /plan-ceo-review 2026-05-15.

**Scope:**
1. Check Pi SQLite version: `sqlite3 --version` — if >= 3.35, use `ALTER TABLE exercises DROP COLUMN muscle_primary; ALTER TABLE exercises DROP COLUMN muscle_secondary`
2. If < 3.35: create `exercises_new`, copy relevant columns, rename — standard SQLite table-rebuild migration
3. Add migration to `_MIGRATIONS` list in `app/db.py` (try/except as with all migrations)
4. Update `exercises.py` data to remove the `muscle_primary`/`muscle_secondary` keys — they're no longer needed after normalization

**Where to start:** Pi SQLite is 3.51.2 (confirmed 2026-05-15) — use `ALTER TABLE exercises DROP COLUMN` directly (supported since 3.35). Add two migrations to `_MIGRATIONS` in `app/db.py`.

**Depends on:** TODO-EL-3 shipped ✓ (2026-05-16). Ready to action.

**SHIPPED 2026-05-16** — Two DROP COLUMN migrations added to `_MIGRATIONS`. UPDATE exercises seed statement trimmed to category/equipment/cue only. `muscle_primary`/`muscle_secondary` keys kept in exercises.py (still needed to seed exercise_muscles). test_db.py assertions updated. 115/115 passing.

---

## TODO-v2-3: Hevy CSV Import Format

**What:** Add support for Hevy's CSV export format in addition to Strong's.

**Why:** Some self-hosters use Hevy. The two formats differ enough to need separate parsing. v1 targets Strong only.

**Hevy format differences:** (to be verified when implementing) Column names and ordering differ. Hevy uses metric by default.

**Depends on:** User request or community feedback after v1 launch.

## Completed

- **#15/#16 — Body measurement unit toggle (cm ↔ in)** — `cm | in` toggle in the Body Metrics page header; preference persisted in `user_settings.pref_body_measurement`; measurements, BMI height, chart, and table all unit-aware; DB always stores cm. **Completed:** 2026-06-03

## ISSUE-24: feat: merge Records + Stats into unified Analytics page

**Source:** auto-triage (2026-06-06)
**Why major:** The implementation spans 7 files (new route, new template, 2 redirect stubs, base.html, dashboard.html, main.py) with substantial new query logic and template authoring, far exceeding the 2-file/~30-line minor threshold.
**Link:** https://github.com/Storm-Raider/Fitness-Tracker/issues/24

**SHIPPED (confirmed 2026-07-12)** — `app/routes/analytics.py` + `analytics.html` live; `prs.py` and `stats.py` are now 301-redirect stubs to `/analytics`. Matches the success criteria below.

## feat: Unified Analytics page (Fixes #24)

**Scope:** 7 files, new route + template, navigation restructure.

### Files to create
- `app/routes/analytics.py` — combine query logic from `prs.py` (108 lines) and `stats.py` (221 lines); omit `pr_timeline`, `top_exercises`, `weekly_muscle_sets`
- `app/templates/analytics.html` — 6 sections in order: heatmap → recovery badges → strength percentiles → PR table → volume trend → stalled exercises

### Files to modify
- `app/routes/prs.py` — replace body with `RedirectResponse("/analytics", status_code=301)`
- `app/routes/stats.py` — replace body with `RedirectResponse("/analytics", status_code=301)`
- `app/main.py` — import `analytics` router and add `app.include_router(analytics.router)`
- `app/templates/base.html` — (a) desktop nav: remove Stats + Records, add Analytics; (b) mobile tab: Records→Analytics (bar-chart-2); (c) mobile tab: Stats→Plan (calendar-check); (d) More sheet: remove Plan mob-more-item block
- `app/templates/dashboard.html` — update 2 `/prs` hrefs to `/analytics`

### Success criteria
- `/analytics` loads all 6 sections in specified order
- `/prs` and `/stats` return 301 redirects
- Mobile tab bar: Home / Sessions / Analytics / Plan / More
- Desktop nav has single Analytics item
- No broken `/prs` or `/stats` references in templates
- `prs.html` and `stats.html` can be deleted in follow-up cleanup — **done 2026-10-10** (with `planner.html` and `coach.html`)

## ISSUE-26: [Bug] Clutter on the dashboard

**Source:** auto-triage (2026-06-09)
**Why major:** auto-fix attempted but the test suite failed; needs manual work
**Link:** https://github.com/Storm-Raider/Fitness-Tracker/issues/26

**SHIPPED 2026-06-12** — `fa6da96` ("Closes #26, closes #25"). Dashboard KPI grid collapsed 3-col → 2-col (dropped stale "Exercises tracked"), removed Total workouts + All-time volume row, stripped duplicate volume/session numbers from the bar chart header.


## ISSUE-25: [Bug] Body metric chart visual

**Source:** auto-triage (2026-06-09)
**Why major:** auto-fix attempted but the test suite failed; needs manual work
**Link:** https://github.com/Storm-Raider/Fitness-Tracker/issues/25

**SHIPPED 2026-06-12** — `fa6da96` ("Closes #26, closes #25"). Metrics charts cap body-weight/measurement x-axis labels at 5 (from 8/6) using a deduplicated evenly-spaced set, fixing label overlap on narrow screens.


## ISSUE-27: [Feature] Acheivements

**Source:** auto-triage (2026-06-11)
**Why major:** Vague feature request requiring product and design judgment to decide which new achievements, thresholds, and criteria to add — no fix is unambiguously correct.
**Link:** https://github.com/Storm-Raider/Fitness-Tracker/issues/27

## Issue #27 — Add More Achievements

**Reporter:** Sibi (in-app feedback, 2026-06-10)
**Page:** `/achievements`

Request to expand the achievements list beyond the current 24. Needs product decisions:
- Which categories are under-represented (e.g. cardio milestones, PR streaks, routine/challenge completions)?
- What thresholds make sense for a solo-user tracker?
- Should existing tiers (bronze/silver/gold) be balanced first?

**Implementation notes:** All achievement logic lives in `app/routes/achievements.py` — add entries to `ACHIEVEMENTS[]` and a corresponding SQL block in `_compute_earned()`. No schema change needed; `user_achievements` table stores arbitrary string IDs.

## ISSUE-28: [Bug] Going to a different page Reese’s the coach request

**Source:** auto-triage (2026-06-19)
**Why major:** The job_id/SSE connection driving generation progress lives only in plan.html's JS state, which is destroyed on navigation; fixing this requires exposing in-memory job state from coach.py through the plan.py route and adding client resume logic across at least 3 files with no single obvious design, so it fails the file-count and ambiguity bars for a minor fix.
**Link:** https://github.com/Storm-Raider/Fitness-Tracker/issues/28

- **SHIPPED 2026-10-10:** `GET /plan` renders the user's in-flight job id (`coach.active_job_id`) and `plan.html` re-attaches through the shared `_followJob()`. Tests: `tests/test_coach_resume.py`, `scripts/e2e/coach_resume.test.mjs`.
- **Coach generation lost on navigation (#28):** `/coach/generate` jobs run as server-side background tasks tracked in-memory (`_JOBS`/`_ACTIVE_BY_USER` in `app/routes/coach.py`) and streamed to the browser via SSE; the job id and `EventSource` only live in `plan.html` JS state. Navigating away mid-generation tears down that state — the job still finishes and auto-saves a draft, but the page shows the empty state until the draft row exists, looking like the request was reset. Needs: expose the user's active job (if any) from `plan.py`'s GET handler, and have `plan.html` detect/resume (reconnect SSE or poll) an in-flight job on load instead of only picking up completed drafts.

## ISSUE-29: [Feature] Backfill Daily log

**SHIPPED 2026-07-13** — `GET /journal/entry?date=...` lookup endpoint plus a date picker + unified `loadEntryForDate()`/`populateForm()` in `journal.html`, replacing the old confirm()-gated, partial-overwrite `fillFromHistory`. Design/plan: `docs/superpowers/specs/2026-07-13-journal-backfill-design.md`, `docs/superpowers/plans/2026-07-13-journal-backfill.md`. Follow-up filed separately: TODO-EL-6 (falsy-zero fields).

**Source:** auto-triage (2026-06-28)
**Why major:** Backfill is a new feature requiring a date-selector UI, changes to saveLog() and fillFromHistory() interaction model, and product/design judgment about editing vs. creating past entries — well beyond a scoped bug fix.
**Link:** https://github.com/Storm-Raider/Fitness-Tracker/issues/29

### #29 — Backfill Daily Log
**Request:** User wants to add daily log entries for past dates, not just today.

**Current state:** `saveLog()` in `app/templates/journal.html` hardcodes `log_date: TODAY`. The backend `POST /journal` already accepts any `log_date` via `LogIn.log_date` — no schema change needed.

**Work needed:**
- Add a `<input type="date">` to the journal form (default = today, max = today to block future dates)
- Update `saveLog()` to read the selected date instead of the `TODAY` constant
- Update `fillFromHistory()` so clicking a past entry loads it for *that* date (not today's), and pre-populates the date picker
- Decide UX: when picking a past date that already has data, auto-load it into the form
- Update the card title ("Today's entry" → "Entry for [date]")
- All changes are in `app/templates/journal.html` (one file); no route/schema/auth changes required

**Reported by:** Lopa via in-app feedback (2026-06-21)

## TODO-EL-5: Single shared DB connection has no lock around explicit transactions

**SHIPPED 2026-07-13** — Added `app.db.write_lock` (single `asyncio.Lock`), wired into all three `BEGIN IMMEDIATE` sites (`workouts.py`, `import_.py`, `auth.py`). Two concurrency regression tests added (invite-accept race, concurrent set-logging), both stable across repeated runs. PR #31.

**What:** `get_db()` (`app/db.py`) yields one module-global `aiosqlite.Connection` for the entire process — there's no per-request connection and no `asyncio.Lock` (or similar) serializing writers. Any route that opens an explicit transaction with `BEGIN IMMEDIATE` (`app/routes/workouts.py:318-345` for PR detection, `app/routes/import_.py:82` for bulk CSV import, and now `app/routes/auth.py`'s `invite_accept_post` for the multi-use invite race-guard) is vulnerable: if two requests both reach their own `BEGIN IMMEDIATE` before either `COMMIT`s/`ROLLBACK`s, the second raises `sqlite3.OperationalError: cannot start a transaction within a transaction` — an unhandled 500 — instead of blocking and then gracefully losing whatever race it was trying to lose.

**Why:** Discovered while implementing multi-use invite links (2026-07-12). A synthetic `asyncio.gather`-based test firing two truly-simultaneous invite-accept requests deterministically crashed this way. The invite-accept fix (wrapping its INSERT+UPDATE in `BEGIN IMMEDIATE`/`COMMIT`/`ROLLBACK`, matching the existing `workouts.py` pattern) is correct for all realistic, non-zero-gap request timing — this TODO is about the deeper, pre-existing architectural gap the synthetic test exposed, not a regression from that feature. See `docs/superpowers/plans/2026-07-12-multi-use-invite-links.md` and the PR at https://github.com/Storm-Raider/Fitness-Tracker/pull/30 for the discovery context.

**Scope:**
1. Decide the fix shape: an `asyncio.Lock` acquired around every `BEGIN IMMEDIATE`/`COMMIT`/`ROLLBACK` block (simplest, serializes all explicit-transaction writers app-wide) vs. moving to a per-request connection pool (bigger change, but removes the single-shared-connection assumption entirely).
2. Apply consistently to all three existing `BEGIN IMMEDIATE` call sites: `app/routes/workouts.py:318-345`, `app/routes/import_.py:82`, `app/routes/auth.py` (`invite_accept_post`).
3. Add a regression test exercising real concurrent requests (e.g. `asyncio.gather` on two requests hitting the same `BEGIN IMMEDIATE` call site) that currently crashes and should instead resolve to one success + one graceful rejection.

**Where to start:** `app/db.py`'s `get_db()`/`open_db()` — this is where the connection is created and where a lock (if that's the chosen fix) would need to live, since all three call sites depend on `Depends(get_db)` returning the same object.

**Depends on:** Nothing. Can be investigated independently of any specific feature.

**Effort:** M — the fix itself is likely small (a lock), but needs careful testing across all three call sites to confirm no new deadlocks or serialization bottlenecks under this app's real (low-concurrency, single-admin/small-friend-group) usage pattern.

---

## TODO-EL-6: Daily Log fields render blank instead of 0 for legitimate zero values

**What:** In `app/templates/journal.html`, fields like `steps`, `water_l`, and `sleep_hrs` are populated with a falsy check — `(entry && entry.x) || ''` in the client-side `populateForm()`, and the equivalent `today_log.x if today_log and today_log.x else ''` server-side in the initial Jinja render. A genuinely-logged `0` (e.g. a rest day with `steps: 0`) is indistinguishable from "no value entered" and renders as a blank field.

**Why:** Found during final review of the Backfill Daily Log feature (2026-07-13, ISSUE-29, ties into `docs/superpowers/plans/2026-07-13-journal-backfill.md`). Pre-existing pattern (not introduced by that feature), but that feature makes editing past entries a first-class flow, giving the bug a data-integrity dimension it didn't clearly have before: load a past date with `steps: 0` → field shows blank → click Save without retyping → the blank is read as `null` → the stored `0` gets silently overwritten with `null`.

**Scope:**
1. Replace the truthy `||` fallback with a nullish check (`entry?.x ?? ''` client-side, `is not none` server-side in Jinja) across all affected fields in `journal.html`: `steps`, `water_l`, `sleep_hrs`, and for consistency check `weight_kg`/`day_number` too (same pattern, though a logged `0` is less realistic for those).
2. Do this consistently in both `populateForm()` (client) and the initial page-load Jinja render — the two entry paths should treat "explicit 0" the same way.

**Where to start:** `app/templates/journal.html` — `populateForm()` (added by the backfill feature) and the pre-existing `value="{{ today_log.x if today_log and today_log.x else '' }}"` Jinja attributes near the top of the form.

**Depends on:** Nothing. Independent of any other feature.

**Effort:** S — mechanical find-and-replace of the falsy check for a handful of fields, plus a couple of regression tests asserting a saved `0` round-trips as `0` not blank.

**SHIPPED 2026-10-10** — Jinja uses `is not none`, `populateForm()` uses a null check; `day_number` deliberately unchanged (day 0 isn't a valid challenge day and it falls back to the active day). Tests: `tests/test_journal.py` (server render), `scripts/e2e/journal_zero.test.mjs` (past date load + Save round trip; 4/4 failed before).

---

## TODO-CC-1: Full custom-challenge builder (numeric rule kind + per-attempt strict/forgiving toggle)

**What:** Extend the custom-challenge `rules_json` shape into an envelope (`{rules, no_fail, allow_partial}` instead of a bare array), add `kind="number"` (target + unit, partial-credit-capable — the "Minimum/Stretch" pattern), and let each custom attempt choose 75-Hard-style strict all-or-nothing reset vs. 75-Medium-style forgiving grace, instead of Approach A's hard-coded forgiving-only policy.

**Why:** The 2026-08-16 Custom Challenge Templates design doc (Approach A, "Minimal Wedge") deliberately ships only the tick-based, forgiving-policy subset of this — the original 2026-05-30 Challenges design doc's "Approach C: Rules engine + custom builder" sketch is the full version, and it's been deferred twice now because no concrete use case has demanded the numeric kind or the strict-mode toggle. If a second real use case shows up that's naturally quantity-based (not tick-based), or someone wants a custom challenge with a real reset-to-Day-1 penalty, this is the follow-on.

**Context (surfaced during `/plan-eng-review` of the Custom Challenge Templates design, Issue 4):** by the time this is picked up, Approach A will already have shipped custom `challenge_attempts` rows with `rules_json` as a bare JSON array. Moving to the envelope format means either a permanent format-detection branch in `attempt_rules()` (distinguish old array-shaped rows from new dict-shaped ones, forever) or a one-time data migration over every existing custom attempt row. This is real, non-zero cost — budget for it, don't treat the schema as free to change.

**Depends on:** Approach A shipped and in real use; a second concrete use case that's naturally quantity-based, not tick-based (per that design doc's Open Questions — the first custom-challenge use case, a habit/practice streak, is fully tick-based and doesn't need this).

**Effort:** M/L — bigger diff than Approach A, touches `attempt_rules()`/`rule_done()`/`day_complete()`/`evaluate_attempt()` plus the format migration above.

---

## TODO-CC-2: Coach progression suggestions (deterministic, lazy)

**What:** After chat ships, add progression suggestions to the Plan page: computed lazily when the page opens (a separate endpoint called after render), with NO hook in the workout-save path and NO LLM call. One-tap Apply bumps the prescribed load; Dismiss is remembered.

**Why:** It is the most coach-like behaviour ("you owned all three sets, want 62.5 kg?") and costs zero API quota. It was fully designed in the 2026-10-04 Coach Chat CEO review (decisions 3 and 24) and then moved out of that effort (CM5) because lean v1 removed its apply path on saved plans and it shares no code with chat.

**Design (so it can be picked up cold):**
- Rule: double progression. Fire when, in the athlete's last 2 sessions containing that plan exercise (match by `exercise_id`), every working set reached the top of the prescribed rep range at RPE <= 8 (or no RPE logged).
- Bump: barbell +2.5 kg, dumbbell +1 kg per hand, machine/cable +2.5 kg, bodyweight and timed holds suggest +reps or +5 s instead; shown in the athlete's unit.
- Cooldown: not again for 7 days after Apply or 14 after Dismiss; never if any set in the last 2 sessions had RPE 10 or a pain note.
- Storage: `coach_suggestions(plan_id, exercise_id, until)` (new migration; migrations are append-only).
- Failure isolation: malformed `plan_json` or missing logs must yield no suggestions and never break the Plan page.

**Pros:** retention, zero API cost, deterministic and unit-testable. **Cons:** constants need tuning with real use; unit conversions and timed exercises are easy to get wrong; needs an apply path on SAVED plans (see TODO-CC-3).

**Context:** you log workouts against saved plans, which are read-only in chat under lean v1. Routines store only `(routine_id, exercise_id, order_idx)`, so changing sets/reps/loads never touches routines; the numbers live only in `coach_plans.plan_json`.

**Effort:** L human / ~2.5 CC hrs. **Priority:** P2. **Depends on:** Coach Chat v1 shipped; TODO-CC-3 (or an equivalent apply path).

---

## TODO-CC-3: Let the coach edit SAVED plans (numeric in place, structural forks)

**What:** Extend chat so a saved plan can be changed: edits that change only sets/reps/notes apply in place; edits that add, remove or swap exercises or days fork into a new draft. The server classifies from the diff.

**Why:** Lean v1 (2026-10-04 Coach Chat CEO review, CM3) makes chat draft-only, so changing a saved plan means regenerating it. The fuller design was already worked out and verified against the code before it was cut, and TODO-CC-2 needs it.

**Design:**
- `coach_plans.rev INTEGER NOT NULL DEFAULT 0`, bumped on every write to `plan_json` or `status`; chat, undo and swap send `base_rev`; re-checked inside the write transaction.
- `coach_messages.role` extended with `event` for non-LLM edits (swap, undo marker); per-message `plan_before` snapshots (keep last 20, prune on write) replace the lean last-3 `undo_json`.
- Fork: new draft from a saved plan (strip `routine_ids`, copy recent history); it replaces the user's single draft, so the UI must confirm first.
- NOTE: the `role` CHECK in `coach_messages` is part of an append-only migration; adding `event` later needs a table rebuild, so decide this before PR2 if there is any chance of doing it.

**Pros:** edit the plan you actually train from. **Cons:** reintroduces the fork/classifier/concurrency machinery lean v1 removed; the riskiest interaction is fork + undo.

**Effort:** L human / ~2 CC hrs. **Priority:** P3. **Depends on:** Coach Chat v1 shipped and real usage showing demand.

---

## TODO-CC-4: Adopt `write_tx` at the other BEGIN IMMEDIATE sites

**What:** Once the shared `write_tx(conn)` helper exists in `app/db.py` (shipped for the coach routes in Coach Chat PR2), move the four older hand-rolled blocks onto it: `app/routes/workouts.py:323` (PR detection), `app/routes/import_.py:84` (bulk import), `app/routes/auth.py:485` (invite accept), `app/routes/routines.py:156`.

**Why:** Each site re-implements lock + `BEGIN IMMEDIATE` + COMMIT/ROLLBACK. One helper removes the duplication and the chance of forgetting the lock (TODO-EL-5 was exactly that bug).

**Pros:** DRY, one place to fix transaction bugs. **Cons:** touches working, tested code for no new behaviour.

**Effort:** S human / ~15 CC min. **Priority:** P3. **Depends on:** Coach Chat PR2 merged.

**SHIPPED 2026-10-10** — all four sites use `write_tx`, as part of the write-isolation fix (every write outside `write_tx` now takes `write_lock`). `tests/test_write_isolation.py` fails if a hand-rolled `BEGIN` or `write_lock` appears outside `app/db.py`.

---

## TODO-CC-5: Fix stale training profile for plan generation (dead `invalidate_profile` hook)

**What:** `invalidate_profile(uid)` in `app/utils/training_profile.py:28` has no callers, so plan GENERATION reads a profile that can be up to 30 minutes stale (`_PROFILE_TTL = 1800`). Pick one fix: (a) bypass the cache in generation too, (b) shorten the TTL, or (c) call `invalidate_profile` from the workout, set and journal save routes.

**Why:** Found while reviewing Coach Chat freshness (2026-10-04 eng review). Someone logs a workout and immediately generates a plan; the coach ignores that workout. Chat avoids this by building a fresh profile each turn (decision D14); generation does not.

**Context:** Measured on the real DB (39 workouts, 442 sets): a fresh `build_profile` takes about 2 ms and `_exercise_catalog` 0.2 ms, so (a) or (b) is nearly free; (c) touches several unrelated routes and silently reintroduces staleness if one path is missed.

**Pros:** closes a freshness bug. **Cons:** (c) spreads edits across workout/journal routes; (a)/(b) change shipped generation behaviour.

**Effort:** S human / ~15 CC min. **Priority:** P3. **Depends on:** nothing.

---

## TODO-CC-6: Remove the pinch-zoom lock from the viewport meta (accessibility debt)

**What:** `app/templates/base.html` line 5 sets `maximum-scale=1.0, user-scalable=no`, which stops people pinch-zooming any page in the app. Remove both after confirming every input is at least 16px (iOS zooms the page on focus into smaller inputs).

**Why:** Low-vision users cannot enlarge text (WCAG 1.4.4 resize failure). Found in the 2026-10-04 Coach Chat design review; the chat adds more small mono text, so it gets slightly worse. Pre-existing and outside that feature.

**Context:** The comment near `base.html:742` ("belt-and-suspenders" 16px inputs) suggests iOS input zoom is partly handled; audit all inputs first.

**Pros:** removes an app-wide accessibility barrier. **Cons:** iOS focus-zoom risk until inputs are audited.

**Effort:** S human / ~15 CC min. **Priority:** P2. **Depends on:** nothing.

**SHIPPED 2026-10-10** — `maximum-scale`/`user-scalable=no` removed from the viewport meta; all fields were already forced to 16px (`base.html`), so iOS focus zoom stays off. Verified with axe-core (`meta-viewport` clean on all 12 pages).

---

## TODO-CC-7: Drag-to-dismiss for the mobile chat sheet

**What:** Let people drag the Coach chat sheet down by its grab handle to dismiss it, in addition to Close, Escape and tap-outside.

**Why:** It is the expected phone gesture; v1 ships without it on purpose (design decision DS-7 in `docs/designs/coach-chat.md`) to avoid a gesture system, but the handle in `mobile-sheet-v2.png` invites it. If this is never built, remove the handle visual.

**Pros:** native feel. **Cons:** touch-event handling, scroll-versus-drag conflicts with the transcript, an accessible alternative, and tests; easy to ship janky.

**Effort:** M human / ~30 CC min. **Priority:** P3. **Depends on:** Coach Chat PR4a shipped.

---

## TODO-CC-8: Migrate `showConfirm` onto the shared `sheet.js` dialog primitive

**What:** After `sheet.js` ships (Coach Chat UI-primitives PR), move `app/templates/base.html`'s `showConfirm` (the delete-confirm bottom sheet, lines ~1530-1578) onto it.

**Why:** Today's confirm sheet has no `role="dialog"`, `aria-modal`, focus trap or `inert`, so keyboard and screen-reader users can tab out of a delete confirmation into the dimmed page; it also leaks its Escape listener. The shared primitive fixes both and removes the duplicated overlay/slide/Escape code. It was deliberately left untouched in the chat PR to avoid regressions in every delete flow.

**Pros:** one accessible sheet implementation. **Cons:** touches every delete confirmation (`htmx:confirm` routes through it) and the app has no automated tests for them, so it needs a careful `/qa` pass.

**Effort:** S human / ~20 CC min. **Priority:** P3. **Depends on:** `sheet.js` shipped.

---

## TODO-EL-7: Dates are converted to local time twice

**What:** `workouts.started_at` (and `ended_at`) are stored in local time already (`DEFAULT (datetime('now','localtime'))`), but 36 queries wrap them in `DATE(..., 'localtime')`, which treats the value as UTC and converts again. 10 queries also compare against `DATE('now', ...)`, which is the UTC date. On a CDT Pi, a workout started between midnight and 05:00 counts as the previous day. The result is wrong days on the dashboard (week volume, streak), analytics (heatmap, stalled windows), achievements, challenge auto-ticks and the coach's profile; and the 28/84-day windows are off by a day for part of each evening.

**Where:** `app/routes/{dashboard,analytics,achievements,workouts}.py`, `app/utils/{challenges,pr_utils,training_profile}.py`. Run `grep -rn "started_at *, *'localtime'" app` and `grep -rn "DATE('now'" app | grep -v localtime`.

**Fix shape:** use `DATE(w.started_at)` everywhere, and `DATE('now','localtime', ...)` for "today". Add a regression test with a workout at 01:30 local that must count for that day. One PR, since the queries must change together.

**Found:** 2026-10-10, while merging the two stalled-lifts queries. **Effort:** S–M. **Priority:** P2 (wrong data, silently).

**SHIPPED 2026-10-10** — all 32 double conversions and 15 UTC "now" comparisons fixed; `tests/test_local_dates.py` pins the process to America/Chicago and checks a 01:30 workout counts for today (challenges, PR date), plus a guard against both patterns.

---

## TODO-EL-8: Five routes 500 on an HTMX request

**What:** `render()` returns `{name}_partial.html` when a request carries `HX-Request`, but `achievements_partial.html`, `analytics_partial.html`, `cardio_partial.html`, `export_partial.html` and `plan_partial.html` don't exist. Nothing sends HTMX requests to those pages today, so it's latent; an `hx-get` or `hx-boost` added later would turn into a TemplateNotFound 500.

**Fix shape:** have `render()` fall back to the full page when the partial is missing. Add a test that walks every route using `render()`.

**Found:** 2026-10-10 (cleanup audit). **Effort:** S. **Priority:** P3.

