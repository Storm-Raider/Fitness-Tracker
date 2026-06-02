#!/usr/bin/env bash
#
# Daily issue triage — classify reported GitHub issues, auto-fix the minor
# ones, queue the major ones for human review.
#
# Runs from crontab once a day (3 AM) as stormraider. For each open issue
# labelled `bug` or `enhancement` that hasn't been triaged yet:
#
#   1. CLASSIFY   — Claude (file-edit tools only, no shell) reads the issue and
#                   the codebase and decides minor vs major against the criteria
#                   embedded below. When in doubt it must choose major.
#   2. MINOR      — Claude edits the fix in place (it cannot commit/push/test).
#                   THIS SCRIPT then runs the full test suite. Only if it passes
#                   does the script commit + push to main (→ auto-deploy). If
#                   tests fail, the edit is discarded and the issue is escalated.
#   3. MAJOR      — no code is touched; the issue is appended to TODOS.md and
#                   labelled for the human to review.
#
# Safety model: the AI never runs git, tests, or the deploy. The shell owns
# every irreversible action and gates the push on a green test suite. Worst
# case of a misclassification is a discarded edit or an extra TODOS entry —
# broken code cannot reach the live app because the push is test-gated.
#
# Install:
#   chmod +x scripts/triage-issues.sh
#   crontab -e
#   0 3 * * * /home/stormraider/Desktop/Git/Fitness-Tracker/scripts/triage-issues.sh >> /home/stormraider/Desktop/Git/Fitness-Tracker/logs/triage.log 2>&1

set -uo pipefail

# --- cron has a bare environment: make the tools we need reachable ----------
export PATH="/home/stormraider/.nvm/versions/node/v22.22.3/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export HOME="/home/stormraider"

REPO="/home/stormraider/Desktop/Git/Fitness-Tracker"
BRANCH="main"
LOCK="/tmp/fitstorm-triage.lock"
PYTEST="$REPO/.venv/bin/pytest"
MAX_ISSUES=10                     # cap per run so a backlog can't run away
CLAUDE_TIMEOUT=600                # 10 min per issue
TODOS="$REPO/TODOS.md"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S')  $*"; }

# --- Single-instance lock ---------------------------------------------------
exec 9>"$LOCK" || { log "cannot open lock $LOCK"; exit 1; }
if ! flock -n 9; then
    log "another triage run is in progress — skipping"
    exit 0
fi

cd "$REPO" || { log "repo dir missing: $REPO"; exit 1; }

# --- Preconditions ----------------------------------------------------------
command -v gh >/dev/null 2>&1     || { log "gh CLI not found — aborting"; exit 1; }
command -v claude >/dev/null 2>&1 || { log "claude CLI not found — aborting"; exit 1; }
command -v jq >/dev/null 2>&1     || { log "jq not found — aborting"; exit 1; }

# Must be on main with a clean tree — never mix auto-fixes with in-progress work.
CUR_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$CUR_BRANCH" != "$BRANCH" ]; then
    log "not on $BRANCH (on $CUR_BRANCH) — aborting"; exit 0
fi
if ! git diff --quiet || ! git diff --cached --quiet; then
    log "working tree is dirty — aborting so auto-fixes don't mix with WIP"; exit 0
fi

# Sync to origin so fixes commit on top of the latest.
git fetch --quiet origin "$BRANCH" 2>/dev/null && \
    git merge --ff-only "origin/$BRANCH" >/dev/null 2>&1 || \
    log "could not fast-forward to origin/$BRANCH — continuing on local HEAD"

# --- Ensure triage labels exist (idempotent) --------------------------------
ensure_label() { gh label create "$1" --color "$2" --description "$3" >/dev/null 2>&1 || true; }
ensure_label "triaged-major"   "B60205" "Auto-triage flagged this for human review"
ensure_label "auto-fixed"      "0E8A16" "Auto-triage fixed and shipped this"
ensure_label "auto-fix-failed" "D93F0B" "Auto-triage attempted a fix but tests failed"

