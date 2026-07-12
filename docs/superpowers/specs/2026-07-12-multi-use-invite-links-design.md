# Multi-use invite links — design

**Date:** 2026-07-12
**Status:** Approved, ready for implementation plan

## Problem

Invite links are currently one-time-use: `invite_tokens.used_at` gets set on the
first successful signup, and any further attempt to accept that same token is
rejected as "invalid or expired." An admin who wants to bring in more than one
person (e.g. a few training partners) has to generate and share a separate
link per person.

## Goal

A single generated invite link can be used for a **fixed number of signups**,
chosen by the admin at generation time, before it stops working. No per-user
audit trail is needed — just a remaining-uses counter.

## Data model

`invite_tokens` table changes (new migration + updated `schema.sql`):

| Column | Change |
|---|---|
| `max_uses` | **new** `INTEGER NOT NULL DEFAULT 1` |
| `uses_count` | **new** `INTEGER NOT NULL DEFAULT 0` |
| `used_at` | **repurposed** — stays `NULL` until the first signup, then updated on *every* subsequent successful signup (last-used timestamp, not first-used) |
| `used_by` | **dropped** — a single FK can't represent "used by N different people," and a per-signup audit trail is explicitly out of scope |

Existing rows get `max_uses = 1` via the migration's default, so invite links
created before this change keep their original one-time-use behavior — no
retroactive expansion of old links.

New link defaults: **7-day expiry** (was 48 hours — extended because rounding
up multiple people takes longer than one), **`max_uses = 5`**, admin-editable
at generation time (validated to an integer between 1 and 50).

### Rejected alternative

A separate `invite_uses` log table (one row per signup, keeping
`invite_tokens` as pure metadata) was considered. It's more normalized and
would support a future "who signed up with this link" view, but that's
exactly the audit trail this feature explicitly doesn't need right now — it
would add a table and joins for no current benefit. Extending `invite_tokens`
with a counter is the simpler correct fit for "just show remaining uses."

A third option — reusing the same token string across multiple rows — was not
seriously considered: `token` is the table's primary key, so this would
require restructuring the table for no gain over the counter approach.

## Route behavior (`app/routes/auth.py`)

- **`POST /invite`** (admin, generate link): accepts a new `max_uses` form
  field (default 5). Validated server-side to an integer between 1 and 50
  via a Pydantic `Field(ge=1, le=50)` constraint, matching the existing
  `GoalIn`/`ExerciseIn` validation pattern in this codebase — an out-of-range
  value gets FastAPI's standard 422 response, no new error-handling UI needed.
  Inserts the token with `expires_at = now + 7 days`, the given `max_uses`,
  `uses_count = 0`.
- **Validity check for accepting an invite**: changes from
  `used_at IS NULL AND expires_at > now` to
  `uses_count < max_uses AND expires_at > now`. This is a **new, dedicated**
  check written specifically for `invite_tokens` — the shared
  `_fetch_valid_token` helper (also used by `password_reset_tokens`, which
  stays single-use) is not modified, so password-reset behavior is untouched.
- **`POST /invite/accept/{token}`** (signup submit): increments `uses_count`
  atomically as part of the same `UPDATE` that re-checks validity:
  ```sql
  UPDATE invite_tokens
  SET uses_count = uses_count + 1, used_at = datetime('now','localtime')
  WHERE token = ? AND uses_count < max_uses AND expires_at > datetime('now','localtime')
  ```
  If this affects zero rows — because someone else claimed the last
  remaining slot between the initial validity check and this update — the
  signup is rejected with the same "Invalid or expired invite link" error
  already used for expired links. This is what keeps concurrent signups from
  pushing a link's use count past `max_uses`.
- **`GET /invite`** (admin "Pending invites" list): filter changes to
  `uses_count < max_uses AND expires_at > now`. An exhausted or expired link
  simply drops off the pending list, same as today.
- **`DELETE /invite/{token}`** (revoke): unchanged — hard-deletes the row
  regardless of how many uses remain.

## UI changes (`app/templates/invite.html`)

- Subtitle: "One-time registration links" → "Shareable registration links".
- Generate panel copy: "Generate an invite link good for multiple signups. It
  expires after 7 days." Adds a "Max uses" number input (default 5) next to
  the Generate button.
- Pending invites table: adds a "Uses" column showing `{{ inv.uses_count }} /
  {{ inv.max_uses }}` alongside Created/Expires. Revoke button unchanged.
- `invite_accept.html` (the page invitees see before signing up): **no
  change**. It doesn't show remaining-use info today and this feature doesn't
  add any — kept out of scope.

## Edge cases

- Links created before this change: `max_uses` defaults to 1 via the
  migration, preserving their original one-time-use behavior.
- Concurrent signups near the cap: handled by the atomic `UPDATE ... WHERE`
  above — only as many requests succeed as slots remain.
- Expired-but-not-exhausted and exhausted-but-not-expired both read as
  "invalid" on the accept page — same single error message as today, no new
  user-facing states.

## Testing

- Generating a link with a custom `max_uses`.
- Accepting up to the cap succeeds for each signup.
- The `(cap + 1)`th accept attempt fails.
- Expiry is still enforced independent of remaining uses (an unexhausted but
  expired link is rejected).
- Revoke still deletes the row regardless of `uses_count`.

## Out of scope

- Per-signup audit trail (who used which link).
- Remaining-uses display on the invitee-facing accept page.
- Changing password-reset token behavior (single-use, untouched).
