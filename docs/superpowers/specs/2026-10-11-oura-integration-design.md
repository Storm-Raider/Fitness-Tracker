# Oura Ring integration: design

Date: 2026-10-11. Status: approved in conversation; written for review.

## Goal

Bring the user's Oura Ring 5 data into Zenkai so that:

1. the Daily log's sleep and steps fill themselves,
2. today's readiness shows on Home and at the start of a session,
3. the AI coach plans with recovery in mind, and
4. Analytics charts recovery trends next to training.

## Constraints (researched 2026-10-11)

- **Sign-in:** Oura API v2 accepts only OAuth2 for new integrations. Personal access tokens were deprecated in December 2025.
  - Authorize: `https://cloud.ouraring.com/oauth/authorize`.
  - Token: `https://api.ouraring.com/oauth/token`.
  - Revoke: `https://api.ouraring.com/oauth/revoke`.
- **Redirect URI:** must be HTTPS. The user reaches Zenkai at a Tailscale `https://….ts.net` address, so the redirect is `{PUBLIC_URL}/oura/callback`. Oura's servers never need to reach the Pi; the browser follows the redirect.
- **Refresh tokens are single-use and rotate.** Every refresh must persist the new refresh token before anything else can use the old one.
- **Endpoints used:** all under `https://api.ouraring.com/v2/usercollection/`, scope `daily`.
  - `daily_sleep` (score)
  - `daily_readiness` (score, temperature deviation)
  - `daily_activity` (steps, active calories)
  - `sleep` (sleep periods: total sleep duration, average HRV, lowest heart rate)
- **No webhooks:** they would need the Pi reachable from the internet (Tailscale Funnel). Rejected for a personal health log.

## Decisions (user)

- **Approach:** pull. Zenkai fetches from Oura on app open (at most every 30 minutes) and every 6 hours in-process. No webhooks, no file import.
- **Precedence:** typed values win. Oura only pre-fills empty Daily log fields and never overwrites a value the user entered.
- **Per user:** each Zenkai user connects their own ring, and no one sees another user's data.
- **Coach:** Oura recovery numbers may be sent to Google Gemini as part of the training summary.
- **Build order:** two PRs (see the end of this document).

## Setup (one-time, admin)