# --- Assessment criteria handed to the model --------------------------------
CRITERIA=$(cat <<'EOF'
CLASSIFY this issue as exactly "minor" or "major".

MINOR — ALL of these must hold (otherwise it is MAJOR):
  - The fix touches at most 2 files and ~30 lines.
  - No database schema or migration change.
  - No auth, session, security, permissions, or data-integrity logic.
  - No new dependency or external service.
  - No route/API contract change other code relies on.
  - Cosmetic or clearly-scoped: copy/typo fixes, label/spacing/colour/icon
    tweaks, a broken link, a missing null-guard, an obvious display bug.
  - You are HIGHLY CONFIDENT the fix is correct and complete. It is either
    verifiable by the existing test suite, OR a self-contained cosmetic /
    CSS / copy change where a passing suite is enough confidence it broke
    nothing.

MAJOR — ANY of these:
  - More than 2 files or ~30 lines, or the root cause is ambiguous / has
    multiple plausible fixes.
  - Any schema/migration, auth/security/data-integrity, dependency, or
    API-contract change.
  - Anything destructive or risking data loss.
  - Performance/architecture work, or a feature needing product/design judgement.
  - It cannot be verified by tests, or your confidence is anything less than high.

TIE-BREAKER: when in doubt, choose MAJOR. A wrongly auto-fixed bug that reaches
the live app is far worse than one that waits a day for human review.
EOF
)

# --- Fetch open, not-yet-triaged issues -------------------------------------
ISSUES_JSON="$(gh issue list --state open --limit 50 \
                 --json number,title,body,labels 2>/dev/null || echo '[]')"

# Keep issues that have bug OR enhancement and NONE of the triage markers.
TARGETS="$(echo "$ISSUES_JSON" | jq -c '
  map(select(
    ([.labels[].name] | any(. == "bug" or . == "enhancement"))
    and ([.labels[].name] | any(. == "triaged-major" or . == "auto-fixed" or . == "auto-fix-failed") | not)
  ))' )"

COUNT="$(echo "$TARGETS" | jq 'length')"
if [ "$COUNT" -eq 0 ]; then
    log "no untriaged bug/enhancement issues — done"
    exit 0
fi
log "found $COUNT issue(s) to triage (processing up to $MAX_ISSUES)"

# --- Per-issue helpers ------------------------------------------------------
discard_edits() {
    git checkout -- . 2>/dev/null || true
    git clean -fd app/ tests/ scripts/ >/dev/null 2>&1 || true
}

queue_major() {  # $1=number $2=title $3=todo_entry $4=reason
    local n="$1" title="$2" entry="$3" reason="$4"
    {
        printf '\n## ISSUE-%s: %s\n\n' "$n" "$title"
        printf '**Source:** auto-triage (%s)\n' "$(date '+%Y-%m-%d')"
        printf '**Why major:** %s\n' "$reason"
        printf '**Link:** https://github.com/Storm-Raider/Fitness-Tracker/issues/%s\n\n' "$n"
        if [ -n "$entry" ]; then printf '%s\n' "$entry"; fi
    } >> "$TODOS"
    git add "$TODOS"
    git commit -q -m "chore(triage): queue #$n for review

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>" || true
    git push -q origin "$BRANCH" 2>/dev/null || log "push of TODOS update failed for #$n"
    gh issue edit "$n" --add-label "triaged-major" >/dev/null 2>&1 || true
    gh issue comment "$n" --body "Daily auto-triage classified this as **major** and added it to TODOS.md for review. Reason: $reason" >/dev/null 2>&1 || true
    log "#$n -> MAJOR (queued to TODOS.md)"
}

# Read the work-list on fd 3, not stdin: claude/gh inside the loop read stdin
# and would otherwise swallow the remaining issues, ending the loop after one.
# Process substitution (not a pipe) also keeps the loop in this shell so the
# counter persists.
processed=0
while IFS= read -r issue <&3; do
    [ "$processed" -ge "$MAX_ISSUES" ] && break
    processed=$((processed + 1))

    NUM="$(echo "$issue" | jq -r '.number')"
    TITLE="$(echo "$issue" | jq -r '.title')"
    BODY="$(echo "$issue" | jq -r '.body // ""')"
    log "triaging #$NUM: $TITLE"

    PROMPT="You are triaging GitHub issue #$NUM for the FitStorm project (self-hosted
