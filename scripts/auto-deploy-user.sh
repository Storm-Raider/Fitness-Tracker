#!/usr/bin/env bash
#
# Auto-deploy FitStorm — no-root variant.
#
# Run from crontab (every 2 min) as stormraider. Since the fitstorm.service
# runs as this user with Restart=always, killing the uvicorn process is enough
# — systemd restarts it automatically, picking up new code and running DB
# migrations via init_db() in the app lifespan.
#
# Install:
#   chmod +x scripts/auto-deploy-user.sh
#   crontab -e
#   # Add this line:
#   */2 * * * * /home/stormraider/Desktop/Git/Fitness-Tracker/scripts/auto-deploy-user.sh >> /home/stormraider/Desktop/Git/Fitness-Tracker/logs/auto-deploy.log 2>&1
#
# Safe by design — NEVER clobbers:
#   • up to date            → do nothing
#   • working tree dirty    → skip
#   • can't fast-forward    → skip (reconcile manually)
#   • requirements changed  → pip install before restart

set -uo pipefail

REPO="/home/stormraider/Desktop/Git/Fitness-Tracker"
BRANCH="main"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S')  $*"; }

cd "$REPO"

if ! git fetch --quiet origin "$BRANCH" 2>/dev/null; then
    log "fetch failed (network/remote unavailable) — skipping"
    exit 0
fi

LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse "origin/$BRANCH")"

[ "$LOCAL" = "$REMOTE" ] && exit 0

if ! git diff --quiet || ! git diff --cached --quiet; then
    log "origin/$BRANCH advanced to ${REMOTE:0:8} but local tree is dirty — skipping"
    exit 0
fi

deps_changed=0
git diff --quiet "$LOCAL" "$REMOTE" -- requirements.txt || deps_changed=1

if ! git merge --ff-only "origin/$BRANCH"; then
    log "cannot fast-forward $BRANCH — skipping. Reconcile manually."
    exit 0
fi
log "updated $BRANCH ${LOCAL:0:8} -> ${REMOTE:0:8}"

if [ "$deps_changed" = "1" ]; then
    log "requirements.txt changed — installing dependencies"
    if ! "$REPO/.venv/bin/pip" install --quiet -r "$REPO/requirements.txt"; then
        log "pip install failed — NOT restarting (app still on old code)"
        exit 1
    fi
fi

# Kill uvicorn — systemd Restart=always brings it back on new code within ~5s.
UVICORN_PID=$(pgrep -fo "uvicorn app.main:app")
if [ -n "$UVICORN_PID" ]; then
    kill "$UVICORN_PID"
    log "sent SIGTERM to uvicorn PID $UVICORN_PID — systemd will restart on ${REMOTE:0:8}"
else
    log "uvicorn not running — systemd will start it on ${REMOTE:0:8}"
fi
