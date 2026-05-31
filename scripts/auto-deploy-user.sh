#!/usr/bin/env bash
#
# Auto-deploy FitStorm — no-root, restart-aware variant.
#
# Runs from crontab (every 2 min) as stormraider. Two independent triggers:
#
#   1. PULL    — origin/main advanced (commits pushed from another machine)
#                → fast-forward the working tree to it.
#   2. RESTART — the running server is on an older commit than HEAD
#                → kill uvicorn so the live app matches HEAD.
#
# Why two triggers: this Pi is both the dev box and the deploy box, sharing one
# working copy. When you commit locally HERE, HEAD advances but origin does NOT
# move relative to your local — so a fetch-only check (old behavior) never fires
# and the server keeps serving stale code. The RESTART trigger compares HEAD
# against what is actually running, tracked in the marker file below, so it
# catches local commits too.
#
# "What's running" lives in logs/deployed-commit, rewritten every time this
# script restarts the server. It is seeded to the current HEAD at install time
# (the running server was verified to be on HEAD then), so the first cron tick
# after install is a clean no-op rather than a gratuitous restart.
#
# fitstorm.service runs as this user with Restart=always, so killing uvicorn is
# enough — systemd respawns it on the new code within ~5s, running DB migrations
# via init_db() in the app lifespan.
#
# Install:
#   chmod +x scripts/auto-deploy-user.sh
#   git rev-parse HEAD > logs/deployed-commit   # seed marker = running commit
#   crontab -e
#   # Add this line:
#   */2 * * * * /home/stormraider/Desktop/Git/Fitness-Tracker/scripts/auto-deploy-user.sh >> /home/stormraider/Desktop/Git/Fitness-Tracker/logs/auto-deploy.log 2>&1
#
# Safe by design:
#   • working tree dirty    → never ff-merge over it (PULL skipped)
#   • can't fast-forward    → skip (diverged; reconcile by hand)
#   • requirements changed  → pip install before restart, else leave app up
#   • offline               → PULL skipped, RESTART check still runs on local HEAD

set -uo pipefail

REPO="/home/stormraider/Desktop/Git/Fitness-Tracker"
BRANCH="main"
MARKER="$REPO/logs/deployed-commit"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S')  $*"; }

cd "$REPO" || { log "repo dir missing: $REPO"; exit 1; }
mkdir -p "$(dirname "$MARKER")"

# --- Trigger 1: PULL commits pushed from elsewhere -------------------------
# A failed fetch (offline) is non-fatal — the RESTART check below still runs
# against the local HEAD, so local commits deploy even with no network.
if git fetch --quiet origin "$BRANCH" 2>/dev/null; then
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
    log "fetch failed (offline) — reconciling against local HEAD only"
fi

# --- Trigger 2: RESTART if the running server is behind HEAD ----------------
HEAD="$(git rev-parse HEAD)"
DEPLOYED="$(cat "$MARKER" 2>/dev/null || echo "")"

# Live app already matches HEAD — nothing to do.
[ "$HEAD" = "$DEPLOYED" ] && exit 0

# Install deps if requirements.txt changed since the running server booted.
# Unknown DEPLOYED (first run, marker absent) → diff against HEAD's parent so a
# needed install is never skipped.
DIFF_BASE="${DEPLOYED:-HEAD~1}"
if ! git diff --quiet "$DIFF_BASE" HEAD -- requirements.txt 2>/dev/null; then
    log "requirements.txt changed — installing dependencies"
    if ! "$REPO/.venv/bin/pip" install --quiet -r "$REPO/requirements.txt"; then
        log "pip install failed — NOT restarting (app left on previous code)"
        exit 1
    fi
fi

OLD_SHORT="${DEPLOYED:0:8}"; [ -z "$OLD_SHORT" ] && OLD_SHORT="none"

# Kill uvicorn — systemd Restart=always respawns it on HEAD within ~5s.
UVICORN_PID=$(pgrep -fo "uvicorn app.main:app")
if [ -n "$UVICORN_PID" ]; then
    kill "$UVICORN_PID"
    log "restarting on ${HEAD:0:8} (was $OLD_SHORT) — SIGTERM to PID $UVICORN_PID"
else
    log "uvicorn not running — systemd will start it on ${HEAD:0:8}"
fi

# Record what the freshly-booted server will serve.
echo "$HEAD" > "$MARKER"
