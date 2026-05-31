#!/usr/bin/env bash
#
# Auto-deploy FitStorm — no-root, restart-aware, health-verified.
#
# Runs from crontab (every 2 min) as stormraider. Three jobs:
#
#   1. PULL    — origin/main advanced (commit pushed from another machine)
#                → fast-forward the working tree to it.
#   2. RESTART — the running server is on an older commit than HEAD AND the
#                change actually touches server code → restart so the live app
#                matches HEAD, then verify it serves traffic.
#   3. VERIFY  — after a restart, poll /health; if the new code fails to boot,
#                alert loudly instead of leaving the app silently down.
#
# Topology note: this Pi is both the dev box and the deploy box, sharing one
# working copy. A commit made HERE advances HEAD without moving origin relative
# to local, so a fetch-only check never fires. RESTART compares HEAD against the
# commit the running server actually booted on (tracked in logs/deployed-commit)
# so local commits deploy too.
#
# fitstorm.service runs as this user with Restart=always / RestartSec=5, so
# killing uvicorn is enough — systemd respawns it on the working-tree code,
# running DB migrations via init_db() in the app lifespan.
#
# Why no auto-rollback: rolling the git tree back to the previous commit would
# be undone by the next PULL (origin still points at the bad commit), flapping
# every 2 min. Instead we record the attempt so we don't thrash-restart, alert,
# and let systemd keep retrying the process; pushing a fix supersedes it.
#
# Install:
#   chmod +x scripts/auto-deploy-user.sh
#   git rev-parse HEAD > logs/deployed-commit   # seed marker = running commit
#   crontab -e
#   */2 * * * * /home/stormraider/Desktop/Git/Fitness-Tracker/scripts/auto-deploy-user.sh >> /home/stormraider/Desktop/Git/Fitness-Tracker/logs/auto-deploy.log 2>&1

set -uo pipefail

REPO="/home/stormraider/Desktop/Git/Fitness-Tracker"
BRANCH="main"
MARKER="$REPO/logs/deployed-commit"
LOCK="/tmp/fitstorm-auto-deploy.lock"
HEALTH_URL="http://127.0.0.1:8000/health"
FETCH_TIMEOUT=30        # seconds — don't let a hung network pile up cron ticks
HEALTH_RETRIES=12       # poll /health up to 12 times...
HEALTH_INTERVAL=2       # ...every 2s = ~24s for restart delay + boot + migrations

log() { echo "$(date '+%Y-%m-%d %H:%M:%S')  $*"; }

# --- Single-instance lock --------------------------------------------------
# A slow deploy (pip install on a Pi) can outlast the 2-min cron interval.
# Without a lock, overlapping runs race on git and on killing uvicorn.
exec 9>"$LOCK" || { log "cannot open lock $LOCK"; exit 1; }
if ! flock -n 9; then
    log "another auto-deploy run is in progress — skipping this tick"
    exit 0
fi

cd "$REPO" || { log "repo dir missing: $REPO"; exit 1; }
mkdir -p "$(dirname "$MARKER")"

write_marker() {  # atomic: a crash mid-write never leaves a truncated marker
    local tmp="$MARKER.tmp.$$"
    printf '%s\n' "$1" > "$tmp" && mv -f "$tmp" "$MARKER"
}

health_ok() {
    command -v curl >/dev/null 2>&1 || return 0   # no curl → can't check, assume ok
    local i
    for ((i = 1; i <= HEALTH_RETRIES; i++)); do
        if curl -fsS --max-time 3 "$HEALTH_URL" >/dev/null 2>&1; then
            return 0
        fi
        sleep "$HEALTH_INTERVAL"
    done
    return 1
}

restart_server() {
    # Kill uvicorn; systemd respawns it on the working-tree code. Escalate to
    # SIGKILL if the process ignores a graceful SIGTERM.
    local pid
    pid=$(pgrep -fo "uvicorn app.main:app" || true)
    if [ -z "$pid" ]; then
        log "uvicorn not running — systemd will start it"
        return 0
    fi
    kill "$pid" 2>/dev/null || true
    local i
    for ((i = 1; i <= 5; i++)); do
        kill -0 "$pid" 2>/dev/null || return 0   # process gone
        sleep 1
    done
    log "uvicorn PID $pid ignored SIGTERM — escalating to SIGKILL"
    kill -9 "$pid" 2>/dev/null || true
}

# --- Trigger 1: PULL commits pushed from elsewhere -------------------------
# A failed/timed-out fetch (offline) is non-fatal — the RESTART check below
# still runs against the local HEAD, so local commits deploy without network.
if timeout "$FETCH_TIMEOUT" git fetch --quiet origin "$BRANCH" 2>/dev/null; then
    LOCAL="$(git rev-parse HEAD)"
    REMOTE="$(git rev-parse "origin/$BRANCH")"
    if [ "$LOCAL" != "$REMOTE" ]; then
        if ! git diff --quiet || ! git diff --cached --quiet; then
            log "origin/$BRANCH advanced to ${REMOTE:0:8} but working tree is dirty — skipping pull"
        elif git merge --ff-only "origin/$BRANCH" >/dev/null 2>&1; then
            log "pulled $BRANCH ${LOCAL:0:8} -> ${REMOTE:0:8}"
        else
            log "cannot fast-forward $BRANCH (diverged) — reconcile manually"
        fi
    fi
else
    log "fetch failed or timed out (offline?) — reconciling against local HEAD only"
fi

# --- Trigger 2: RESTART if the running server is behind HEAD ----------------
HEAD="$(git rev-parse HEAD)"
DEPLOYED="$(cat "$MARKER" 2>/dev/null || echo "")"
[ "$HEAD" = "$DEPLOYED" ] && exit 0

OLD_SHORT="${DEPLOYED:0:8}"; [ -z "$OLD_SHORT" ] && OLD_SHORT="none"
DIFF_BASE="${DEPLOYED:-HEAD~1}"

# Only Python, the SQL schema, or dependency changes can affect the running
# server. Templates hot-reload (Jinja auto_reload), static files are served
# fresh, and docs/scripts/tests never touch it — for those, advance the marker
# with no restart so a doc or script commit causes zero downtime.
CHANGED="$(git diff --name-only "$DIFF_BASE" HEAD 2>/dev/null || echo "")"
if ! printf '%s\n' "$CHANGED" | grep -qE '(^app/.*\.py$|^requirements\.txt$|^schema\.sql$)'; then
    write_marker "$HEAD"
    log "no server-code changes ${OLD_SHORT}..${HEAD:0:8} — marker advanced, no restart"
    exit 0
fi

# Install deps first if requirements.txt moved since the running server booted.
if ! git diff --quiet "$DIFF_BASE" HEAD -- requirements.txt 2>/dev/null; then
    log "requirements.txt changed — installing dependencies"
    if ! "$REPO/.venv/bin/pip" install --quiet -r "$REPO/requirements.txt"; then
        log "pip install failed — NOT restarting (app left on previous code)"
        exit 1
    fi
fi

log "deploying ${HEAD:0:8} (was $OLD_SHORT)"
restart_server

# Record the attempt regardless of outcome so we never thrash-restart the same
# commit; systemd owns keeping the process alive from here.
write_marker "$HEAD"

if health_ok; then
    log "deployed ${HEAD:0:8} — health OK"
else
    log "ALERT: ${HEAD:0:8} deployed but /health did not come up in $((HEALTH_RETRIES * HEALTH_INTERVAL))s — inspect: journalctl -u fitstorm -n 50"
    exit 1
fi
