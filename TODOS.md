# TODOS

Deferred work from engineering + design reviews. Each item has enough context to pick up cold.

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

## TODO-v2-3: Hevy CSV Import Format

**What:** Add support for Hevy's CSV export format in addition to Strong's.

**Why:** Some self-hosters use Hevy. The two formats differ enough to need separate parsing. v1 targets Strong only.

**Hevy format differences:** (to be verified when implementing) Column names and ordering differ. Hevy uses metric by default.

**Depends on:** User request or community feedback after v1 launch.