fitness tracker; FastAPI + Jinja2 + HTMX; tests live in tests/ and run with pytest;
templates in app/templates; routes in app/routes).

ISSUE TITLE: $TITLE
ISSUE BODY:
$BODY

$CRITERIA

TASK:
1. Investigate the codebase enough to classify the issue.
2. If MINOR: make the minimal, correct fix by editing files directly. Do NOT
   commit, push, run git, or run tests — a wrapper handles that. If the fix
   needs a regression test, add it to the existing tests/ following local
   conventions.
3. If MAJOR: make NO code changes at all.

Respond with ONLY a single minified JSON object on the last line, no markdown
fences, no surrounding prose:
{\"classification\":\"minor\"|\"major\",\"reason\":\"one sentence\",\"summary\":\"one line\",\"commit_message\":\"conventional commit subject incl. (Fixes #$NUM); empty if major\",\"todo_entry\":\"extra markdown context for TODOS.md; empty if minor\"}"

    RAW="$(timeout "$CLAUDE_TIMEOUT" claude -p "$PROMPT" \
              --allowedTools "Read,Edit,Write,Grep,Glob" \
              --output-format json </dev/null 2>>"$REPO/logs/triage.log" || echo '')"

    # Extract the assistant's result text, then the trailing JSON verdict.
    RESULT="$(echo "$RAW" | jq -r '.result // empty' 2>/dev/null)"
    VERDICT="$(printf '%s' "$RESULT" | grep -oE '\{.*\}' | tail -1)"

    if [ -z "$VERDICT" ] || ! echo "$VERDICT" | jq -e . >/dev/null 2>&1; then
        log "#$NUM: could not parse a verdict from Claude — discarding edits, escalating"
        discard_edits
        queue_major "$NUM" "$TITLE" "" "auto-triage could not produce a parseable verdict"
        continue
    fi

    CLASS="$(echo "$VERDICT" | jq -r '.classification')"
    REASON="$(echo "$VERDICT" | jq -r '.reason // ""')"
    COMMIT_MSG="$(echo "$VERDICT" | jq -r '.commit_message // ""')"
    TODO_ENTRY="$(echo "$VERDICT" | jq -r '.todo_entry // ""')"

    if [ "$CLASS" = "major" ]; then
        discard_edits   # safety: ensure nothing was changed
        queue_major "$NUM" "$TITLE" "$TODO_ENTRY" "$REASON"
        continue
    fi

    # --- MINOR: gate on a green test suite before anything ships ------------
    if git diff --quiet && git diff --cached --quiet && [ -z "$(git status --porcelain)" ]; then
        log "#$NUM: classified minor but no edits were made — escalating"
        queue_major "$NUM" "$TITLE" "" "auto-triage classified minor but produced no code change"
        continue
    fi

    log "#$NUM: minor fix applied — running test suite"
    if timeout 1200 "$PYTEST" tests/ -q >>"$REPO/logs/triage.log" 2>&1; then
        [ -n "$COMMIT_MSG" ] || COMMIT_MSG="fix: address issue (Fixes #$NUM)"
        git add -A
        git commit -q -m "$COMMIT_MSG

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>" || { log "#$NUM: commit failed"; discard_edits; continue; }
        if git push -q origin "$BRANCH" 2>/dev/null; then
            gh issue edit "$NUM" --add-label "auto-fixed" >/dev/null 2>&1 || true
            gh issue close "$NUM" --comment "Auto-fixed by daily triage in \`$(git rev-parse --short HEAD)\` and deploying to the live app shortly. Reopen if it isn't resolved." >/dev/null 2>&1 || true
            log "#$NUM -> MINOR fixed, pushed $(git rev-parse --short HEAD)"
        else
            log "#$NUM: push failed — leaving commit local for next run"
        fi
    else
        log "#$NUM: tests FAILED after auto-fix — discarding and escalating"
        discard_edits
        gh issue edit "$NUM" --add-label "auto-fix-failed" >/dev/null 2>&1 || true
        queue_major "$NUM" "$TITLE" "" "auto-fix attempted but the test suite failed; needs manual work"
    fi
done 3< <(echo "$TARGETS" | jq -c '.[]')

log "triage run complete"
