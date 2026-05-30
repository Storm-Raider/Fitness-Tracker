#!/usr/bin/env bash
#
# Auto-deploy FitStorm when origin/main advances.
#
# Run by fitstorm-deploy.timer (every ~2 min) as root. Git runs as the repo
# owner (so it uses their SSH key + config); the service restart runs as root.
#
# Safe by design — this box is both dev and deploy host, so it NEVER clobbers:
#   • up to date            → do nothing
#   • working tree dirty    → skip (don't touch local changes)
#   • can't fast-forward    → skip (diverged history; reconcile by hand)
#   • requirements changed  → pip install before restart, else leave old code up
#
# Logs go to the journal:  journalctl -u fitstorm-deploy

set -uo pipefail

REPO="/home/stormraider/Desktop/Git/Fitness-Tracker"
OWNER="stormraider"
SERVICE="fitstorm"
BRANCH="main"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S')  $*"; }

# Run git as the repo owner with their HOME, so SSH keys and git config resolve.
git_as_owner() { runuser -u "$OWNER" -- env HOME="/home/$OWNER" git -C "$REPO" "$@"; }

if ! git_as_owner fetch --quiet origin "$BRANCH"; then
    log "fetch failed (network/remote unavailable) — skipping"
    exit 0
fi

LOCAL="$(git_as_owner rev-parse HEAD)"
REMOTE="$(git_as_owner rev-parse "origin/$BRANCH")"

# Up to date — exit quietly (this runs every couple of minutes).
[ "$LOCAL" = "$REMOTE" ] && exit 0

# Never overwrite uncommitted work on this dev+deploy host.
if ! git_as_owner diff --quiet || ! git_as_owner diff --cached --quiet; then
    log "origin/$BRANCH advanced to ${REMOTE:0:8} but local tree is dirty — skipping"
    exit 0
fi

# Note whether dependencies change in this update (before we move HEAD).
deps_changed=0
git_as_owner diff --quiet "$LOCAL" "$REMOTE" -- requirements.txt || deps_changed=1

if ! git_as_owner merge --ff-only "origin/$BRANCH"; then
    log "cannot fast-forward $BRANCH (diverged from origin) — skipping. Reconcile manually."
    exit 0
fi
log "updated $BRANCH ${LOCAL:0:8} -> ${REMOTE:0:8}"

if [ "$deps_changed" = "1" ]; then
    log "requirements.txt changed — installing dependencies"
    if ! runuser -u "$OWNER" -- env HOME="/home/$OWNER" \
            "$REPO/.venv/bin/pip" install --quiet -r "$REPO/requirements.txt"; then
        log "pip install failed — NOT restarting (app left running on previous code)"
        exit 1
    fi
fi

# DB migrations run automatically on startup (app lifespan), so a restart is
# all that's needed to apply schema changes.
systemctl restart "$SERVICE"
log "restarted $SERVICE — now live on ${REMOTE:0:8}"
