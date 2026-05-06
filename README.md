# fit

A self-hosted fitness tracker for your Raspberry Pi. Log workouts, track PRs, own your data.

```bash
docker compose up -d
```

Open `http://<your-tailscale-ip>:8000` in your browser.

---

## What it does

- **Log workouts** — exercise, sets, reps, weight. Custom exercise names you define.
- **Track PRs** — personal record detected automatically on every set. Badge appears instantly.
- **View history** — last 7 workouts on dashboard. 52-week activity heatmap.
- **Export your data** — one-click CSV download. Import from Strong/Hevy.
- **Webhook on PR** — pipe PR events to Home Assistant, ntfy, or any HTTP endpoint.

No cloud. No subscription. No telemetry. SQLite file on your Pi.

---

## Requirements

- Raspberry Pi (any model with Docker support) or any Linux machine
- [Docker](https://docs.docker.com/engine/install/) + Docker Compose
- [Tailscale](https://tailscale.com/download) installed on the Pi (for private access)

---

## Setup

**1. Set Pi timezone** (so workout dates are correct):
```bash
sudo timedatectl set-timezone Europe/London  # replace with your timezone
```

**2. Restrict port 8000 to Tailscale only:**
```bash
sudo ufw allow from 100.64.0.0/10 to any port 8000
sudo ufw deny 8000
sudo ufw enable
```

**3. Run:**
```bash
git clone https://github.com/yourname/fitness-tracker
cd fitness-tracker
docker compose up -d
```

**4. Open** `http://<your-tailscale-ip>:8000`

---

## Configuration

`docker-compose.yml` environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_PATH` | `/app/data/fitness.db` | SQLite file path inside container |
| `WEBHOOK_URL` | *(empty)* | Optional: HTTP endpoint to notify on new PRs |

---

## Importing from Strong / Hevy

1. Export your workout history from Strong as CSV (`Profile → Export Data`)
2. `POST /import/csv` with the file, or use the import button in the app
3. Exercises are auto-created from CSV names. Cardio rows are skipped. Weights in lbs are converted to kg.

---

## API

All routes return JSON by default. Pass `Accept: text/html` or `HX-Request: true` for HTML.

```
GET  /health              → {"status": "ok"}
GET  /                    → dashboard
GET  /workouts            → list workouts
POST /workouts            → create workout
POST /workouts/{id}/sets  → log set → {id, is_pr, current_pr}
GET  /exercises           → list exercises (includes last-session context)
POST /exercises           → create exercise
GET  /export/workouts.csv → CSV download
POST /import/csv          → import Strong/Hevy CSV
GET  /webhooks            → webhook config
GET  /metrics             → list body metrics
POST /metrics             → log body weight/calories
```

---

## Webhook payload (on PR)

```json
{
  "event": "pr",
  "exercise_name": "Bench Press",
  "weight_kg": 100.0,
  "previous_pr_kg": 97.5,
  "workout_id": 42,
  "timestamp": "2026-05-02T14:30:00"
}
```

---

## Data

SQLite database at `/app/data/fitness.db` (inside container), persisted to a named Docker volume.

```bash
# Backup
docker run --rm -v fitness-tracker_fitness-data:/data -v $(pwd):/backup \
  alpine tar czf /backup/fitness-backup-$(date +%Y%m%d).tar.gz /data

# Access directly
docker exec -it fitness-tracker-app-1 sqlite3 /app/data/fitness.db
```

---

## Development

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Tests:
```bash
pytest tests/ -v
```

---

*Built for the self-hoster who runs their own stack and wants their fitness data to live next to everything else.*
