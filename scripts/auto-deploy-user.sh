#!/usr/bin/env bash
#
# Auto-deploy Zenkai — no-root, restart-aware, health-verified.
#
# Runs from crontab (every 2 min) as stormraider, only while the checkout is
# on main (a feature branch checked out here is never deployed). Three jobs:
#
#   1. PULL    — origin/main advanced (commit pushed from another machine)
#                → if it changes server code, test it first in a separate
#                worktree, then fast-forward the working tree to it.
#   2. RESTART — the running server is on an older commit than HEAD AND the
#                change actually touches server code → restart (testing first
#                unless PULL already tested this commit) so the live app
#                matches HEAD, then verify it serves traffic.
#
# Why test in a worktree (2026-11-07): templates and static files are read
# from the working tree on every request, so pulling first and testing after
# served new templates against the old Python for the whole test run (a 500 on
# /plan while the suite ran). Now the live tree only moves once the new commit
# has passed, and the restart follows within seconds. A commit that fails
# never reaches the live tree at all.
#   3. VERIFY  — after a restart, poll /health; if the new code fails to boot,
#                alert loudly instead of leaving the app silently down.
#
# Topology note: this Pi is both the dev box and the deploy box, sharing one
# working copy. A commit made HERE advances HEAD without moving origin relative
# to local, so a fetch-only check never fires. RESTART compares HEAD against the
# commit the running server actually booted on (tracked in logs/deployed-commit)
# so local commits deploy too.
#
# zenkai.service runs as this user with Restart=always / RestartSec=5, so
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

# The ZENKAI_* overrides exist for tests/test_auto_deploy.py, which runs this
# script against a throwaway repo; cron sets none of them.
REPO="${ZENKAI_REPO:-/home/stormraider/Desktop/Git/Fitness-Tracker}"
BRANCH="main"
MARKER="$REPO/logs/deployed-commit"
TESTS_FAILED_MARKER="$REPO/logs/tests-failed-commit"   # commit whose tests failed; not re-run every tick
TESTS_PASSED_MARKER="$REPO/logs/tests-passed-commit"   # commit tested (and passed) before it was pulled
WORKTREE="${ZENKAI_WORKTREE:-${XDG_CACHE_HOME:-$HOME/.cache}/zenkai-deploy-worktree}"
TEST_CMD="${ZENKAI_TEST_CMD:-timeout 900 $REPO/.venv/bin/python -m pytest -q -x -p no:cacheprovider}"
LOCK="${ZENKAI_DEPLOY_LOCK:-/tmp/zenkai-auto-deploy.lock}"
HEALTH_URL="${ZENKAI_HEALTH_URL:-http://127.0.0.1:8000/health}"
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
    if [ -n "${ZENKAI_RESTART_CMD:-}" ]; then
        bash -c "$ZENKAI_RESTART_CMD"
        return 0
    fi
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

# Only Python, the SQL schema, or dependency changes can affect the running
# server; templates hot-reload and static files are served fresh.
SERVER_CODE='(^app/.*\.py$|^requirements\.txt$|^schema\.sql$)'

touches_server() {  # $1..$2 changes server code?
    git diff --name-only "$1" "$2" 2>/dev/null | grep -qE "$SERVER_CODE"
}

install_deps() {  # $1 = directory whose requirements.txt to install
    log "requirements.txt changed — installing dependencies"
    "$REPO/.venv/bin/pip" install --quiet -r "$1/requirements.txt"
}

# Check out $1 in the deploy worktree (outside the repo, so pytest in the repo
# never collects it) and run the suite there; output in logs/deploy-tests.log.
common_dir() {  # the .git directory a checkout belongs to, as a real path (any git version)
    (cd "$1" 2>/dev/null && cd "$(git rev-parse --git-common-dir 2>/dev/null)" 2>/dev/null && pwd -P) || echo "none"
}

prepare_worktree() {
    local commit="$1"
    # A leftover directory that isn't this repo's worktree is replaced, not reused.
    if [ -e "$WORKTREE" ] && [ "$(common_dir "$WORKTREE")" != "$(common_dir "$REPO")" ]; then
        rm -rf "$WORKTREE"
    fi
    if [ -e "$WORKTREE/.git" ] && [ "$(git -C "$WORKTREE" rev-parse HEAD 2>/dev/null)" = "$commit" ]; then
        return 0
    fi
    if [ -e "$WORKTREE/.git" ]; then
        git -C "$WORKTREE" checkout --quiet --detach --force "$commit" \
            && git -C "$WORKTREE" clean --quiet -fdx
    else
        git worktree prune
        mkdir -p "$(dirname "$WORKTREE")"
        git worktree add --quiet --detach --force "$WORKTREE" "$commit"
    fi || { log "could not prepare the test worktree at $WORKTREE"; return 1; }
}

test_in_worktree() {
    prepare_worktree "$1" || return 1
    (cd "$WORKTREE" && bash -c "$TEST_CMD") > "$REPO/logs/deploy-tests.log" 2>&1
}

