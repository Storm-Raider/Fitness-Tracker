# FitStorm Design System

Source of truth: `app/templates/base.html`. This file is a human-readable extract — if there's a conflict, the CSS wins.

---

## Product Context

- **What this is:** A self-hosted fitness tracker that runs on a Raspberry Pi. Log workouts, track PRs, own your data.
- **Who it's for:** Pi builders and privacy-conscious lifters who chose self-hosting deliberately. Technically capable users who know what a SQLite file is.
- **Space:** Self-hosted fitness tracking. Adjacent to Strong and Hevy, but not competing on their terms.
- **Project type:** Web app + PWA. FastAPI + Jinja2 + HTMX. Works offline when the Pi is unreachable.

---

## Aesthetic Direction

**Pi-grade Industrial**

The visual language of serious software — a server dashboard, not a consumer app. Dark, data-dense, earned. Every pixel earns its place.

Hevy looks like a social app. Strong looks like a marketing site. FitStorm looks like infrastructure — because it is. The dark palette isn't mood-setting, it's a position: Pi builders live in dark terminals, and data is what matters.

**Decoration level:** Minimal. A subtle dot-grid background texture on `body` only. No gradients, no hero illustrations, no decorative blobs. Typography does all the work.

**Layout:** Grid-disciplined. `max-width: 1100px` container, 2-panel dashboard (list + sidebar) on ≥768px, single column on mobile.

---

## Memorable Thing

> "This is real software that a real person built and owns."

Not a product someone signed up for — something someone runs. The visual language should reinforce the identity of the person who chose to self-host: technical, deliberate, uninterested in being sold to.

---

## Design Principles

**1. Data over decoration.** Every visual element either communicates data or gets out of the way. No gradients, no hero illustrations. If it doesn't tell you something, it isn't there.

**2. Gold is earned.** The PR gold (`--pr: #f59e0b`) appears exactly once per set that beats the user's personal record. It is FitStorm's primary identity color precisely because it's rare. Never use gold for decoration or UI chrome. Blue is plumbing. Gold is achievement.

**3. Gym-use touch targets.** Hands are sweaty. Attention is split. Every primary action gets ≥56px. Steppers are 40×40px minimum (52px target). The Log Set button is the most-tapped element in the app.

**4. Dark by conviction.** No light mode — not an oversight, a position. Pi builders live in dark terminals and lift in dim gyms. Light mode would signal "we're trying to appeal to everyone." FitStorm isn't.

---

## Deliberate Risks (Where FitStorm Gets Its Own Face)

These are intentional departures from the fitness app category. They are policy, not accidents.

| Risk | What | Why |
|------|------|-----|
| **Gold as identity, not accent** | `--pr` (#f59e0b) is the primary brand color, used only for PRs | Every fitness app uses blue or orange as their hero color. Reserving gold for earned moments makes it genuinely meaningful and visually distinctive. |
| **No light mode** | Dark-only, by design | An explicit position. Documenting it prevents recurring "add light mode" requests. The answer is no — the target user doesn't want one. |
| **Syne as display typeface** | Geometric, slightly cold — unusual for fitness apps | Most fitness apps use rounded, friendly typefaces (Poppins, Nunito). Syne signals technical software, not a lifestyle brand. This is intentional. |

**Safe choices (category baseline — play these straight):**
- Dark background + card elevation surfaces (every serious dark app does this)
- Monospace for numeric data (developer-legible, expected)
- Blue for interactive chrome (universal expectation — links, buttons, focus rings)

---

## Spacing

**Base unit:** 8px

| Token | Value | Use |
|-------|-------|-----|
| 2xs | 2px | Tight internal gaps |
| xs | 4px | Icon-to-label, badge padding |
| sm | 8px | Compact row padding |
| md | 16px | Card internal padding |
| lg | 24px | Section gaps |
| xl | 32px | Page-level vertical rhythm |
| 2xl | 48px | Section separators |
| 3xl | 64px | Page header spacing |

**Density:** Comfortable. Data-dense enough to feel like a real tool; not so tight it's hard to tap.

---

## Motion

**Approach:** Minimal-functional. Motion earns its place only when it aids comprehension.

| Duration | Range | Use |
|----------|-------|-----|
| Micro | 50–100ms | Hover state transitions |
| Short | 150–250ms | Button press, badge appear, set append |
| Medium | 250–400ms | Page enter (fadeUp 0.22s, 8px translateY) |

**Easing:** `ease-out` on enter, `ease-in` on exit, `ease-in-out` on positional moves.

**Never:** scroll-driven animations, loading choreography, entrance animations on repeated elements (table rows, badge lists). Motion is for state changes the user caused, not decoration.

---

## Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-05-30 | Aesthetic direction: Pi-grade Industrial | Competitive research (Hevy, Strong) showed the entire category looks like consumer apps. FitStorm's users chose self-hosting deliberately — the visual language should reinforce that identity. |
| 2026-05-30 | Gold (#f59e0b) named as primary identity color, not accent | Gold appears only on earned PR moments. Making it the identity color turns rarity into brand. Blue is plumbing. Gold is achievement. |
| 2026-05-30 | No light mode — documented as explicit position | Not an oversight. Pi builders use dark environments. Documenting this ends recurring discussion. |
| 2026-05-30 | Syne (geometric, slightly cold) retained as display font | Unusual in fitness apps. Signals technical software over lifestyle brand. Intentional departure from category convention. |

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
| CSV import | button → "Importing…", disabled | — | Inline flash (detail from server) | "Imported N sets (M skipped)." flash |
| HTMX partial swap | — (swap is instant) | — | — | Target element replaced |
| Active session card | — (server-rendered) | Card hidden when no active session | — | — |
| Active session (0 sets) | — | "Ready to log · X min elapsed" | — | — |
| Stats — sparkline | — (server-rendered) | "Your training arc appears here…" + [Start Session →] link | — | — |
| Stats — top exercises | — | "No sets logged yet." | — | — |
| Stats — muscle coverage | — | "No workouts logged this week." | — | — |
| Invite revoke | — (HTMX swap) | "No pending invites." in card | — | Row removed via outerHTML swap |

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
