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
- `streak.py` — `max_streak()` will exist after the FitStorm rename PR ships

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

**Depends on:** FitStorm rename PR shipped (for consistency). No schema changes needed.

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
