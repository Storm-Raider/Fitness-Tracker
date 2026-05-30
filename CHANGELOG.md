# Changelog

All notable changes to FitStorm are documented here.

## [Unreleased]

### Added
- **Undo for deletes** — deleting a workout, set, or cardio session is now recoverable. An "Undo" toast appears after every delete; deleted items are captured into a recycle bin (`deleted_items`) and restored on undo via `POST /undo/{token}`. Tokens are single-use and user-scoped; the bin auto-purges after 7 days. Deleting a workout now also correctly removes its in-workout cardio (previously orphaned).
- **Standalone cardio page** — `/cardio` (in the More menu) for logging cardio outside a workout: pick an activity, date, duration, optional distance, and notes, with a live min/km pace readout. History table shows pace per session with inline delete. Form-based (`POST /cardio` → redirect), cardio-category validated server-side. Complements the existing in-workout cardio logging.
- **AI Coach** — a local Ollama LLM builds a personalised multi-day routine from your training history. New `Coach` page (`/coach`): pick a goal (strength / hypertrophy / balance / general) and days per week; the coach analyses your top movements, per-muscle set coverage, neglected muscle groups, and estimated 1RMs, then returns a structured plan with sets/reps and coaching notes. Review and save — each day is persisted as a user-owned routine usable in the workout logger. Runs fully on-device via [Ollama](https://ollama.com); no data leaves the host. New `coach_plans` table, `app/utils/ollama.py` async client, and `OLLAMA_URL` / `OLLAMA_MODEL` config (default `qwen2.5:3b`). Generated exercise names are validated against the exercise library at save time.
- **Exercise metadata** — category, equipment, primary muscle, secondary muscle, and form cue for all 105 exercises
- **Exercise detail chips** — category / equipment / all muscles (primary + secondary, one chip each) shown as neutral muted chips on exercise detail pages; form cue rendered below; muscle data sourced from `exercise_muscles` join table
- **14 pre-built global routines** — PPL (Push/Pull/Legs), Full Body A & B, Upper/Lower (Upper A/B, Lower A/B), and Bro Split (Chest/Back/Shoulders/Arms/Legs) — visible to all users in the routine dropdown
- **`app/data/` module** — `exercises.py` (105 entries) and `routines.py` (14 routines) as the authoritative data source; replaces inline schema seed
- **Cascading Routine → Muscle Group → Exercise filter** — workout form now has a Muscle Group dropdown between the routine select and the exercise chips; selecting a routine narrows the muscle list to that routine's muscles; selecting a muscle group filters both chips and datalist autocomplete; empty-state messages shown when the intersection is zero
- **`exercise_muscles` table** — normalized one-row-per-muscle storage (314 rows seeded from `exercises.py`); compound strings like "Quads, Glutes" split into separate rows; `GET /api/exercises` and `GET /routines` now return `muscles:[{name,is_primary}]` arrays per exercise

### Changed
- Exercise seeding now uses `INSERT OR IGNORE` + `UPDATE` so metadata is refreshed on every startup without duplicates
- Routine seeding wrapped in `BEGIN IMMEDIATE` transaction for atomic startup
- `GET /routines` returns global pre-built routines (`user_id IS NULL`) alongside user-created ones
- `GET /exercises/{id}` now joins `exercise_muscles` and returns `muscles:[{name,is_primary}]` instead of legacy `muscle_primary`/`muscle_secondary` string columns
- `GET /api/exercises` response shape: each exercise now includes `muscles:[{name,is_primary}]` array (user-created exercises return `muscles:[]`)
- `GET /routines` response shape: each routine's exercises now include `muscles:[{name,is_primary}]` array

### Changed (continued)
- Webhook payloads now include `user_id` and `username` — both `pr_achieved` and `session_complete` events carry user identity so Home Assistant / n8n automations can distinguish who triggered the event
- `GET /routines` rewritten from N+1 (1 + N per-routine queries) to a single cross-routine JOIN query; Python assembles the response in one pass — eliminates 14+ extra DB round-trips on every workout form load
- `exercises` table: `muscle_primary` and `muscle_secondary` columns dropped via `ALTER TABLE … DROP COLUMN` migration; data is now exclusively in `exercise_muscles`
- Exercise seeding: `UPDATE exercises` no longer writes to the dropped columns; `exercises.py` entries retain those keys to drive `exercise_muscles` seeding

### Fixed
- `.gitignore` `data/` pattern was too broad and blocked `app/data/` module from being tracked — anchored to `/data/`

---

## [0.3.0] — 2026-05-09

### Added
- **Multi-user auth** — invite-gated registration with 48-hour expiring invite links
- **Admin role** — admin user seeded from `ADMIN_USERNAME`/`ADMIN_PASSWORD` env vars at startup
- **Session cookies** — HMAC-signed `itsdangerous` tokens, `httponly`, `SameSite=Strict`; configurable `SESSION_DAYS`
- **Rate limiting** — IP-based login throttle (10 attempts per 15 minutes)
- **Design system** — Space Grotesk (UI text) + JetBrains Mono (numeric data via `.num` class)
- **Gold identity** (`--pr: #f59e0b`) for PRs, sparklines, 1RM, badges; blue accent (`--accent: #4f9cf9`) for interactive chrome
- **Lucide icons** (v0.378.0) — back arrow, delete, rest timer dismiss
- **Routine system** — save, load, and delete workout templates
- **Finish workout** — dedicated endpoint, `session_complete` webhook payload, summary modal
- **Volume tracking** — session volume on workout form, weekly volume on dashboard
- **Rest timer** — SVG ring countdown with configurable duration
- **Workout notes** — PATCH endpoint + localStorage fallback
- **Activity heatmap** — 52-week contribution-style heatmap on dashboard
- **Streak badge** — consecutive-day streak displayed on dashboard
- **PR table** — personal records per exercise on dashboard
- **Exercise detail page** — weight progression sparkline, session history table, estimated 1RM
- **Export** — CSV export scoped to current user
- **Import** — Strong CSV import with 10 MB cap and UTF-8 validation
- **Body metrics** — weight and calorie logging with history table
- **PWA** — manifest, service worker, app icons
- **Content-Security-Policy** header added to all responses

### Security
- **SEC-01** Fix stored XSS — added `escHtml()` helper in workout form JS to escape exercise names and notes before `innerHTML` insertion
- **SEC-02** Fix open redirect — block protocol-relative `//evil.com` paths in login `next` parameter
- **SEC-03** Fix unauthenticated webhook config — `GET /webhooks` now requires admin role
- **SEC-04** Add Content-Security-Policy header — `default-src 'self'` with allowlists for Unpkg CDN, Google Fonts, and inline styles

### Fixed
- Exercise link on PR table rendered as `/exercises/` (no ID) — fixed missing `exercise_id` alias in dashboard query
- Metrics page inputs and table cells missing `.num` JetBrains Mono class
- Workout list card set count and duration stats missing `.num` class
- Second set log failing with "cannot start a transaction within a transaction"

## [0.2.0] — 2026-04-xx

### Added
- Exercises library with global exercise table
- Set logging with weight/reps inputs and stepper controls
- Last session 1RM hint on exercise select
- Dashboard with weekly stats
- Docker Compose deployment with persistent volume
- CI workflow for Docker Hub build and push

## [0.1.0] — 2026-04-xx

### Added
- Initial FitStorm release — workout logging, exercise tracking, and basic dashboard
