# Deviations from Original Spec

This file documents intentional divergences from `specs/tasks.yaml`, `specs/api_contract.yaml`,
and `specs/backend_spec.yaml`. These are conscious decisions, not mistakes.
`specs/rules.yaml` says "do not modify api_contract.yaml" — this file is the substitute.

---

## 1. aiosqlite instead of SQLAlchemy ORM

**Spec says:** `tasks.yaml:setup_database` — "Configure SQLAlchemy", "Create User, Workout, WorkoutEntry, BodyMetrics models"

**What we build:** Raw `aiosqlite` with a single shared connection. SQL written by hand.

**Why:** A Raspberry Pi 4 has 4GB RAM but SQLite + SQLAlchemy adds ~30MB of import overhead and ORM machinery that buys nothing for a single-user personal tool. Raw aiosqlite is ~5MB, is auditable (you read what executes), and has zero magic. For a project where "understand every layer" is an explicit goal, the ORM is the wrong layer.

**Consequence:** No Alembic auto-migrations. Schema changes are `ALTER TABLE` statements appended to `_MIGRATIONS` in `app/db.py`, run idempotently on every startup. Multi-user shipped in v0.3.0 using this pattern without Alembic. Alembic remains deferred until a migration becomes complex enough that the hand-rolled approach breaks down (see TODOS.md).

---

## 2. HMAC session tokens instead of JWT; Tailscale as network boundary

**Spec says:** `tasks.yaml:implement_auth` — "Implement password hashing using bcrypt", "Implement JWT token generation", "Create /auth/register and /auth/login endpoints"

**What we build (v0.3.0):** bcrypt for password hashing ✓. `itsdangerous` HMAC-signed session tokens in an `httponly SameSite=Strict` cookie instead of JWT. `/login` and `/invite/accept` endpoints instead of `/auth/register` and `/auth/login`. Tailscale recommended as the network-layer trust boundary on top of app auth.

**Why no JWT:** JWT is stateless by design, which means token revocation requires a blocklist (extra DB query per request) or short expiry + refresh tokens (extra round-trips). For a self-hosted personal tool, a server-signed opaque session cookie achieves the same security with none of that complexity. The token is validated in one line (`URLSafeTimedSerializer.loads`), expires server-side by timestamp, and requires no extra table.

**Why invite-only instead of open `/auth/register`:** This is a household or small-group app, not a public service. Invite-gated registration means the admin controls who can create an account — appropriate when the Pi is on a home network and Tailscale ACLs are the first line of defence.

**Consequence:** Consumers that expect a JWT Bearer token in the `Authorization` header will not work. Session cookies are the only auth mechanism.

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
