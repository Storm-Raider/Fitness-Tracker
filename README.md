# FitTrack

A self-hosted fitness tracker for your Raspberry Pi. Log workouts, track PRs, own your data.

```bash
cp .env.example .env   # fill in APP_PASSWORD and APP_SECRET
docker compose up -d
```

Open `http://<your-pi-ip>:8000` — or your Tailscale address.

---

## Features

- **Log workouts** — exercise, sets, reps, weight. PR detected automatically on every set.
- **Rest timer** — SVG ring countdown starts after each logged set. 90s default, adjustable.
- **Workout notes** — session notes auto-save with localStorage fallback when offline.
- **Volume tracking** — live kg total per session; 7-day volume on dashboard.
- **Routines** — save a session as a named routine, reload exercises in one tap next time.
- **Finish & summarise** — "Finish Workout" shows duration, set count, and total volume.
- **52-week heatmap** — GitHub-style activity grid on the dashboard.
- **Body metrics** — log weight and calories alongside workouts.
- **CSV export / import** — one-click download; import history from Strong.
- **Webhooks** — HTTP POST on PR and session complete. Wire to Home Assistant or n8n.
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
APP_PASSWORD=choose-a-strong-password
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

**4. Open** `http://<your-pi-ip>:8000`

---

## Configuration

All settings go in `.env` (copied from `.env.example`):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `APP_PASSWORD` | Yes | — | Password to log in |
| `APP_SECRET` | Yes | — | Signing key for session cookies (≥32 chars) |
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

All routes return JSON by default. Add `Accept: text/html` for the HTML views.

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
GET  /exercises                     → list exercises (includes last-session context)
POST /exercises                     → create exercise → {id}
GET  /routines                      → list routines
POST /routines                      → create routine  → {id}
DELETE /routines/{id}               → delete routine  → 204
GET  /metrics                       → list body metrics
POST /metrics                       → log body weight/calories
GET  /export/workouts.csv           → CSV download
POST /import/csv                    → import Strong CSV
GET  /webhooks                      → webhook config
```

---

## Webhooks

Set `WEBHOOK_URL` in `.env`. FitTrack will POST JSON to that URL on two events.

### `pr_achieved` — new personal record on a set

```json
{
  "event": "pr_achieved",
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
  "workout_id": 42,
  "duration_minutes": 58.3,
  "set_count": 18,
  "volume_kg": 4250.0,
  "timestamp": "2026-05-02T15:28:00"
}
```

Webhook delivery is a best-effort background task (5s timeout). If your endpoint is down, the event is dropped — no retry queue.

---

## Home Assistant integration

**1. Create a webhook automation in HA**

In `configuration.yaml` (or via the UI — Settings → Automations → New → Trigger: Webhook):

```yaml
automation:
  - alias: FitTrack PR notification
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
          {{ trigger.json.exercise_name }}:
          {{ trigger.json.weight_kg }} kg
          (was {{ trigger.json.previous_pr_kg }} kg)

  - alias: FitTrack session complete
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
          {{ trigger.json.duration_minutes | round }} min ·
          {{ trigger.json.set_count }} sets ·
          {{ trigger.json.volume_kg }} kg
```

**2. Get the webhook URL from HA**

Settings → Automations → your automation → copy the webhook URL. It looks like:

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
- Copy the test or production URL (e.g. `https://n8n.yourdomain.com/webhook/fittrack`).

**2. Branch on event type**

Add a **Switch** node after the webhook:

| Output | Condition |
|--------|-----------|
| PR | `{{ $json.event }}` equals `pr_achieved` |
| Session | `{{ $json.event }}` equals `session_complete` |

**3. Wire actions**

**PR branch** — example: send an ntfy notification:
- Add an **HTTP Request** node
- Method: POST
- URL: `https://ntfy.sh/your-topic`
- Body (JSON):
  ```json
  {
    "topic": "your-topic",
    "title": "New PR — {{ $json.exercise_name }}",
    "message": "{{ $json.weight_kg }} kg (was {{ $json.previous_pr_kg }} kg)",
    "tags": ["trophy"]
  }
  ```

**Session complete branch** — example: append to Google Sheets:
- Add a **Google Sheets** node → Append row
- Map: Date = `{{ $json.timestamp }}`, Duration = `{{ $json.duration_minutes }}`, Sets = `{{ $json.set_count }}`, Volume = `{{ $json.volume_kg }}`

**4. Set the webhook URL in `.env`:**

```
WEBHOOK_URL=https://n8n.yourdomain.com/webhook/fittrack
```

Then `docker compose restart`.

---

## Importing from Strong

1. In Strong: Profile → Export Data → email yourself the CSV
2. In FitTrack: go to the import page and upload the file
3. Exercises are created from CSV names. Cardio rows are skipped. Weights in lbs are converted to kg automatically.

---

## Backup & restore

```bash
# Backup — creates a dated tar.gz in the current directory
docker run --rm \
  -v fitness-tracker_fitness_data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/fittrack-$(date +%Y%m%d).tar.gz /data

# Restore
docker run --rm \
  -v fitness-tracker_fitness_data:/data \
  -v $(pwd):/backup \
  alpine tar xzf /backup/fittrack-20260502.tar.gz -C /
```

---

## Development

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in test values
uvicorn app.main:app --reload
```

Tests (57 passing):
```bash
pytest tests/ -v
```

---

## PWA / mobile install

On Android (Chrome): visit the app → three-dot menu → **Add to Home Screen**.

On iOS (Safari): visit the app → Share → **Add to Home Screen**.

The app works offline for pages you've visited — when the Pi is unreachable you'll see a cached version instead of a blank error. Notes typed offline sync automatically when the Pi is back.

---

*Built for the self-hoster who runs their own stack and wants their fitness data to live next to everything else.*
