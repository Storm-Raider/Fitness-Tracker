# Changelog

All notable changes to FitTrack are documented here.

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
- Initial FitTrack release — workout logging, exercise tracking, and basic dashboard
