#!/bin/bash
# Run every browser check, each against its own throwaway server (a fresh
# database in a temp dir, port 8766 so a manual server on 8765 is left alone).
# Never touches the real database or the running app. Prints one summary line:
#   2026-10-13 04:15  PASS 8/8
#   2026-10-13 04:15  FAIL 7/8: a11y (passed on retry: sheet_drag)
# and keeps each check's full output in logs/e2e/<run>/ (the last 14 runs).
# A check that fails is run once more on a fresh server; passing the second
# time is reported, because a flaky check is worth knowing about too.
#   scripts/e2e/run_all.sh            (cron: 15 4 * * *, see README)
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PORT="${E2E_PORT:-8766}"
export E2E_BASE="http://127.0.0.1:$PORT"
OUT="$ROOT/logs/e2e/$(date +%Y%m%d-%H%M)"
# The newest nvm Node first: cron's PATH finds only the system one, which can be too old.
NODE="${NODE:-$(ls -1d "$HOME"/.nvm/versions/node/*/bin/node 2>/dev/null | sort -V | tail -1)}"
NODE="${NODE:-$(command -v node)}"
# E2E_SUITES picks some; offline stops its server, so it goes last.
SUITES="${E2E_SUITES:-a11y.mjs no_overflow.test.mjs js_errors.test.mjs confirm_sheet.test.mjs
        journal_zero.test.mjs sheet_drag.test.mjs coach_resume.test.mjs ui_usability.test.mjs offline.test.mjs}"

exec 9>"$ROOT/logs/.e2e.lock"
flock -n 9 || { echo "$(date '+%F %H:%M')  SKIP: another run is still going"; exit 0; }
if [ -z "$NODE" ] || [ ! -x "$NODE" ]; then
  echo "$(date '+%F %H:%M')  ERROR: node not found"; exit 1
fi
mkdir -p "$OUT"

stop_server() {
  if [ -n "${SRV:-}" ]; then
    kill "$SRV" 2>/dev/null
    for _ in $(seq 20); do kill -0 "$SRV" 2>/dev/null || break; sleep 0.25; done
  fi
  fuser -k "$PORT/tcp" >/dev/null 2>&1   # anything still holding the port (offline.test stops its own)
  for _ in $(seq 20); do curl -sf -o /dev/null "$E2E_BASE/login" || break; sleep 0.5; done
  [ -n "${TMPD:-}" ] && rm -rf "$TMPD"
  SRV=""; TMPD=""
}
trap stop_server EXIT

# run_one <suite> <log>: fresh server, run the suite, stop the server. Exit status is the suite's.
run_one() {
  stop_server
  TMPD="$(mktemp -d)"
  # Started from a subshell so it isn't one of this shell's jobs: offline.test kills
  # its own server, and bash would print "Killed" into the nightly log.
  SRV=$(E2E_TMP="$TMPD" E2E_PORT="$PORT" bash "$ROOT/scripts/e2e/serve.sh" "$ROOT" >>"$2" 2>&1 & echo $!)
  for _ in $(seq 60); do curl -sf -o /dev/null "$E2E_BASE/login" && break; sleep 1; done
  if ! curl -sf -o /dev/null "$E2E_BASE/login"; then
    echo "server did not start" >>"$2"; return 1
  fi
  E2E_DB="$TMPD/e2e.db" timeout 900 "$NODE" "$ROOT/scripts/e2e/$1" >>"$2" 2>&1
  local rc=$?
  stop_server
  return $rc
}

failed=(); flaky=(); passed=0; total=0
for suite in $SUITES; do
  total=$((total + 1))
  name="${suite%%.*}"
  if run_one "$suite" "$OUT/$name.log"; then
    passed=$((passed + 1))
  elif echo "--- retry on a fresh server ---" >>"$OUT/$name.log" && run_one "$suite" "$OUT/$name.log"; then
    passed=$((passed + 1)); flaky+=("$name")
  else
    failed+=("$name")
  fi
done

line="$(date '+%F %H:%M')  "
if [ ${#failed[@]} -eq 0 ]; then line+="PASS $passed/$total"; else line+="FAIL $passed/$total: ${failed[*]}"; fi
[ ${#flaky[@]} -gt 0 ] && line+=" (passed on retry: ${flaky[*]})"
echo "$line"
echo "$line" >"$OUT/summary.txt"

# E2E_NOTIFY=github (the cron line sets it): a failed run opens a GitHub issue,
# or comments on the one already open; the next clean run closes it. Only full
# runs report, and a check that passed on its retry doesn't open one. The
# label keeps it out of scripts/triage-issues.sh (bug/enhancement only).
notify_github() {
  local label="nightly-checks" open body
  gh label create "$label" --color FBCA04 --description "The nightly browser checks failed" >/dev/null 2>&1 || true
  open="$(gh issue list --state open --label "$label" --limit 1 --json number -q '.[0].number' 2>/dev/null)"
  if [ ${#failed[@]} -eq 0 ]; then
    [ -n "$open" ] && gh issue close "$open" --comment "Clean again: \`$line\`" >/dev/null
    return 0
  fi
  body="\`$line\`"$'\n'
  for name in "${failed[@]}"; do
    body+=$'\n'"**$name**"$'\n```\n'"$(grep -E 'FAIL|Error|error|Timeout|not ' "$OUT/$name.log" | grep -v '^INFO' | head -12)"$'\n```\n'
  done
  body+=$'\n'"Full output on the Pi: \`logs/e2e/$(basename "$OUT")/\`. Each check ran twice, on fresh servers."
  if [ -n "$open" ]; then
    gh issue comment "$open" --body "$body" >/dev/null
  else
    gh issue create --label "$label" --title "Nightly browser checks failing: ${failed[*]}" --body "$body" >/dev/null
  fi
}
if [ "${E2E_NOTIFY:-}" = "github" ] && [ -z "${E2E_SUITES:-}" ]; then
  notify_github || echo "$(date '+%F %H:%M')  could not update the GitHub issue"
fi

# Keep the last 14 runs' logs.
ls -1d "$ROOT"/logs/e2e/*/ 2>/dev/null | sort | head -n -14 | xargs -r rm -rf
[ ${#failed[@]} -eq 0 ]
