# FitStorm Design System

Source of truth: `app/templates/base.html`. This file is a human-readable extract — if there's a conflict, the CSS wins.

---

## Color Tokens

```css
/* Core surfaces */
--bg:           #090b10   /* page background */
--surface:      #0f1219   /* cards, nav, inputs */
--surface-2:    #161b24   /* elevated surfaces (hover rows, chips) */
--surface-hover:#1c2230   /* interactive surface hover state */
--border:       #1e2334   /* dividers, input borders */

/* Text */
--text:         #e4eaf2   /* primary text */
--muted:        #5a6a82   /* labels, timestamps, placeholders */

/* Interactive */
--accent:       #4f9cf9   /* links, primary buttons, focus rings */
--accent-hover: #7ab8fc   /* hover state */
--accent-dim:   rgba(79,156,249,0.09)   /* hover backgrounds */

/* Semantic */
--danger:       #f87171   /* errors, delete actions */
--success:      #34d399   /* success states, streak badge */
--pr:           #f59e0b   /* PRs, achievement identity color */
--pr-dim:       rgba(245,158,11,0.12)   /* PR highlight backgrounds */

/* Shadows & glow */
--shadow-sm:    0 1px 3px rgba(0,0,0,0.7), 0 0 0 1px rgba(255,255,255,0.04)
--shadow:       0 6px 20px rgba(0,0,0,0.6)
--glow:         0 0 0 3px rgba(79,156,249,0.22)   /* focus ring (accent) */
--glow-pr:      0 0 0 3px rgba(245,158,11,0.2)    /* focus ring (PR) */
```

**Rule:** Blue (`--accent`) = interactive chrome. Gold (`--pr`) = data/achievement. Never swap — a gold button or a blue PR value is wrong.

---

## Typography

| Role | Font | Weights | Size |
|------|------|---------|------|
| Headings (h1/h2/h3) | Syne | 600, 700, 800 | varies |
| UI / prose | Barlow | 400, 500, 600 | 15px base |
| Numeric data | JetBrains Mono | 500, 600, 700 | inherits |

Fonts are self-hosted as `.woff2` in `app/static/fonts/`. No Google Fonts CDN call at runtime — works fully offline.

**Heading style:** `letter-spacing: -0.02em` — tighter than default, gives the geometric Syne letters more presence.

**Label style:** `0.72rem`, `font-weight: 700`, `text-transform: uppercase`, `letter-spacing: 0.06em`, `color: var(--muted)`. Used for form labels and section headers.

**Section title style** (`.section-title`): `0.68rem`, `font-weight: 700`, `text-transform: uppercase`, `letter-spacing: 0.1em`, `color: var(--muted)`.

**`.num` class:** Apply to any weight (kg), rep count, volume, duration, or PR value — renders in JetBrains Mono.

---

## Layout

**Container:** `max-width: 1100px`, centered, `padding: 1.5rem`. Fade-up entry animation (0.25s, 6px translateY).

**Dashboard — two-panel (≥768px):**
- Left: workout list (flex-1)
- Right: PR sidebar / stats (fixed ~260px)

**Dashboard — single column (<768px):**
- Workout list first, stats below (natural scroll order)

**Grids:**
- `.grid-2`: two equal columns, collapses to 1 at ≤640px
- `.grid-3`: three equal columns, collapses to 1 at ≤640px

**Nav:** sticky top, `height: 52px`, `--surface` background with bottom border + shadow.

---

## Components

### Cards

```css
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.25rem;
  box-shadow: var(--shadow-sm);
  /* subtle top-left highlight via ::before pseudo-element */
}
```

### Buttons