1. Register an API application at cloud.ouraring.com with redirect URI `{PUBLIC_URL}/oura/callback`.
2. Set `OURA_CLIENT_ID` and `OURA_CLIENT_SECRET` in the Pi's `.env`, then restart Zenkai. The restart needs sudo, so the user runs it.
3. Without both settings, the integration is off.
   - Settings shows "Oura isn't set up on this server".
   - Only an admin sees the setup steps (same pattern as the coach's `GEMINI_API_KEY` note).

README gains an "Oura Ring" section with these steps and a note on what is stored where.

## Components

### `app/utils/oura.py` (new): the Oura client

The only module that talks to Oura. Pure functions plus thin async HTTP, using the app's shared `httpx.AsyncClient`.

- `is_configured() -> bool`: both env vars are set.
- `authorize_url(state) -> str`: scope `daily`, redirect from `PUBLIC_URL`.
- `exchange_code(code) -> Tokens`: code to token.
- `refresh(refresh_token) -> Tokens`: refresh.
- `revoke(access_token)`.
- `fetch_days(access_token, start, end) -> list[OuraDay]`:
  - calls the four endpoints and merges them per `day`;
  - from `sleep`, takes the long sleep period (type `long_sleep`) for that day: duration in hours, `average_hrv`, `lowest_heart_rate`.
- Errors map to three exceptions:
  - `OuraAuthError` (401/403): the user must reconnect.
  - `OuraUnavailable` (5xx, timeouts, 429): try later.
  - `OuraError`: anything else.

### Storage (migrations in `app/db.py`)

- **`oura_connections`:**
  - `user_id` (PK, FK users, cascade)
  - `access_token`, `refresh_token`, `expires_at`, `scopes`
  - `connected_at`, `last_sync_at`, `last_error`, `needs_reconnect` (0/1)

  Tokens live only here, in the SQLCipher-encrypted database. They are never in the browser and never logged.
- **`oura_daily`:**
  - `user_id`, `day` (TEXT `YYYY-MM-DD`), PK(`user_id`, `day`)
  - `sleep_score`, `readiness_score`
  - `sleep_hours`, `hrv_ms`, `resting_hr`
  - `steps`, `active_calories`, `temp_deviation`
  - `updated_at`

  All values are nullable, since Oura can omit any of them.

### `app/utils/oura_sync.py` (new): syncing

- `sync_user(conn, uid)`:
  - Under the DB write lock: if the access token is expiring within 5 minutes, refresh it and store the new pair (rotation-safe).
  - Fetch `[max(last_sync_day - 2, today - 90), today]`. The first sync backfills 90 days.
  - Upsert `oura_daily`.
  - Set `last_sync_at`, and clear `last_error`.
  - On `OuraAuthError`: set `needs_reconnect=1`.
  - On `OuraUnavailable`: record `last_error` and retry next time.
- **Triggers:**
  1. Right after connecting (backfill).
  2. Opportunistically on a page load: the `/achievements/unseen` poll that every page already makes schedules a background task when `last_sync_at` is older than 30 minutes. It never blocks the page, and a per-user in-flight guard prevents overlapping syncs.
  3. A 6-hour loop started in the app lifespan, for all connected users.
- **Disconnect:** revoke (best effort), delete the connection row, and keep or delete `oura_daily` as the user chooses.

### Routes: `app/routes/oura.py` (new)

- `GET /oura/connect`:
  - Logged-in users only.
  - Creates a signed, single-use `state` tied to the user and session (CSRF), valid for 10 minutes.
  - Redirects to Oura.
- `GET /oura/callback`:
  - Verifies `state`, exchanges the code, stores the tokens, kicks off the backfill.
  - Redirects to `/settings?oura=connected`.
  - On the user's denial or an error, redirects with a readable message.
- `POST /oura/sync`: "Sync now" from Settings.
- `POST /oura/disconnect`: body `{keep_data: bool}`.
- `GET /api/oura/today`: today's (or the latest) day, for the Home card and session line.

## Where it shows up

### PR 1

- **Settings:** an **Oura Ring** card.
  - Not configured: a plain note; the admin sees the setup steps.
  - Configured but not connected: a Connect button and one line on what is read ("sleep, readiness and activity").
  - Connected: last sync time, Sync now, Disconnect.
  - Needs reconnect: an explanation and a Reconnect button.
- **Daily log:**
  - Empty `sleep_hrs` and `steps` fields are pre-filled from `oura_daily` for the shown day, with a small "from Oura" tag. Typed values are untouched.
  - A read-only line: "Oura: resting HR 52 bpm · HRV 48 ms · readiness 82".
  - No `daily_logs` row is created automatically.
- **Home:** a compact card under the Start/Resume button: "Readiness 82 · Sleep 76 · HRV 48 ms".
  - Oura's band word sits beside readiness: ≥85 Optimal, 70–84 Good, 60–69 Fair, <60 Pay attention.
  - If today has no data yet: "Waiting for today's Oura sync".
  - Ink numbers, no new colours (DESIGN.md).
  - Shown only when connected.
- **Session screen:** before the first set, one line under the header: "Readiness 82 (Good)". Hidden once a set is logged and when not connected.

### PR 2

- **Coach** (`app/utils/training_profile.py`, `coach_plan.athlete_context`, `coach_program._volume_delta`):
  - The profile gains `recovery`:
    - `today_readiness`
    - `avg_readiness_7d`, `avg_sleep_score_7d`
    - `hrv_vs_baseline_pct` (7-day vs 30-day mean)
    - `resting_hr_vs_baseline_bpm`
  - These are prompt lines, e.g. "Recovery: readiness 7-day avg 68 (Fair), HRV 14% below the 30-day baseline". They are sent to Gemini.
  - Rule: if `avg_readiness_7d < 70` or `hrv_vs_baseline_pct <= -15`, `_volume_delta` gives −1. It stays clamped to [−1, 1] with the existing wellness and feedback signals.
- **Analytics:** a **Recovery** section, shown only when there are Oura days in the last 12 weeks.
  - One canvas line chart with the shared `.seg-toggle`: Readiness / Sleep / HRV / Resting HR.
  - Weekly averages over 12 weeks, keyed by week Monday like the volume chart.
  - Ink series, number-face axes, "Sep 13" dates, and the newest point marked.

## Privacy and security

- **Stays on the Pi:** Oura data lives in the encrypted database. The only third party that receives any of it is Google Gemini (PR 2, the coach summary), which the user accepted.
- **Tokens:** never sent to the browser, never logged. The `state` is signed with the app secret.
- **Per user:** every query filters by `user_id`. Tests prove user B can't read or disconnect user A's connection or days.
- **Minimal scope:** `daily` only.

## Error handling

| Situation | Behaviour |
|---|---|
| Oura down / timeout / 429 | `last_error` set; Settings shows "Last sync failed: Oura didn't respond. Will retry."; data so far stays |
| Token revoked or expired beyond refresh | `needs_reconnect`; Settings and Home show "Reconnect Oura" |
| User denies on Oura's page | back to Settings with "Oura wasn't connected" |
| Bad or expired `state` | 400 page "That link expired, try Connect again" |
| Env vars unset | integration hidden except the Settings note |

## Testing

- **Faked HTTP:** every Oura call goes through `httpx.MockTransport`. No network in tests.
- **Unit:** merging the four endpoints per day, the long-sleep pick, unit conversions, band words, and baseline maths (PR 2).
- **Sync:**
  - backfill window
  - three-day re-fetch
  - refresh rotation persisted before reuse
  - 401 → `needs_reconnect`; 5xx → `last_error` with data kept
  - no double sync while one is in flight
- **Routes:**
  - connect/callback with a valid, forged, expired or reused `state`
  - denial
  - disconnect keeping or deleting data
  - user isolation
- **Pages:**
  - Settings card in each of its four states
  - Daily log pre-fill without overwriting typed values
  - Home card and session line present/absent
  - the existing a11y and no-overflow browser suites stay green
- **Live check:** after PR 1 merges, the user does the setup and connects the ring. Zenkai's Settings must show the backfill, and Home must show today.

## Out of scope

- Webhooks.
- Heart-rate time series, workouts, tags, SpO2.
- Writing anything back to Oura.
- Other wearables.
