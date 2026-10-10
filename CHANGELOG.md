# Changelog

All notable changes to FitStorm are documented here.

## [Unreleased]

### Added
- **Per-exercise menu, swap picker, starter questions and feedback chip.** Every exercise in a plan has a **⋯** menu: **Swap exercise** (drafts) unfolds up to six ranked alternatives under the row and replaces the exercise in one tap, keeping sets and reps, with an Undo toast and no AI request; **Why this exercise?** (drafts and saved plans) asks the coach. An empty conversation offers starter questions, and when you tell the coach how the plan felt it offers to save that as the plan's feedback (showing the value it would replace).
- **Coach chat on the Plan page.** Once a plan is on screen the left column switches to a **Coach** tab (next to Generate) with the conversation: your messages and the coach's replies, a change summary under every edit, **Undo** for the last three edits, your coach notes (open the list to forget one), and a "Remember: ...? Yes / No" prompt when the coach suggests a note. On a phone a message bar sits above the tab bar and opens the chat as a bottom sheet; after an edit made with the sheet closed, an "Edited Day 2 · Undo" toast appears. Saved plans get a **Coach** button: their conversation opens read-only with "Regenerate from this chat", which pre-fills the generator with what you asked for. A first-use card explains what is sent to Google (including that on the free tier human reviewers may read it) and must be acknowledged once per person. Edits made in another tab show "This plan changed in another tab · Reload" without losing what you typed; at the daily cap the composer says the coach is resting. While a plan is generating the Coach tab is unavailable. Everything the coach writes is shown as text, never as markup.
- **Swap an exercise** (backend; the picker UI follows). `GET /coach/plans/{id}/swap?day=&idx=` offers up to six ranked alternatives for one exercise of a draft: same primary muscle, not already on that day, similar movements first (a squat offers other squats before a leg curl), exercises not used on another day before ones that are, your preferred equipment, then conventional staples. Anything that loads an area you flagged, said in the chat, or keep as a note (knee, shoulder, lower back, elbow, hip) is left out. `POST /coach/plans/{id}/swap {base_rev, day, idx, exercise_id}` replaces it in place keeping sets and reps (the old note describes the old lift, so it is cleared), pushes an undo entry, bumps the revision and writes no chat message. Drafts only, compare-and-set like every other edit, and no AI request is spent, so it works at the daily cap and with the chat switched off.
- **Coach chat backend** (the Plan-page panel follows). `GET/POST /coach/plans/{id}/chat`, `POST /coach/plans/{id}/undo`, `GET/POST/DELETE /coach/notes`, `POST /coach/chat/ack`, admin `GET /coach/usage`. A turn is one Gemini request (two at most) built from your fresh training profile, your notes and the conversation; the model returns only the days it changed and the **server** applies them (fixed day count, names checked against your library) and reports the diff, never the model's own account. Edits apply to drafts only, are undoable (last 3), and commit in one compare-and-set transaction, so a second tab or a save in progress can never be overwritten; a failed turn saves nothing. The coach can propose a short note to remember; it is saved only when you confirm (up to 20). Saved plans answer questions but are never edited. New tables `coach_messages`, `coach_notes`, `coach_usage` and columns `coach_plans.rev/updated_at/undo_json`, `user_settings.coach_chat_ack_at` (with an automated migration test).
- **Daily request cap.** `COACH_AI_MAX_PER_DAY` (default 300 until you set it to ~80% of your real limit, see <https://ai.dev/rate-limit>) caps all Gemini requests per Pacific day, counted before every request including retries and failures, for plan generation as well as chat. At the cap the coach rests until tomorrow; `COACH_CHAT_ENABLED=false` is a kill switch; `GEMINI_CHAT_MODEL` optionally picks a different model for chat.
- `GeminiError.kind`, `gemini.chat_turn_json` (multi-turn, deadline, per-request hook) and 12 chat scenarios in `scripts/coach_eval.py`.

### Changed
- **Saving a plan now uses the name you typed.** The plan-name box used to be cosmetic (the stored title was saved); `confirm` now takes `{title, base_rev}`, refuses a plan that changed since you last saw it, and refuses a plan whose exercise was deleted (it used to fail with a server error).
- **Exercise names are restricted** to letters, numbers, spaces and `- ' ( ) / + & . ,` (up to 60 characters) when you create one, because names are placed in AI prompts. Existing and imported names are kept but any that don't fit are left out of prompts. A new exercise or a CSV import is now visible to the coach immediately (it used to need a restart).
- Your saved notes are also sent with plan generation. Permanent Gemini errors (bad key, daily quota used up) are no longer retried. Drafts now expire 7 days after their last activity, not after creation.
- **Coach generation prompt rewritten for Gemini.** The rules now live once in the system prompt (the user message carries only this athlete's data and a one-line task), the JSON worked example is gone (a real generation copied its numbers and phrasing; the schema already fixes the shape), and athlete-written text (the focus request, pain notes, journal and workout comments) is explicitly marked as data that cannot change the rules, day count or output format. The system prompt is 29% shorter (2,870 to 2,027 chars) and a whole request about a third smaller (about 7.8k to 5.1k chars for an athlete with no history). The example-copy detector, unreachable without the example, is removed. Plan generation now runs through one shared `_generate_plan`, and the athlete-context renderer moved to `app/utils/coach_plan.py` so the coming chat describes the athlete identically.
- **Coach now honours flagged pain and your request more reliably.** Live testing on `gemini-3.5-flash-lite` showed it ignored constraints stated only mid-message: with a flagged left knee it still programmed Leg Press and Leg Extension in 3 of 3 runs, and it ignored "avoid deadlifts". The task line at the end of the prompt now restates flagged pain (with the movements the recognised area rules out: knee, shoulder, lower back, elbow, hip) and your request. Knee case: 0/3 -> 3/3 clean; "avoid deadlifts": fail -> 2/2.
- **`scripts/coach_eval.py`** — a manual live evaluation of plan generation (12 synthetic scenarios incl. flagged knee pain and prompt-injection attempts via the request and via journal/workout comments) with a merge gate (>=80% pass, no safety failures). Never run in CI; `--dry-run` builds the prompts offline. Recorded baseline on `gemini-3.5-flash-lite`: 11/12, all safety scenarios clean; known weak spot: a muscle trained within the last day still gets programmed on Day 1.
- **Plan page UI groundwork for the coming coach chat.** The AI plan is now drawn by a shared `plan_view.js` (text-only DOM, redraws just the days that changed) and one `PlanState` store tracks which plan is on screen; the Save row is static markup, so a typed plan name is no longer reset by a redraw. Save and Regenerate are disabled while a generation or save is running (previously Regenerate stayed clickable mid-generation). New shared pieces in `base.html`/`static/`: `showActionToast`, a `.pill` (the Exercises muscle filter now extends it, no visual change), a 44px `.seg-toggle`, and `sheet.js` (a modal bottom sheet). Static JS is served with a content-hash URL (`static_url`) so the cache-first service worker can never serve a stale copy. A test bans HTML-string APIs in the files that render model text.
- **AI Coach now uses the Google AI Studio (Gemini) API instead of local Ollama.** Set `GEMINI_API_KEY` (and optionally `GEMINI_MODEL`, default `gemini-3.5-flash-lite`); the Coach is disabled without a key. New `app/utils/gemini.py` client (JSON-schema structured output, streamed progress, automatic retry of 429/5xx/timeouts) replaces `app/utils/ollama.py`; the `OLLAMA_*` settings, Ollama fallback host and model warm-up are gone. **Privacy:** the coach prompt (training history, set notes, RPE trend, journal wellness, injury flags) is now sent to Google — it is no longer on-device. On the free AI Studio tier Google may use submitted content to improve its products; the paid tier does not. Existing `OLLAMA_*` lines in `.env` are ignored and can be deleted. The background pre-generation of the next plan (which silently doubled API usage per routine) is removed — `POST /coach/generate` now always returns `202` with a job id.

### Added
- **Body measurement unit toggle (cm ↔ in)** — a `cm | in` button in the Body Metrics page header lets you log and view body measurements (Chest, Waist, Hips, etc.) and BMI height in your preferred unit. Preference is stored server-side (`user_settings.pref_body_measurement`) and syncs across devices/browsers. All values remain stored in cm; conversion is display-only. The kg/lbs weight toggle is unaffected. Closes #15 and #16.

### Added
- **Challenges** — fixed-length daily-adherence programs (75 Hard, 75 Medium) at `/challenges`. Tick daily rules; the workout rule auto-ticks when you log a workout/cardio that day; the run resets to Day 1 if a locked day is left incomplete (strict, with a 1-day grace so a forgotten tap doesn't nuke a streak). Reset/completion is computed lazily on load — no background job. **Progress photos stay on your device** (browser IndexedDB) and are never uploaded; the server only records done/not-done. Multiple challenges can run at once; a dashboard card surfaces active ones, and completing a program unlocks an achievement. New `challenge_attempts` + `challenge_checkins` tables and `app/data/challenges.py` rule definitions.
- **Auto-deploy on merge** — `deploy/fitstorm-deploy.{service,timer}` + `scripts/auto-deploy.sh`: a systemd timer polls `origin/main` every ~2 min and, on new commits, fast-forwards, installs changed dependencies, and restarts the service. Safe on a dev+deploy host (only deploys when the tree is clean and can fast-forward). See README "Auto-deploy on merge".
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

### Added
- **In-app feedback** — `/feedback` (More menu) files a bug report or feature request straight to GitHub Issues. Pick Bug or Feature, add a title and description; the issue is created with the matching label (`bug` / `enhancement`) and a context footer. Needs `GITHUB_TOKEN` (+ optional `GITHUB_REPO`); shows a graceful notice when unconfigured. 30s per-user cooldown.
- **Better AI Coach routines** — the coach now follows conventional S&C programming: per-goal set/rep/intensity prescriptions, recommended splits by days/week (Full Body, Upper/Lower, PPL), compound-first ordering, mandatory progression cues, and a catalog that always surfaces staple compounds (Squat, Deadlift, Bench, Row, OHP).
- **km / mi distance unit** — cardio distances and pace now convert between km and mi. Set in Settings → Units alongside the existing kg/lbs toggle. Preference persisted per-user.
- **Daily Log** (`/journal`) — structured daily check-in: day number, weight, workout, 3 meals, water, energy, motivation, sleep, steps, notes. Auto-fills challenge day number from active run. Dashboard nudge until today's entry is saved. History of last 60 days with one-tap copy.
- **Planner: Load saved plans** — each saved mesocycle plan now has a Load button that restores goal, weeks, and lifts into the builder and regenerates the week-by-week table.
- **Workout search** — filter the workouts list by exercise name, date, or notes via `?q=`. Searches full set history (not just the 3-exercise preview). 300ms debounce, clear link, distinct empty state.
- **Pi-grade Industrial design system** — spacing tokens (`--sp-2xs` → `--sp-3xl`), `<main>` landmark, nav `aria-label`, `text-wrap: balance` on headings, `font-variant-numeric: tabular-nums` on numeric values.

### Fixed
- **Weight unit gaps** — journal history, metrics table, and PRs bodyweight display now convert correctly when toggling kg/lbs.
- **Double-tap zoom** — `touch-action: manipulation` applied directly to all interactive elements (buttons, links, inputs). The body-level setting wasn't inherited. iOS inputs on Daily Log also forced to 16px to prevent focus-triggered zoom.
- **Daily Log icon size** — mob-more-item was missing the `.mob-more-icon` wrapper, rendering at 18px instead of 22px like all other items.
- **Exception handling** — `stats.py` date parsing now catches `ValueError` instead of bare `Exception`; `achievements.py` DB insert failures now log a warning instead of silently passing.
- **Session revocation** — logout now immediately invalidates the server-side session. Previously, a copied cookie stayed valid for up to 30 days after logout. Existing sessions will require a one-time re-login after this deploy.
- **Case-insensitive login** — usernames are now matched case-insensitively at login. `Admin`, `ADMIN`, and `admin` all resolve to the same account.

### Added
- **Edit rules on active 75 Medium challenge** — the challenge detail page now shows a Rules card with an Edit button. Rename rules, toggle optional, add or remove entries mid-run. Checkins immediately use the updated rule set.
- **Editable 75 Medium rules** — the 75 Medium challenge card now shows an inline rule editor before starting. Edit any rule label, mark rules optional, remove non-workout rules, and add new ones. Custom rules are stored per-attempt and carried through restarts. 75 Hard stays fixed.

### Fixed
- `.gitignore` `data/` pattern was too broad and blocked `app/data/` module from being tracked — anchored to `/data/`
- **Accessibility — keyboard focus ring**: `outline: none` on inputs suppressed all keyboard focus indicators. Added `*:focus-visible` global ring (2px accent blue); form inputs keep their existing border+glow via `:focus`.
- **Accessibility — reduced motion**: Added `@media (prefers-reduced-motion: reduce)` block — all transitions and animations collapse to 0.01ms for users who opt out of motion at the OS level.
- **Dark mode declaration**: Added `color-scheme: dark` to `:root` so the browser renders native controls (scrollbars, caret, date pickers) in dark mode rather than defaulting to light.
- **Touch targets — unit toggle**: `kg`/`lbs` button was 23px tall (spec ≥40px for steppers). Raised to `min-height: 40px` on both desktop and mobile instances.
- **Touch targets — dashboard**: Weekly goal "Set"/"Save" buttons (28–30px → 36px), "All records →" link (15px → 36px), PR table exercise links (17px → 36px).
- **Touch targets — nav user chip**: Username link (16px → 36px inline-flex), Sign out button (30px → 36px).
- **Touch targets — `.btn` base class**: Global minimum raised from 38px to 44px; affects Start Session and all secondary action buttons.

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