| Class | Background | Text | Use |
|-------|-----------|------|-----|
| `.btn-primary` | Blue gradient (#4f9cf9 → #3b82f6) | `#fff` | Primary actions (Log Set) |
| `.btn-ghost` | Transparent | `--muted` | Secondary actions (Clear, Cancel) |
| `.btn-danger` | `--danger` | `#fff` | Destructive (Delete) |

Minimum height: `38px` (`.btn` base). Primary action buttons use inline `min-height: 56px` where the touch target spec requires it.

### Badges

| Class | Background | Text | Font | Use |
|-------|-----------|------|------|-----|
| `.badge-pr` | `--pr-dim` | `--pr` | JetBrains Mono | Personal record |
| `.badge-streak` | green-dim | `--success` | Barlow | Consecutive-day streak |

### Form fields

Inputs, selects, textareas: `--bg` background, `--border` border, `border-radius: 8px`, `0.875rem` font size. Focus: `border-color: var(--accent)`, `box-shadow: var(--glow)`.

### Stepper group

```
[−] [numeric input] [+]
```

`.stepper-btn`: `40×40px`, border + `--surface` background. On hover: `--accent` border/color, `--accent-dim` background.

### Tables

`th`: `0.68rem`, uppercase, `letter-spacing: 0.08em`, `--muted`. `td`: `0.875rem`. Row hover: `rgba(255,255,255,0.025)` background.

---

## Icons

Lucide **v0.378.0** via CDN. Pinned — do not use `@latest`.

- Nav icons: 18px
- Card / inline icons: 16–20px
- `stroke-width: 2`, `stroke: currentColor` (inherits text color)
- Re-initialize after HTMX swaps: call `lucide.createIcons()` in `htmx:afterSwap` listener

---

## Touch Targets (gym-use focused)

| Element | Target size | Notes |
|---------|------------|-------|
| Nav links | 44px height | Full nav bar height (52px) satisfies this |
| Stepper buttons (−/+) | 52×52px | CSS currently 40×40px — padding expansion planned |
| Log Set button | 56px height | Primary action, frequent tap |
| Finish Workout button | 56px height | Primary action |
| Set row delete (×) | 44×44px | Visually smaller; padding expands tap target |
| Exercise search results | 48px per result | datalist — browser-controlled |

---

## Interaction States

| Screen | Loading | Empty | Error | Success |
|--------|---------|-------|-------|---------|
| Dashboard | — (server-rendered) | "No workouts yet. [Start Workout →]" — centered, `--text-dim`, `--accent` link | — | — |
| Log Set | button text → "Logging…" | — | "Failed — try again" inline below button | Set row appends, form retains last values |
| Metrics form | button → "Saving…" | — | Inline error | "Saved" (2 s flash) |
| Exercise search | — | "No match" + "+ Add as new exercise" (JS-injected) | — | Name appears in field |
| CSV export | Browser native | — | — | File downloads |
| HTMX partial swap | — (swap is instant) | — | — | Target element replaced |

**PR badge:** Shown immediately after `POST /sets` returns `{"is_pr": true}`. Gold (`--pr-dim` bg, `--pr` text, JetBrains Mono), appears inline on the set row. First set of any exercise always earns one.

**Empty states:**
- Dashboard (no workouts): "No workouts yet. / Track your first session to start building your history. / [Start Workout →]" — center-aligned, subtitle in `--muted`, link in `--accent`
- PR table (no sets): "PRs appear here after your first workout." — single line, `--muted`

---

## Accessibility

**Keyboard navigation:**
- Tab order on workout form: exercise search → weight input → reps input → Log Set
- Arrow keys on stepper inputs increment by step value
- Escape closes any open datalist / dropdown

**ARIA landmarks:**
- `<main>` on every page
- `<nav aria-label="Main navigation">` in base template
- Timer: `aria-live="off"` (suppress per-second announcements)
- Set list region: `aria-live="polite"` (announce new set after logging)
- PR badge: `aria-label="Personal record"`

**Autofocus:**
- Workout form: exercise search input gets `autofocus` on load
- After logging a set: focus returns to exercise search (ready for next set)

**Color contrast (WCAG):**

| Foreground | Background | Ratio | Grade |
|-----------|-----------|-------|-------|
| `--text` #e4eaf2 | `--bg` #090b10 | ~15:1 | AAA |
| `--muted` #5a6a82 | `--bg` #090b10 | ~5.4:1 | AA |
| `--accent` #4f9cf9 | `--bg` #090b10 | ~7.5:1 | AA |
| `--pr` #f59e0b | `--surface` #0f1219 | ~8.2:1 | AAA |
| `#fff` | `--accent` #4f9cf9 | ~3.8:1 | AA (large text) |
| `#000` | `--pr` #f59e0b | ~10.5:1 | AAA |

---

## Background Texture

The page body has a subtle dot-grid texture (`radial-gradient`, 28px repeat, 5% white dots at 1px). Adds depth without competing with content. Do not apply to cards or surface elements.

---

## HTMX Interaction Map

**HTMX is used in exactly 3 places.** The workout form (`/workouts/{id}`) uses vanilla `fetch()` for everything — no HTMX there.

### True HTMX interactions

| Action | Method + URL | `hx-target` | `hx-swap` | Server response |
|--------|-------------|-------------|-----------|-----------------|
| Generate invite link | `POST /invite` | `#invite-result` | `innerHTML` | `invite_partial.html` fragment (link + copy button) |
| Delete workout (from list) | `DELETE /workouts/{id}` | `#workout-{id}` | `outerHTML` | empty 200 (element removed) |
| Delete body metric | `DELETE /metrics/{id}` | `#metric-{id}` | `outerHTML` | empty 200 (row removed) |

Delete actions both carry `hx-confirm="..."` — browser native confirm dialog fires before the request. No JS required.

**Re-initialize Lucide after swap:** All HTMX swap targets that inject new HTML must call `lucide.createIcons()` to render icon SVGs. The base template registers a global `htmx:afterSwap` listener that does this automatically.

### Vanilla `fetch()` interactions (workout form)

The workout form JS (`workouts/{id}`) owns all interactions below. No HTMX.

| Action | Method + URL | DOM update |
|--------|-------------|------------|
| Log set | `POST /workouts/{id}/sets` | Prepend `div#set-{id}` to `#sets-container`; show PR badge for 3 s if `data.is_pr` |
| Delete set | `DELETE /workouts/{id}/sets/{sid}` | `document.getElementById('set-' + id)?.remove()` |
| Finish workout | `POST /workouts/{id}/finish` | Populate `#finish-modal` fields, set `display: flex` |
| Delete workout | `DELETE /workouts/{id}` | `window.location.href = '/workouts'` |
| Load routines | `GET /routines` | Build `<option>` list inside `#routine-select` |
| Load exercises | `GET /api/exercises` | Populate `<datalist>` + `#muscle-group-select` options |
| Save routine | `POST /routines` | Close modal, call `loadRoutines()` to refresh dropdown |
| Patch notes | `PATCH /workouts/{id}` | No DOM update; debounced autosave |

**Why fetch() not HTMX on the workout form:** The log-set response drives multiple DOM mutations simultaneously (append row, show/hide PR badge, update volume total, reset form). HTMX's single-target swap model can't express that without `hx-swap-oob`, which would require the server to render partial fragments it doesn't currently own. The JS approach is 30 lines and keeps the server returning clean JSON.
