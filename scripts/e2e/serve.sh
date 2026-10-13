#!/bin/bash
# Throwaway Zenkai server for the browser checks: port 8765 (E2E_PORT), a fresh
# database in a temp directory (E2E_TMP, default a new mktemp dir), an admin
# account e2eadmin. Never touches the real DB.
#   scripts/e2e/serve.sh [checkout]   (default: this checkout)
ROOT="${1:-$(cd "$(dirname "$0")/../.." && pwd)}"
TMP="${E2E_TMP:-$(mktemp -d)}"
cd "$ROOT" && exec env -i PATH="$PATH" HOME="$HOME" \
  DATABASE_PATH="$TMP/e2e.db" ADMIN_USERNAME=e2eadmin ADMIN_PASSWORD=e2e-admin-password-123 \
  APP_SECRET=$(printf 'b%.0s' {1..40}) \
  .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${E2E_PORT:-8765}" --log-level "${LOG_LEVEL:-warning}"
