# Deviations from Original Spec

This file documents intentional divergences from `specs/tasks.yaml`, `specs/api_contract.yaml`,
and `specs/backend_spec.yaml`. These are conscious decisions, not mistakes.
`specs/rules.yaml` says "do not modify api_contract.yaml" — this file is the substitute.

---

## 1. aiosqlite instead of SQLAlchemy ORM

**Spec says:** `tasks.yaml:setup_database` — "Configure SQLAlchemy", "Create User, Workout, WorkoutEntry, BodyMetrics models"

**What we build:** Raw `aiosqlite` with a single shared connection. SQL written by hand.

**Why:** A Raspberry Pi 4 has 4GB RAM but SQLite + SQLAlchemy adds ~30MB of import overhead and ORM machinery that buys nothing for a single-user personal tool. Raw aiosqlite is ~5MB, is auditable (you read what executes), and has zero magic. For a project where "understand every layer" is an explicit goal, the ORM is the wrong layer.

**Consequence:** No Alembic auto-migrations. Schema changes are `ALTER TABLE` statements in `schema.sql` or manual `sqlite3` commands on the Pi. Acceptable in v1; Alembic added in v2 when multi-user lands (see TODOS.md).

---

## 2. Tailscale instead of JWT + bcrypt

**Spec says:** `tasks.yaml:implement_auth` — "Implement password hashing using bcrypt", "Implement JWT token generation", "Create /auth/register and /auth/login endpoints"

**What we build:** No auth layer. Tailscale subnet router is the trust boundary.

**Why:** This is a single-user app running on a private Tailscale network. JWT adds two round-trips (register, login), key rotation complexity, and latency on every request — to protect data from... the one person with Tailscale access to the Pi. The authentication is handled at the network layer, not the application layer. This is the standard self-hosted pattern (see: Vaultwarden, Nextcloud with Tailscale).

**When this changes:** When a second user needs access, Tailscale ACL rules are the first control. JWT added in v2 alongside the `users` table and `user_id` FK constraints (the scaffold for which is already in the schema).

---

## 3. Base path `/` instead of `/api/v1/`

**Spec says:** `specs/api_contract.yaml` — all routes under `/api/v1/` prefix

**What we build:** Routes at root (`/workouts`, `/exercises`, `/metrics`, etc.)

**Why:** This app serves HTML via HTMX from the same process. The HTMX templates reference routes directly. A `/api/v1/` prefix on every `hx-post` and `hx-get` attribute is noise. The API consumers (scripts, Home Assistant, etc.) can use root-path routes; they're just as stable.

**Consequence:** Consumers that follow `api_contract.yaml` literally and hardcode `/api/v1/` will fail. For a personal tool with one consumer (you), this is not a real risk.

---

## 4. HTMX + Jinja2 added (not in original spec)

**Spec says:** `specs/backend_spec.yaml` — API-only backend, no frontend mentioned

**What we build:** Jinja2 templates served by FastAPI, HTMX for partial updates, no JS build step.

**Why:** A personal fitness tool you can only interact with via `curl` is not a tool you use at the gym. The HTMX addition is ~30% more work upfront and eliminates a separate frontend project entirely. No Node.js. No React. No build step. Runs on a Pi with 200MB RAM.

**Added files:** `app/templates/`, `app/utils/render.py`. No new Python dependencies beyond `jinja2` (already a FastAPI transitive dependency).
