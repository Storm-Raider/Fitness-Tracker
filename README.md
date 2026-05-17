# FitStorm

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
- **Exercise library** — 120 exercises with category, equipment, and muscle group metadata.
- **Cascading filter** — pick a routine → filter by muscle group → tap an exercise chip to select it.
- **12 pre-built routines** — PPL, Full Body, Upper/Lower, Bro Split — visible to all users.
- **Personal records** — PR table on the dashboard; gold badge on every set that beats your best.
- **Exercise detail** — weight progression sparkline, session history, estimated 1RM.
- **52-week heatmap** — GitHub-style activity grid. Streak badge next to it.
- **Stats page** — weekly volume sparkline (12 weeks), top exercises by set count, muscle coverage for the current week.
- **Volume tracking** — live kg total per session; 7-day volume on the dashboard.
- **Rest timer** — SVG ring countdown after each logged set. 90 s default, adjustable.
- **Body metrics** — log weight and calories alongside workouts.
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

FitStorm uses invite-only registration. As admin:

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
| `SESSION_DAYS` | No | `30` | How long a login session lasts (1–365) |
| `DATABASE_PATH` | No | `/data/fitness.db` | SQLite file path inside container |
| `WEBHOOK_URL` | No | *(empty)* | HTTP endpoint to notify on events |

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
GET  /export/workouts.csv           → CSV download
POST /import/csv                    → import Strong CSV
GET  /webhooks                      → webhook config (admin only)
GET  /invite                        → invite management (admin only)
POST /invite                        → generate invite link (admin only)
```

---

## Webhooks

Set `WEBHOOK_URL` in `.env`. FitStorm will POST JSON to that URL on two events.

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
  - alias: FitStorm PR notification
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

  - alias: FitStorm session complete
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
2. In FitStorm: go to the import page and upload the file
3. Exercises are created from CSV names. Cardio rows are skipped. Weights in lbs are converted to kg automatically.

---

## Backup & restore

```bash
# Backup — creates a dated tar.gz in the current directory
docker run --rm \
  -v fitness-tracker_fitness_data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/fitstorm-$(date +%Y%m%d).tar.gz /data

# Restore
docker run --rm \
  -v fitness-tracker_fitness_data:/data \
  -v $(pwd):/backup \
  alpine tar xzf /backup/fitstorm-20260502.tar.gz -C /
```

---

## Development

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in test values
DATABASE_PATH=/tmp/fitstorm.db uvicorn app.main:app --reload
```

Tests:
```bash
pytest tests/ -v
```

---

## PWA / mobile install

On Android (Chrome): visit the app → three-dot menu → **Add to Home Screen**.

On iOS (Safari): visit the app → Share → **Add to Home Screen**.

Offline: cached pages show when the Pi is unreachable. Notes typed offline sync when the Pi is back.

---

*Built for the self-hoster who runs their own stack and wants their fitness data to live next to everything else.*