# --- Only ever deploy the main branch -------------------------------------
# This checkout doubles as the dev workspace. With a feature branch checked
# out, its HEAD is not what was reviewed and merged, so neither pull nor
# restart: the next tick after switching back to main catches up.
CURRENT_BRANCH="$(git symbolic-ref --short -q HEAD || echo detached)"
if [ "$CURRENT_BRANCH" != "$BRANCH" ]; then
    log "checkout is on $CURRENT_BRANCH, not $BRANCH — not deploying"
    exit 0
fi

# --- Trigger 1: PULL commits pushed from elsewhere -------------------------
# A failed/timed-out fetch (offline) is non-fatal — the RESTART check below
# still runs against the local HEAD, so local commits deploy without network.
if timeout "$FETCH_TIMEOUT" git fetch --quiet origin "$BRANCH" 2>/dev/null; then
    LOCAL="$(git rev-parse HEAD)"
    REMOTE="$(git rev-parse "origin/$BRANCH")"
    if [ "$LOCAL" != "$REMOTE" ]; then
        if ! git diff --quiet || ! git diff --cached --quiet; then
            log "origin/$BRANCH advanced to ${REMOTE:0:8} but working tree is dirty — skipping pull"
        elif ! git merge-base --is-ancestor "$LOCAL" "$REMOTE"; then
            log "cannot fast-forward $BRANCH (diverged) — reconcile manually"
        else
            # Server code changed: test the new commit before it touches the live tree.
            if touches_server "$LOCAL" "$REMOTE" \
               && [ "$(cat "$TESTS_PASSED_MARKER" 2>/dev/null)" != "$REMOTE" ]; then
                if [ "$(cat "$TESTS_FAILED_MARKER" 2>/dev/null)" = "$REMOTE" ]; then
                    exit 0                         # already failed; wait for the next commit
                fi
                log "testing ${REMOTE:0:8} in $WORKTREE before pulling"
                # New dependencies go in before the tests, which need them (the
                # running app keeps working: pip only adds or upgrades packages).
                if ! git diff --quiet "$LOCAL" "$REMOTE" -- requirements.txt 2>/dev/null \
                   && ! { prepare_worktree "$REMOTE" && install_deps "$WORKTREE"; }; then
                    log "pip install failed — NOT pulling (app left on ${LOCAL:0:8})"
                    exit 1
                fi
                if ! test_in_worktree "$REMOTE"; then
                    printf '%s\n' "$REMOTE" > "$TESTS_FAILED_MARKER"
                    log "ALERT: tests failed on ${REMOTE:0:8} — NOT pulling (app and working tree left on ${LOCAL:0:8}); see logs/deploy-tests.log"
                    exit 1
                fi
                printf '%s\n' "$REMOTE" > "$TESTS_PASSED_MARKER"
            fi
            if git merge --ff-only "origin/$BRANCH" >/dev/null 2>&1; then
                log "pulled $BRANCH ${LOCAL:0:8} -> ${REMOTE:0:8}"
            else
                log "cannot fast-forward $BRANCH — reconcile manually"
            fi
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
if ! touches_server "$DIFF_BASE" HEAD; then
    write_marker "$HEAD"
    log "no server-code changes ${OLD_SHORT}..${HEAD:0:8} — marker advanced, no restart"
    exit 0
fi

# Already tested before the pull (the usual case): restart straight away, so the
# new templates never wait on the old Python. Otherwise (a commit made on this
# machine) test here first, as before.
if [ "$(cat "$TESTS_PASSED_MARKER" 2>/dev/null)" != "$HEAD" ]; then
    # Install deps first if requirements.txt moved since the running server booted.
    if ! git diff --quiet "$DIFF_BASE" HEAD -- requirements.txt 2>/dev/null; then
        if ! install_deps "$REPO"; then
            log "pip install failed — NOT restarting (app left on previous code)"
            exit 1
        fi
    fi

    # Run the test suite before restarting: a red commit leaves the app on the
    # code it is already running. The failure is remembered per commit so the
    # suite isn't re-run every tick; the next commit (the fix) tries again.
    if [ "$(cat "$TESTS_FAILED_MARKER" 2>/dev/null)" = "$HEAD" ]; then
        exit 0
    fi
    log "testing ${HEAD:0:8} before deploying"
    if ! (cd "$REPO" && bash -c "$TEST_CMD") > "$REPO/logs/deploy-tests.log" 2>&1; then
        printf '%s\n' "$HEAD" > "$TESTS_FAILED_MARKER"
        log "ALERT: tests failed on ${HEAD:0:8} — NOT restarting (app left on ${OLD_SHORT}); see logs/deploy-tests.log"
        exit 1
    fi
fi
rm -f "$TESTS_FAILED_MARKER"

log "deploying ${HEAD:0:8} (was $OLD_SHORT)"
restart_server

# Record the attempt regardless of outcome so we never thrash-restart the same
# commit; systemd owns keeping the process alive from here.
write_marker "$HEAD"

if health_ok; then
    log "deployed ${HEAD:0:8} — health OK"
else
    log "ALERT: ${HEAD:0:8} deployed but /health did not come up in $((HEALTH_RETRIES * HEALTH_INTERVAL))s — inspect: journalctl -u zenkai -n 50"
    exit 1
fi
