# Backfill Daily Log — design

**Date:** 2026-07-13
**Status:** Approved, ready for implementation plan
**Tracks:** TODOS.md ISSUE-29

## Problem

`saveLog()` in `app/templates/journal.html` hardcodes `log_date: TODAY` — there's
no way to add or edit a daily-log entry for any date other than today, even
though the backend (`POST /journal`) already accepts an arbitrary `log_date`
and upserts on `(user_id, log_date)`. Users can only view/edit past entries
indirectly, by clicking a history row to copy its data into today's form
(which then saves under *today's* date, not the original one).

## Goal

Add a date picker to the Daily Log form so a user can view, create, or edit
an entry for any past date, not just today.

## Backend: one new endpoint

`GET /journal/entry?date=YYYY-MM-DD` (new route in `app/routes/journal.py`)

- Returns `{"entry": {...} | null}` — the current user's `daily_logs` row for
  that date, in the same shape as the existing `history`/`today_log` rows
  (same dict keys), or `null` if no entry exists for that date.
- Validates the date parses with `date.fromisoformat()`; a malformed date
  returns 422. Does **not** reject future dates — the UI already blocks
  picking them via the date input's `max` attribute, and querying one simply
  and correctly returns `null` (no special-casing needed).
- Requires `get_current_user`, scoped to `user_id=uid`, same as every other
  route in this file.

No changes to the existing `GET /journal` page load (still only sends
today's entry + last 60 days of history — unchanged) or `POST /journal` (the
upsert already handles any `log_date` correctly; re-saving an existing date
overwrites it via `ON CONFLICT(user_id, log_date) DO UPDATE`, not a new row).

## Frontend: one unified load function

All changes confined to `app/templates/journal.html`.

- New `<input type="date" id="j-date">`, default value = today, `max` = today
  (blocks future dates), no `min` (any past date is pickable — an explicit,
  deliberate choice: unlimited range is simplest and the lookup endpoint
  handles "does this date have data" correctly no matter how far back).
- New `loadEntryForDate(dateStr, entry)` — the single function that drives
  every date change:
  - Sets the date picker's value to `dateStr`.
  - Every call **fully clears and repopulates every field** — day number,
    weight, workout, all three meals, water, sleep, steps, notes, energy,
    motivation — either from `entry`'s values or to blank/no-selection if
    `entry` is `null` or a given field is absent from it. This is a
    deliberate fix to the current `fillFromHistory`, which only overwrites
    fields that are truthy on the clicked entry (`if (entry.weight_kg)
    document.getElementById('j-weight').value = ...`) and silently leaves
    whatever was already typed in any field the entry doesn't have a value
    for — so switching from a detailed entry to a sparser one today can
    leave stale data from the previous date sitting in the form. The new
    function must not repeat that: every field is explicitly set or cleared
    on every call, never left untouched.
  - If `entry` is passed in (the history-row-click case, which already has
    the data in hand), populates the form directly from it — no network
    call.
  - Otherwise (the date-picker-changed case), fetches
    `/journal/entry?date=dateStr` and populates from the response the same
    way. This is the **only** path for a date-picker change — there's no
    separate "check the already-rendered 60-day history array first"
    shortcut. One code path is simpler than two, and the extra round-trip
    is free on a local Pi.
  - Updates the card title: `"Today's entry"` when `dateStr === TODAY`,
    otherwise `"Entry for " + dateStr` (the raw ISO string, matching how
    history rows already display dates — no new date-formatting helper).
  - The day-number field's auto-fill from the active challenge's current day
    count (`active_day`, computed server-side at page load) only applies
    when `dateStr === TODAY` and there's no saved entry for today yet. It is
    never applied when loading a past date with no entry — `active_day`
    reflects *today's* position in the challenge and would be wrong for any
    other date, so a past date with no entry gets a genuinely blank
    day-number field.
- `fillFromHistory(entry)` (clicking a history row) becomes a thin wrapper:
  call `loadEntryForDate(entry.log_date, entry)`. The date picker updates to
  match, per the approved "auto-load immediately, no prompt" behavior — this
  replaces the current `confirm()` popup, since selecting a date via the
  picker and selecting one via a history row are now the same underlying
  action (viewing/editing a specific date's entry) and should behave
  identically.
- `saveLog()` reads `document.getElementById('j-date').value` instead of the
  hardcoded `TODAY` constant for the `log_date` field in its POST payload.

## Edge cases

- **Unsaved changes on date switch:** no dirty-check or warning — picking a
  new date (via picker or history row) loads it immediately, silently
  discarding whatever's currently in the visible fields. This matches
  existing behavior (`fillFromHistory` already does this today with no
  guard), so it's not a regression — just noting it's not in scope to add
  a "you have unsaved changes" prompt here.
- **Re-saving an existing date:** already correct via the existing
  `ON CONFLICT(user_id, log_date) DO UPDATE` upsert — no new logic needed.
- **Picking today after viewing a past date:** works the same as any other
  date change, through `loadEntryForDate(TODAY)` — no special-casing beyond
  the title reverting to "Today's entry".

## Testing

- New endpoint returns the correct entry for a date that has one.
- New endpoint returns `{"entry": null}` for a date with no entry.
- New endpoint requires auth (redirects/401s for an unauthenticated request,
  matching this file's existing auth pattern).
- New endpoint returns 422 for a malformed date string.
- No new backend tests needed for `POST /journal` — its upsert behavior for
  arbitrary dates is already covered by existing tests and unchanged here.

## Out of scope

- Any change to the 60-day history window size or the main `GET /journal`
  page's query.
- A "you have unsaved changes" warning when switching dates.
- Any change to `POST /journal`'s upsert logic.
