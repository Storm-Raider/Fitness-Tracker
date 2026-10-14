# Zenkai

A self-hosted fitness tracker for your Raspberry Pi. Log workouts, track PRs, own your data.

```bash
cp .env.example .env   # set ADMIN_USERNAME, ADMIN_PASSWORD, APP_SECRET
docker compose up -d
```

Open `http://<your-pi-ip>:8000`

---

> **Would you run this on your Pi?** That's the one question I'm trying to answer before going wider.
> If you try it, [open an issue](https://github.com/Storm-Raider/Fitness-Tracker/issues) or reply to the thread — even a one-liner helps.

---

## Screenshot

*(coming soon — screenshot at ~1440px dashboard and ~375px workout form)*

---

## Features

- **Log workouts** — exercise, sets, reps, weight. PR flagged automatically on every set.
- **Exercise library** — 100+ curated exercises with category, equipment, and muscle group metadata.
- **Cascading filter** — pick a routine → filter by muscle group → tap an exercise chip to select it.
- **12 pre-built routines** — PPL, Full Body, Upper/Lower, Bro Split — visible to all users.
- **Personal records** — PR table on the dashboard; gold badge on every set that beats your best.
- **Exercise detail** — weight progression sparkline, session history, estimated 1RM.
- **52-week heatmap** — GitHub-style activity grid. Streak badge next to it.
- **AI Coach** — Google Gemini (via the [AI Studio API](https://aistudio.google.com)) reads your training history and writes a tailored multi-day routine; pick a goal + days/week, review, and save it straight into your routines. Your training summary is sent to Google to generate the plan — see [AI Coach](#ai-coach-gemini).
- **Stats page** — weekly volume sparkline (12 weeks), top exercises by set count, muscle coverage for the current week.
- **Volume tracking** — live kg total per session; 7-day volume on the dashboard.
- **Rest timer** — SVG ring countdown after each logged set. 90 s default, adjustable.
- **Body metrics** — log weight, body measurements, and calories alongside workouts. Height and measurements display in cm or in — stored as cm, converted client-side per your preference.
- **CSV export / import** — one-click download; import history from Strong.
- **Webhooks** — HTTP POST on PR and session complete. Wire to Home Assistant or n8n.
- **Multi-user** — invite-gated registration; admin creates invite links, users self-register.
- **PWA** — installable on Android/iOS home screen; works offline when the Pi is unreachable.

No cloud. No subscription. No telemetry. SQLite file on your Pi.

---

## Requirements

- Raspberry Pi (any model with Docker) or any Linux machine
- [Docker](https://docs.docker.com/engine/install/) + Docker Compose
- [Tailscale](https://tailscale.com/download) on the Pi (recommended for private remote access)

---

## Quick start

**1. Set timezone** (so workout dates are correct):
```bash
sudo timedatectl set-timezone Europe/London   # replace with your timezone
```

**2. Clone and configure:**
```bash
git clone https://github.com/Storm-Raider/Fitness-Tracker
cd Fitness-Tracker
cp .env.example .env
```

Edit `.env`:
```
ADMIN_USERNAME=yourname
ADMIN_PASSWORD=choose-a-strong-password
APP_SECRET=run-python3-c-import-secrets-print-secrets.token_hex-32
```

Generate `APP_SECRET` with:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

**3. Start:**
```bash
docker compose up -d
```

**4. Open** `http://<your-pi-ip>:8000` and log in with the admin credentials you set.

---

## Adding more users

Zenkai uses invite-only registration. As admin:

1. Go to **Account → Invite** (or `/invite`)
2. Generate an invite link (valid for 48 hours)
3. Send it to the person — they click it, set a username and password, done

Each user sees only their own workouts, PRs, and metrics. Global routines and the exercise library are shared.

---

## Configuration

All settings go in `.env` (copied from `.env.example`):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ADMIN_USERNAME` | Yes | — | Username for the admin account (created on first start) |
| `ADMIN_PASSWORD` | Yes | — | Admin password. Recommend ≥16 random chars. |
| `APP_SECRET` | Yes | — | Signing key for session cookies (≥32 chars, never commit) |
| `PUBLIC_URL` | Recommended | request host | Address people open Zenkai at, used for password-reset and invite links. Set it: without it the links come from the request's Host header, which can be forged |
| `SESSION_DAYS` | No | `30` | How long a login session lasts (1–365) |
| `DATABASE_PATH` | No | `/data/fitness.db` | SQLite file path inside container |
| `WEBHOOK_URL` | No | *(empty)* | HTTP endpoint to notify on events |
| `GEMINI_API_KEY` | For AI Coach | — | Google AI Studio API key ([get one](https://aistudio.google.com/apikey)). Without it the Coach is disabled |
| `GEMINI_PLAN_MODEL` | No | `gemini-3.8-flash` | Model that writes plans (with a thinking budget). If it fails or its quota runs out, generation falls back to `GEMINI_MODEL` |
| `GEMINI_MODEL` | No | `gemini-3.5-flash-lite` | Default Gemini model: the plan fallback, and the chat unless `GEMINI_CHAT_MODEL` is set |
| `GEMINI_CHAT_MODEL` | No | `GEMINI_MODEL` | Model for the Coach chat |
| `COACH_AI_MAX_PER_DAY` | No | `300` | App-wide cap on Gemini requests per day (Pacific time, when Google's quota resets). Set it to about 80% of your real limit from <https://ai.dev/rate-limit> |
| `COACH_CHAT_ENABLED` | No | `true` | `false` turns the Coach chat off (undo and note management keep working) |
| `OURA_CLIENT_ID` | For Oura | — | Client ID of your Oura API application (see [Oura Ring](#oura-ring)). Oura is off unless this and the secret are set |
| `OURA_CLIENT_SECRET` | For Oura | — | Its client secret (never commit) |

---

## AI Coach (Gemini)

The **Coach** page turns your logged training into a tailored routine using
Google's [Gemini API](https://aistudio.google.com) (Google AI Studio).

1. Create a key at <https://aistudio.google.com/apikey>.
2. Add it to `.env` (or your Docker environment) and restart:

   ```bash
   GEMINI_API_KEY=your-key-here
   # GEMINI_MODEL=gemini-3.5-flash-lite   # optional; this is the default
   ```

3. Open **Plan → AI Routine**, pick a goal and how many days per week you train,
   and hit *Generate*. Review the plan and save it — each day becomes a routine
   you can load straight into the workout logger.

**How a plan is built.** The programme itself is written by code
(`app/utils/coach_program.py`), not by the model:

- the split for your days per week (full body, upper/lower, push/pull/legs; or the one you ask for)
- each day's slots: main lift, secondary lift, accessories, isolation, core
- sets, reps, effort (RIR) and rest for your goal
- loads from your own recent estimated 1RMs
- a progression rule for every exercise

It also adjusts for your feedback on the last plan, low sleep or energy, pain you
flagged, under-trained muscles and a muscle trained yesterday. Gemini then picks one
exercise per slot from a short list ranked by your own history, and writes the cues,
the title and a summary of why the plan suits you.

Saved routines keep these targets: pick the exercise in the workout logger and its
target (e.g. `5 × 3-5 · 112.5 kg · RIR 2 · 3 min rest`) shows under "Last time".

The progression rule is checked against what you log, in code (no model call,
`app/utils/progression.py`). When your last two finished sessions of a plan exercise
each had the planned sets at the top of the rep range, at the target load or more,
with none rated Max and no pain in a set note, the newest saved plan on the Plan page
lists it under **Ready to progress** (e.g. `Bench Press 60 kg → 62.5 kg`). **Apply**
raises the target in the plan and its routine; **Not yet** hides it for two weeks.
During a session the same step shows as an orange "Ready for 62.5 kg" chip. Steps:
+2.5 kg, +5 kg for a barbell or machine squat or hinge, one dumbbell (+2.5 kg), +1 rep
a set for an unloaded bodyweight move, +10 s for a timed hold.

Generation takes a few seconds to about a minute. Rate-limit and temporary
Gemini errors are retried automatically.

**Free-tier quota:** Gemini's free tier caps requests per model per day (it was
20/day for `gemini-3.8-flash` when this was set up; see your limits at
<https://ai.dev/rate-limit>). A routine normally uses 1 request; when Gemini is
struggling, retries and the fallback are capped at 4. When the plan model's quota
runs out, plans fall back to `GEMINI_MODEL`, which has its own quota. Nothing is
generated in the background.

**Coach chat:** each plan can have a conversation with the coach: ask a
question, or ask for a change ("swap squats for leg press", "day 2 is too long",
"my knee hurts") and the draft updates at once, with Undo (last 3 edits) and a change
summary the server computes itself. It only edits **drafts**; a saved plan answers
questions but is never changed. The coach can propose a short note to remember
("left knee clicks on squats"); it is saved only when you confirm, and notes (up to 20)
shape later chats and plans. On the Plan page the conversation sits behind a **Coach**
tab next to Generate (it opens by itself once a plan is on screen); on a phone a message
bar above the tab bar opens it as a sheet. Saved plans have a **Coach** button that opens
their conversation read-only, with "Regenerate from this chat" to start a new plan from
what you asked for.

Every exercise in a plan has a **⋯** menu. **Swap exercise** (drafts) lists up to six
alternatives right under the row (the same muscle, similar movements first, avoiding
anything that loads an area you flagged as painful); picking one replaces it, keeping the
sets and reps, with an Undo toast. It needs no AI request, so it works even when the daily
cap is reached. **Why this exercise?** (drafts and saved plans) asks the coach. An empty
conversation offers a few starter questions, and when you tell the coach how the plan felt
it offers to record that as the plan's feedback.

Limits worth knowing: every Gemini request counts toward `COACH_AI_MAX_PER_DAY`
(retries and failed requests included; the count is also stored in `coach_usage`, and
admins can see it at `GET /coach/usage`). A chat message is one request (two at most if
Google answers badly), 1–500 characters, with a 30-second budget. At the cap the coach
rests until the next Pacific day; undo, notes and everything outside the Coach keep
working. `scripts/coach_eval.py` is a manual live evaluation of generation and chat
(about 1–2 requests per scenario; never run in CI).

**Privacy:** the prompt — your training history, set notes, RPE trend, journal
wellness entries and injury flags — is sent to Google. Chat messages and your coach
notes are sent too, which is why the chat asks each person to acknowledge that once.
Google's [Gemini API terms](https://ai.google.dev/gemini-api/terms) (checked 2026-10-09)
say that on the **free (unpaid) tier** Google uses what you send to improve its products,
**human reviewers may read it** (disconnected from your account first), and you should
not send sensitive or personal information; on the paid tier prompts are not used to
improve products and are kept only briefly to detect abuse. The same terms say the unpaid
tier may not be offered to users in the **EEA, Switzerland or the UK** (use a paid key
there) and require users to be **18 or older**. If that's not acceptable, enable billing on
the key, set `COACH_CHAT_ENABLED=false` to keep generation but turn the chat off, or leave
`GEMINI_API_KEY` unset and the Coach stays disabled (everything else is unaffected).

---

## Oura Ring

Each user can connect their own Oura ring. Zenkai reads Oura's **daily data** (sleep, readiness, activity) and stores it on this server; it never writes to Oura.

**Setup (once, admin):**

1. Make sure `PUBLIC_URL` is the **https** address Zenkai is opened at (a Tailscale `https://….ts.net` name works).
2. At [cloud.ouraring.com](https://cloud.ouraring.com) → *API Applications*, create an application with the redirect URI `{PUBLIC_URL}/oura/callback`.
3. Put its ID and secret in `.env` as `OURA_CLIENT_ID` and `OURA_CLIENT_SECRET`, and restart Zenkai.

**Connecting:** Settings → *Oura Ring* → **Connect Oura**, approve on Oura's page. The first sync fetches the last 90 days.

**What it does:**
- **Daily log:** empty sleep and steps fields are pre-filled from Oura ("from Oura"); anything you typed wins. Resting heart rate, HRV and readiness show beside them.
- **Home:** today's readiness (with Oura's word: Optimal, Good, Fair, Pay attention), sleep score and HRV.
- **Session:** the readiness shows under the header until the first set.
- **Coach:** plans see your recovery: today's readiness, the 7-day average readiness and sleep score, and your HRV and resting heart rate against your own 30-day baseline (7-day averages need 3 days of data, the baseline 14). When the 7-day readiness is under 70 or HRV is 15% or more below baseline, accessory and isolation work drops a set, the same single step as poor sleep in the Daily log (the two don't stack). These numbers go to Gemini with the rest of the prompt.
- **Analytics:** a *Recovery (Oura)* chart of weekly averages over 12 weeks, switchable between readiness, sleep, HRV and resting heart rate.

**Syncing:** whenever you open a page and the last sync is over 30 minutes old (in the background), every 6 hours, and on *Sync now*. Each sync re-fetches the last 3 days, since Oura revises recent days.

**Privacy:** tokens and data stay in Zenkai's (encrypted) database, except the recovery summary above, which the coach sends to Gemini; tokens are never sent to the browser or logged. Disconnect removes the tokens and, if you tick the box, the synced days. Personal access tokens no longer exist at Oura (retired December 2025), so the OAuth app is required.

## Auto-deploy on merge

A systemd timer polls `origin/main` every ~2 minutes and, when it sees new
commits, fast-forwards the working copy, installs any new dependencies, and
restarts the service. No webhook, no exposed port, no secrets — it uses your
existing SSH key. Merge a PR on GitHub and it's live within a couple of minutes.

It's safe on a machine you also develop on: it only deploys when the working
tree is **clean** and `main` can **fast-forward**. Uncommitted changes or a
diverged history → it skips and logs, never clobbers.

**Install:**
```bash
chmod +x scripts/auto-deploy.sh
sudo cp deploy/zenkai-deploy.service /etc/systemd/system/
sudo cp deploy/zenkai-deploy.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now zenkai-deploy.timer
```

**Watch it:**
```bash
systemctl list-timers zenkai-deploy     # next run
journalctl -u zenkai-deploy -f          # deploy log
```

DB migrations run on startup, so the restart applies schema changes
automatically. To deploy on demand without waiting for the timer:
`sudo systemctl start zenkai-deploy`.

**No-root variant (what the Pi runs):** `scripts/auto-deploy-user.sh` from the
user's crontab every 2 minutes, logging to `logs/auto-deploy.log`. It adds two
gates before any restart:

- **Main only.** If the checkout is on any other branch it neither pulls nor
  restarts (`checkout is on X, not main — not deploying`); switching back to
  `main` catches up on the next tick.
- **Tests first.** A commit that changes server code runs the test suite
  (`pytest -x`, about 75 s on a Pi 5; output in `logs/deploy-tests.log`). If it
  fails, the app stays on the code it is running and the log says `ALERT: tests
  failed`; that commit isn't re-tested every tick, and the next commit tries again.
  Dependencies are installed before the tests run, since the tests need them.

---

## Firewall (optional but recommended)

If you're exposing the Pi on a LAN and using Tailscale:

```bash
sudo ufw allow from 100.64.0.0/10 to any port 8000   # Tailscale range only
sudo ufw deny 8000
sudo ufw enable
```

---

## API

```
GET  /health                        → {"status": "ok"}
GET  /                              → dashboard
GET  /workouts                      → list workouts
POST /workouts                      → create workout  → {id}
GET  /workouts/{id}                 → workout detail + sets
PATCH /workouts/{id}                → update notes   → 204
POST /workouts/{id}/finish          → finish workout  → {duration_minutes, set_count, volume_kg, …}
POST /workouts/{id}/sets            → log set         → {id, is_pr, current_pr}
DELETE /workouts/{id}/sets/{sid}    → delete set      → 204
GET  /exercises                     → exercise browser
GET  /exercises/{id}                → exercise detail (sparkline, 1RM, session history)
GET  /api/exercises                 → exercise list JSON (includes muscles array)
POST /exercises                     → create exercise → {id}
GET  /routines                      → list routines (global + user-created)
POST /routines                      → create routine  → {id}
DELETE /routines/{id}               → delete routine  → 204
GET  /stats                         → analytics (weekly volume, top exercises, muscle coverage)
GET  /metrics                       → body metrics
POST /metrics                       → log body weight/calories
PATCH /api/settings/unit            → set weight unit (kg / lb)       → {unit}
PATCH /api/settings/distance        → set distance unit (km / mi)     → {distance}
PATCH /api/settings/body-measurement → set body measurement unit (cm / in) → {unit}
GET  /export/workouts.csv           → CSV download
POST /import/csv                    → import Strong CSV
GET  /webhooks                      → webhook config (admin only)
GET  /invite                        → invite management (admin only)
POST /invite                        → generate invite link (admin only)
```

---

## Webhooks

Set `WEBHOOK_URL` in `.env`. Zenkai will POST JSON to that URL on two events.

### `pr_achieved` — new personal record on a set

```json
{
  "event": "pr_achieved",
  "user_id": 1,
  "username": "alice",
  "exercise_name": "Bench Press",
  "weight_kg": 100.0,
  "previous_pr_kg": 97.5,
  "workout_id": 42,
  "timestamp": "2026-05-02T14:30:00"
}
```

### `session_complete` — workout finished

```json
{
  "event": "session_complete",
  "user_id": 1,
  "username": "alice",
  "workout_id": 42,
  "duration_minutes": 58.3,
  "set_count": 18,
  "volume_kg": 4250.0,
  "timestamp": "2026-05-02T15:28:00"
}
```

Delivery is a best-effort background task (5 s timeout). If your endpoint is down, the event is dropped — no retry queue.

---

## Home Assistant integration

**1. Create a webhook automation in HA**

In `configuration.yaml` (or via the UI — Settings → Automations → New → Trigger: Webhook):

```yaml
automation:
  - alias: Zenkai PR notification
    trigger:
      platform: webhook
      webhook_id: fittrack
      allowed_methods: [POST]
      local_only: false
    condition:
      condition: template
      value_template: "{{ trigger.json.event == 'pr_achieved' }}"
    action:
      service: notify.mobile_app_your_phone
      data:
        title: "🏆 New PR!"
        message: >
          {{ trigger.json.username }} — {{ trigger.json.exercise_name }}:
          {{ trigger.json.weight_kg }} kg
          (was {{ trigger.json.previous_pr_kg }} kg)

  - alias: Zenkai session complete
    trigger:
      platform: webhook
      webhook_id: fittrack
      allowed_methods: [POST]
      local_only: false
    condition:
      condition: template
      value_template: "{{ trigger.json.event == 'session_complete' }}"
    action:
      service: notify.mobile_app_your_phone
      data:
        title: "✅ Workout done"
        message: >
          {{ trigger.json.username }} — {{ trigger.json.duration_minutes | round }} min ·
          {{ trigger.json.set_count }} sets · {{ trigger.json.volume_kg }} kg
```

**2. Get the webhook URL from HA**

Settings → Automations → your automation → copy the webhook URL:

```
http://homeassistant.local:8123/api/webhook/fittrack
```

**3. Set it in `.env`:**

```
WEBHOOK_URL=http://homeassistant.local:8123/api/webhook/fittrack
```

Then `docker compose restart`.

---

## n8n integration

**1. Create a Webhook node in n8n**

- Add a **Webhook** node. Method: POST. Path: `fittrack`.
- Copy the production URL (e.g. `https://n8n.yourdomain.com/webhook/fittrack`).

**2. Branch on event type**

Add a **Switch** node:

| Output | Condition |
|--------|-----------|
| PR | `{{ $json.event }}` equals `pr_achieved` |
| Session | `{{ $json.event }}` equals `session_complete` |

**3. Wire actions**

PR → ntfy notification:
```json
{
  "topic": "your-topic",
  "title": "New PR — {{ $json.exercise_name }}",
  "message": "{{ $json.username }}: {{ $json.weight_kg }} kg (was {{ $json.previous_pr_kg }} kg)",
  "tags": ["trophy"]
}
```

Session complete → Google Sheets append: map `timestamp`, `duration_minutes`, `set_count`, `volume_kg`, `username`.

**4. Set the URL in `.env`:**

```
WEBHOOK_URL=https://n8n.yourdomain.com/webhook/fittrack
```

Then `docker compose restart`.

---

## Importing from Strong

1. In Strong: Profile → Export Data → email yourself the CSV
2. In Zenkai: go to the import page and upload the file
3. Exercises are created from CSV names. Cardio rows are skipped. Weights in lbs are converted to kg automatically.

---

## Backup & restore

`scripts/backup.py` uses Python's `sqlite3.Connection.backup()` which is WAL-safe and
works while the app is live. Run it manually or wire it to cron.

Each copy is checked with `PRAGMA integrity_check` before it counts as a backup.

**Manual backup:**
```bash
DATABASE_PATH=./fittrack.db python3 scripts/backup.py
# → backups/fittrack-20260521-114000.db  (176 KB; integrity ok; 3 users, 40 workouts, 450 sets)
```

**A second copy on another device.** Backups on the same SD card as the database
die with it. Set `MIRROR_DIR` to a folder on a USB drive and each backup is also
copied there (and checked again), keeping `MIRROR_KEEP` copies (default 30). If the
drive isn't mounted the script exits non-zero instead of quietly writing to the SD
card; the local backup is still made. Keep a copy of `DB_ENCRYPTION_KEY` somewhere
else too (a password manager): an encrypted backup is useless without it.

**Cron — nightly at 3:30, 7 local + 30 on the drive, then a restore drill:**
```bash
crontab -e
# add (one line):
30 3 * * * cd /home/pi/Fitness-Tracker && export DB_ENCRYPTION_KEY="$(grep -m1 '^DB_ENCRYPTION_KEY=' .env | cut -d= -f2-)" && DATABASE_PATH=$PWD/fittrack.db KEEP_DAYS=7 MIRROR_DIR=/media/pi/USB/zenkai-backups .venv/bin/python scripts/backup.py && .venv/bin/python scripts/restore_check.py backups /media/pi/USB/zenkai-backups >> logs/backup.log 2>&1
```

**Restore drill.** `scripts/restore_check.py` opens the newest backup in each folder
with the key, runs the integrity check, prints row counts, and fails if a backup is
damaged, unreadable or older than `MAX_AGE_DAYS` (default 3):
```bash
DB_ENCRYPTION_KEY=... .venv/bin/python scripts/restore_check.py backups /media/pi/USB/zenkai-backups
# OK    backups/fittrack-20261011-033001.db: integrity ok, 0.2 days old; 3 users, 40 workouts, 450 sets
```

**Restore:**
```bash
# Stop the app first, then overwrite the database (and drop its WAL files)
sudo systemctl stop zenkai
rm -f fittrack.db-wal fittrack.db-shm
cp backups/fittrack-20260521-114000.db fittrack.db
sudo systemctl start zenkai
```

**Docker volume backup (alternative — full volume tar):**
```bash
docker run --rm \
  -v fitness-tracker_fitness_data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/zenkai-$(date +%Y%m%d).tar.gz /data

# Restore
docker run --rm \
  -v fitness-tracker_fitness_data:/data \
  -v $(pwd):/backup \
  alpine tar xzf /backup/zenkai-20260502.tar.gz -C /
```

---

## Development

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in test values
DATABASE_PATH=/tmp/zenkai.db uvicorn app.main:app --reload
```

Tests:
```bash
pytest tests/ -v
```

Browser checks (Playwright; `scripts/e2e/`): accessibility, no page wider than a
phone, no script errors on any page, offline logging, and a few interaction
checks. Each runs against a throwaway server with a fresh database, never the
real one:
```bash
scripts/e2e/serve.sh &                  # port 8765 (E2E_PORT), temp database
node scripts/e2e/no_overflow.test.mjs   # or any other check
scripts/e2e/run_all.sh                  # all of them, one fresh server each (port 8766)
```

`run_all.sh` prints one line (`PASS 8/8`, or `FAIL 7/8: a11y`, plus any check
that only passed on a retry) and keeps each check's output in `logs/e2e/<run>/`
(the last 14 runs). Nightly, from the user's crontab:
```bash
15 4 * * * E2E_NOTIFY=github /home/pi/Fitness-Tracker/scripts/e2e/run_all.sh >> /home/pi/Fitness-Tracker/logs/e2e-nightly.log 2>&1
```
With `E2E_NOTIFY=github` a failed run opens a GitHub issue labelled
`nightly-checks` (or comments on the open one) with the failing lines, and the
next clean run closes it, so GitHub's own notifications tell you. It needs `gh`
signed in for the user that runs cron. A check that only passed on its retry
doesn't open one.
It needs Node and Playwright (`PLAYWRIGHT_DIR`), and axe-core for the
accessibility check (`AXE_PATH`).

---

## PWA / mobile install

On Android (Chrome): visit the app → three-dot menu → **Add to Home Screen**.

On iOS (Safari): visit the app → Share → **Add to Home Screen**.

Offline: cached pages show when the Pi is unreachable. Notes typed offline sync when the Pi is back.

---

*Built for the self-hoster who runs their own stack and wants their fitness data to live next to everything else.*
