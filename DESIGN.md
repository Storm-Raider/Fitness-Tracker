# Zenkai Design System

Source of truth: `app/templates/base.html`. This file is a human-readable extract — if there's a conflict, the CSS wins.

---

## Product Context

- **What this is:** A self-hosted fitness tracker that runs on a Raspberry Pi. Log workouts, track PRs, own your data.
- **Who it's for:** Pi builders and privacy-conscious lifters who chose self-hosting deliberately. Technically capable users who know what a SQLite file is.
- **Space:** Self-hosted fitness tracking. Adjacent to Strong and Hevy, but not competing on their terms.
- **Project type:** Web app + PWA. FastAPI + Jinja2 + HTMX. Works offline when the Pi is unreachable.

---

## Aesthetic Direction

**Redline Console** (v2 of Pi-grade Industrial)

The visual language of a live instrument you're piloting, not a dashboard you're scrolling. Every panel reads like a bracket-framed HUD readout; numbers that matter — the live timer, a new PR — are lit and oversized, not quietly printed.

This is a deliberate escalation from the original Pi-grade Industrial system, which was already dark and data-dense but stayed visually quiet. Round one (an additive "Instrument Console" pass — one new color, one chamfered corner) was rejected as too safe. Redline Console keeps every hard rule from the original system (dark-only, gold-is-earned, blue-is-chrome, gym touch targets) and turns the surface language up: bracket-corner framing, glow, and motion are visible everywhere now, not just on one card.

**Decoration level:** Intentional (up from minimal). Bracket-corner HUD framing on every card, a blueprint-grid background with glowing intersection nodes, glow halos on interactive/live/achievement color, and purposeful one-shot motion (scan-sweep, radar-spin, flash-bloom). Still no gradients-as-decoration, no hero illustrations, no glassmorphism — the glow serves the HUD reading, it doesn't replace it.

**Layout:** Unchanged. Grid-disciplined. `max-width: 1100px` container, 2-panel dashboard (list + sidebar) on ≥768px, single column on mobile. Redline Console is a surface-language change, not a structural rebuild.

---

## Memorable Thing

> "This feels like a command console, not an app."

Not a screen you scroll — an instrument you're reading in real time. Every visual choice should reinforce that the system is actively watching: it scans on load, it sweeps a radar ring around your live timer, it flashes when it detects a new PR. The identity underneath — "real software a real person built and owns" — doesn't change; the surface now behaves like it's live.

---

## Design Principles

**1. Data over decoration.** Every visual element either communicates data or gets out of the way. Bracket framing, glow, and motion all exist to signal state (interactive, live, earned) — never as pure ornament.

**2. Gold is earned — and now it's an event.** The PR gold (`--pr: #f59e0b`) still appears exactly once per set that beats the user's personal record, and it is still Zenkai's primary identity color precisely because it's rare. What's new: the moment a PR lands, the row does a one-shot flash + glow-bloom (`flashbloom`, ~2.4s, fires once) before settling back to the quiet permanent badge. Never use gold, or the flash-bloom pattern, for anything that isn't a genuine PR.

**3. Live is a third meaning, not a repaint.** Phosphor cyan (`--live: #22e5c9`) means "running right now" — the active session timer (with its radar-sweep ring), the rest-timer ring, an in-progress streak. Blue is still plumbing. Gold is still achievement. Live is policed exactly as strictly as gold: if it's not currently active, it doesn't get cyan.

**4. Numbers are the hero, not the headline.** The live timer and PR values render 2–3× larger than body text, in JetBrains Mono, with a glow/text-shadow treatment. On a console, the reading is the point — the number should be the first thing you see on the screen, ahead of any heading.

**5. Gym-use touch targets.** Unchanged. Hands are sweaty, attention is split. Every primary action gets ≥56px. Steppers are 40×40px minimum (52px target). The Log Set button is the most-tapped element in the app.

**6. Dark by conviction.** Unchanged. No light mode — not an oversight, a position. Pi builders live in dark terminals and lift in dim gyms.

---

## Deliberate Risks (Where Zenkai Gets Its Own Face)

These are intentional departures from the fitness app category. They are policy, not accidents.

| Risk | What | Why | Cost / mitigation |
|------|------|-----|--------------------|
| **Bracket-corner HUD framing** | Every card gets a diagonal viewfinder bracket (top-left + bottom-right, 2px, low opacity at rest) that brightens and glows on hover or when live | Turns "cards on a dark background" into "panels on a console" — a structural signature, not a color swap | Busier than a clean border at a glance — kept thin and low-opacity by default so it recedes until you interact with or activate the panel |
| **Live gets a radar sweep** | The active-session timer sits inside a rotating conic-gradient ring (`spin`, 2.6s loop) instead of a static dot | A live thing should visibly move; a static dot doesn't read as "the system is currently reading you" | Continuous animation — must respect `prefers-reduced-motion` without exception |
| **PR arrival is an event** | A new PR triggers a one-shot flash + glow-bloom on the set row (fires once, ~2.4s, then settles to the normal badge) | Turns achievement into a real, felt moment instead of a quiet inline badge | Risk of feeling gimmicky if it fired often — mitigated because it can only ever fire on a genuine PR, once |
| **Numbers outrank headings** | PR values and the live timer render 2–3× larger than body/heading text, with a glow/text-shadow | On an instrument, the reading matters more than the label around it | Needs a firm responsive scale-down on narrow screens or it crowds the layout |
| **Blueprint grid + glowing nodes** | Background texture upgraded from a plain dot-grid to a fine hairline grid with faint glowing intersection nodes (radial-gradient, blue-tinted) | More visually present "instrument panel" feeling than a flat texture | Node glow must stay subtle enough to never sit under a text block — opacity capped low by design |
| **Gold as identity, not accent** *(carried over)* | `--pr` is the primary brand color, used only for PRs | Every fitness app uses blue or orange as their hero color; reserving gold for earned moments makes it genuinely meaningful | — |
| **No light mode** *(carried over)* | Dark-only, by design | An explicit position for Pi builders in dark environments | — |
| **Syne as display typeface** *(carried over)* | Geometric, slightly cold — unusual for fitness apps, and deliberately kept unchanged through both design revisions | Swapping it would trade an already-validated identity bet for novelty with no real gain | — |

**Safe choices (category baseline — play these straight):**
- Dark background + card elevation surfaces
- Monospace for numeric data (now expanded — see Typography)
- Blue for interactive chrome only — never data, never decoration
- Grid-disciplined 1100px layout, 8px spacing scale, gym touch targets — all untouched from Pi-grade Industrial

---

## Spacing

**Base unit:** 8px (unchanged)

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

**Approach:** Intentional (up from minimal-functional). Motion still earns its place — every new animation signals state (live, arrived, scanning), never decoration for its own sake.

| Animation | Duration | Trigger | Use |
|-----------|----------|---------|-----|
| Hover state transitions | 50–100ms | Hover | Bracket opacity/glow on `.hud` |
| Button press, badge appear | 150–250ms | User action | Unchanged from v1 |
| Page enter (fadeUp) | 250–400ms | Page load | Unchanged from v1, 8px translateY |
| **Scan-sweep** | ~2.2s, one-shot | Live card mounts / page load | A single light sweep across the active-session card — "the console just scanned this panel" |
| **Radar-spin** | 2.6s, continuous loop while live | Active session exists | Rotating conic-gradient ring around the live timer |
| **Flash-bloom** | ~2.4s, one-shot | A set is flagged `is_pr: true` | Row flashes gold and blooms outward, then settles to the permanent PR badge |

**Easing:** `ease-out` on enter, `ease-in` on exit, `ease-in-out` on positional moves, `linear` on the radar spin.

**Never:** scroll-driven animations, loading choreography, entrance animations on repeated elements (table rows, badge lists), any of the three new one-shot/loop animations firing on anything other than their specific trigger (a real live session, a real PR).

**Accessibility:** All four new animations (radar-spin, scan-sweep, flash-bloom, live-dot pulse) must be disabled under `prefers-reduced-motion: reduce` — state changes should still be visible (color, text) with the motion removed, never invisible.

---

## Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-05-30 | Aesthetic direction: Pi-grade Industrial | Competitive research (Hevy, Strong) showed the entire category looks like consumer apps. Zenkai's users chose self-hosting deliberately — the visual language should reinforce that identity. |
| 2026-05-30 | Gold (#f59e0b) named as primary identity color, not accent | Gold appears only on earned PR moments. Making it the identity color turns rarity into brand. Blue is plumbing. Gold is achievement. |
| 2026-05-30 | No light mode — documented as explicit position | Not an oversight. Pi builders use dark environments. Documenting this ends recurring discussion. |
| 2026-05-30 | Syne (geometric, slightly cold) retained as display font | Unusual in fitness apps. Signals technical software over lifestyle brand. Intentional departure from category convention. |
| 2026-09-09 | Evolved to Redline Console — bracket-corner HUD framing, `--live` cyan, radar-sweep + flash-bloom motion, oversized glowing hero numbers | User asked for a "futuristic" redesign via `/design-consultation`. First pass (additive, one new color + one chamfered corner) was explicitly rejected as too safe — see 2026-09-09 (rejected) below. This pass escalates decoration to "intentional" and motion to purposeful one-shot/loop animation while keeping every hard rule (dark-only, gold-earned, blue-chrome, touch targets, Syne/Barlow/JetBrains Mono families) unchanged. |
| 2026-09-09 | *(rejected)* Instrument Console — chamfered live-card corners, single `--live` color, mono-expanded labels, no other surface changes | Too incremental — read as "the app got a skin," not a genuine futuristic departure. Superseded same day by Redline Console above. |
| 2026-09-09 | Extended Redline Console (bracket framing, `--live` cyan) to `challenges.html`, `achievements.html`, `coach.html`, `plan.html`, `planner.html` | Those five templates defined their own bespoke card classes instead of the shared `.card`, so they'd silently missed the redesign. Challenges' "in progress" day-counter/bar and the AI coach's generating spinner also moved from `--accent` blue to `--live` cyan — both are genuinely running things, same rule as the session timer. |
| 2026-10-10 | Accessibility pass: `--muted` #5a6a82 → #72849e, dark text on blue buttons, no zoom lock, labelled icon buttons, 44px set actions, 56px Finish | axe-core (WCAG 2.1 AA) found 76 problems across 12 pages; the contrast table here claimed ~5.4:1 for `--muted` but it measured 3.6:1. User chose dark text on the same blue over a deeper blue with white text, to keep the Redline glow. |
| 2026-10-10 | Gold removed from everything that isn't a PR or an achievement | RPE 8 chip → peach, exercise colour palette and Abs muscle colour → non-gold, estimated 1RM and top-set text → `--text`, goal bar → `--muted-hi`, dashboard challenge card → `--live` (per 2026-09-09), best streak → `--success`, challenge partial/grace and plan "peak"/draft notices → orange `#fb923c` ("at risk", not "earned"). |
| 2026-09-09 | Reversed the in-flight "FitStorm" rename — Zenkai retained as the product name everywhere (docs, infra, deploy) | TODOS.md referenced a planned Zenkai→FitStorm rename that README/DESIGN.md/CHANGELOG had already adopted in prose, but the running app (nav, PWA manifest, localStorage keys) never actually shipped it. User decided to keep Zenkai and retire FitStorm for good rather than finish that rename. |

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
--muted:        #72849e   /* labels, timestamps, placeholders (≥4.5:1 on every surface but --surface-hover) */

/* Interactive (chrome only — never data, never decoration) */
--accent:       #4f9cf9   /* links, primary buttons, focus rings */
--accent-hover: #7ab8fc   /* hover state */
--accent-dim:   rgba(79,156,249,0.09)   /* hover backgrounds */
--accent-glow:  rgba(79,156,249,0.6)    /* bracket/halo glow on hover */

/* Live (active-only — session timer, rest ring, in-progress streak) */
--live:         #22e5c9
--live-dim:     rgba(34,229,201,0.12)
--live-glow:    rgba(34,229,201,0.65)   /* radar ring + live-dot glow */

/* Semantic */
--danger:       #f87171   /* errors, delete actions */
--success:      #34d399   /* success states, streak badge */
--pr:           #f59e0b   /* PRs, achievement identity color */
--pr-dim:       rgba(245,158,11,0.12)   /* PR highlight backgrounds */
--pr-glow:      rgba(245,158,11,0.65)   /* flash-bloom + hero-number glow */

/* Shadows & glow */
--shadow-sm:    0 1px 3px rgba(0,0,0,0.7), 0 0 0 1px rgba(255,255,255,0.04)
--shadow:       0 6px 20px rgba(0,0,0,0.6)
--glow:         0 0 0 3px rgba(79,156,249,0.22)   /* focus ring (accent) */
--glow-pr:      0 0 0 3px rgba(245,158,11,0.2)    /* focus ring (PR) */
```

**Rule:** Blue (`--accent`) = interactive chrome. Gold (`--pr`) = data/achievement. Cyan (`--live`) = currently active/running, nothing else. Never swap — a gold button, a blue PR value, or a cyan "static" label are all wrong.

---

## Typography

| Role | Font | Weights | Size |
|------|------|---------|------|
| Headings (h1/h2/h3) | Syne | 600, 700, 800 | varies |
| UI / prose | Barlow | 400, 500, 600 | 15px base |
| Numeric data + all structural labels | JetBrains Mono | 500, 600, 700 | inherits |
| **Hero numbers** (live timer, new-PR value) | JetBrains Mono | 700 | 2–3× base data size, with glow text-shadow |

Fonts are self-hosted as `.woff2` in `app/static/fonts/`. No Google Fonts CDN call at runtime — works fully offline.

**Heading style:** `letter-spacing: -0.02em` — tighter than default, gives the geometric Syne letters more presence.

**Label style:** `0.72rem`, `font-weight: 700`, `text-transform: uppercase`, `letter-spacing: 0.06em`, `color: var(--muted)`. Used for form labels and section headers.

**Section title style** (`.section-title`): `0.68rem`, `font-weight: 700`, `text-transform: uppercase`, `letter-spacing: 0.1em`, `color: var(--muted)`.

**`.num` class:** Apply to any weight (kg), rep count, volume, duration, or PR value — renders in JetBrains Mono.

**Hero number treatment:** Reserved for exactly two contexts — the active-session elapsed timer, and a PR value at the moment it's flagged. `font-size` 2–3× the surrounding data, `text-shadow: 0 0 10px var(--live-glow|--pr-glow), 0 0 30px <same, lower alpha>`. Must scale down on ≤480px so it never overflows its card.

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

### HUD cards (`.hud`, replaces plain `.card` styling)

```css
.hud {
  position: relative;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 1.25rem;
  box-shadow: var(--shadow-sm);
  --bracket: var(--accent);
  --bracket-op: .5;
}
.hud::before, .hud::after {
  content: ''; position: absolute; width: 16px; height: 16px;
  transition: opacity .2s;
}
.hud::before { top: -1px; left: -1px; border-top: 2px solid var(--bracket); border-left: 2px solid var(--bracket); opacity: var(--bracket-op); }
.hud::after  { bottom: -1px; right: -1px; border-bottom: 2px solid var(--bracket); border-right: 2px solid var(--bracket); opacity: var(--bracket-op); }
.hud:hover::before, .hud:hover::after { opacity: 1; filter: drop-shadow(0 0 4px var(--accent-glow)); }
.hud.is-live { --bracket: var(--live); --bracket-op: .85; }
```

Diagonal bracket corners only (top-left + bottom-right) — deliberately not all four, to keep the frame legible rather than busy. `.is-live` swaps the bracket color/opacity to cyan for the active-session card.

### Buttons

| Class | Background | Text | Use |
|-------|-----------|------|-----|
| `.btn-primary` | Blue gradient (#4f9cf9 → #3b82f6), `box-shadow: 0 0 16px rgba(79,156,249,.35)` | `#090b10` (near-black; white was 2.8:1) | Primary actions (Log Set); also selected RPE chips, the Plan mode toggle |
| `.btn-ghost` | Transparent | `--muted` | Secondary actions (Clear, Cancel) |
| `.btn-danger` | `--danger` | `#fff` | Destructive (Delete) |

Minimum height: `38px` (`.btn` base). Primary action buttons use inline `min-height: 56px` where the touch target spec requires it.

### Badges

| Class | Background | Text | Font | Use |
|-------|-----------|------|------|-----|
| `.badge-pr` | `--pr-dim`, `box-shadow: 0 0 10px rgba(245,158,11,.25)` | `--pr` | JetBrains Mono | Personal record |
| `.badge-live` | `--live-dim` | `--live` | JetBrains Mono | Live/active status, pulsing dot |
| `.badge-streak` | green-dim | `--success` | Barlow | Consecutive-day streak |

### Live radar timer

Wraps the active-session elapsed timer in a rotating conic-gradient ring (`animation: spin 2.6s linear infinite`), masked to a thin ring via `radial-gradient` mask. Timer text uses the hero-number treatment in `--live` with glow. Disabled under `prefers-reduced-motion`.

### PR flash-bloom

Applied once to a set row the instant the server reports `is_pr: true`: `animation: flashbloom 2.4s ease-out 1;` — background and box-shadow pulse gold outward, then settle. Never re-fires on the same set. Disabled under `prefers-reduced-motion` (the permanent gold badge still communicates the PR without motion).

### Form fields

Inputs, selects, textareas: `--bg` background, `--border` border, `border-radius: 8px`, `0.875rem` font size. Focus: `border-color: var(--accent)`, `box-shadow: var(--glow)`.

### Segmented toggle

```
[Generate] [Coach]
```

Two or more mutually exclusive options in one row — e.g. the challenge rule
editor's Tick/Photo kind selector (`app/templates/partials/rule_editor.html`).
Real `<button aria-pressed>` elements, not a styled `<div onclick>`, for
keyboard and screen-reader parity with every other control.

**`.seg-toggle`** (base.html) is the shared component: 44px tall, the
`--accent-dim`/`--accent` pill look for the pressed option, muted otherwise.
Use it anywhere a choice sits in a normal row (e.g. Generate | Coach on the
Plan page).

```html
<div class="seg-toggle" role="group" aria-label="Left column view">
  <button type="button" aria-pressed="true">Generate</button>
  <button type="button" aria-pressed="false">Coach</button>
</div>
```

**`.kind-toggle`** is the older compact variant used by the challenge rule
editor (`app/templates/partials/rule_editor.html`). It has no stylesheet
rule: the editor builds it in JS with inline styles at ~28px, which is below
the touch-target rule. It is kept only for that dense row; new code uses
`.seg-toggle`.

### Pill

`.pill` (base.html) is the interactive choice/filter pill: 40px tall, round,
`--muted-hi` text, `--accent` when `.active`. `.muscle-pill` on the
Exercises page extends it (the count span stays local). Prompt chips in the
Coach chat use it with a 44px minimum height on touch.

`.profile-pill` (Plan page "Your training profile") is a different thing:
a non-interactive stat chip. It does not use `.pill` and must not look
clickable.

### Sheet

`sheet.js` turns an element that already lives in the page into a modal
bottom sheet without moving it in the DOM (a focused `<textarea>` keeps its
focus and text). `Sheet.open(root, {label, initialFocus, returnFocus,
onClose})` returns `{close()}`.

- 62% of the visual viewport; while the keyboard is open it fills the visual
  viewport (`visualViewport` offsetTop and height), so the input stays above it.
- `role="dialog"`, `aria-modal="true"`; everything outside the sheet's own
  ancestor chain is `inert`; focus moves to `initialFocus` synchronously
  (iOS raises the keyboard only for a `focus()` inside the tap), is trapped
  with Tab, and returns on close.
- Dismiss with Escape, a tap on the dimmed page, or a 44px Close button the
  caller provides. No drag gesture in v1.
- The page is scroll-locked with `position: fixed` (not `overflow: hidden`,
  which does not lock iOS) and the scroll position is restored.
- Reduced motion: no slide. Top corners 8px.
- Overlays that open on top stay usable: `.undo-toast`, the achievement rack
  and `[data-sheet-keep]` are never made inert, and the sheet ignores Escape
  and Tab while `#confirm-sheet` is open.

### Action toast

`showActionToast(message, onUndo, opts)` shows "message · Undo" for ~9s. The
Undo button awaits `onUndo()`: resolving anything but `false` shows "Undone",
and `false` or a throw shows "Undo failed" (button re-enabled). `opts.id`
lets two kinds of toast coexist; the same id replaces the previous one.
`showUndoToast(token, label)` (deleted-item recovery) is a wrapper that keeps
its original behaviour: `POST /undo/{token}`, then reload.

### Stacking (z-index)

| z-index | Layer |
|---|---|
| 50 | top nav |
| 90 | chat composer (reserved) |
| 100 | mobile tab bar |
| 140 / 150 | More menu backdrop / More sheet |
| 349 / 350 | sheet dim / sheet |
| 399 / 400 | confirm overlay / confirm sheet |
| 450 | undo and action toasts |
| 9999 | achievement toasts |

`--composer-h` (default `0px`, set to the composer's height while it is
present) lifts the toasts, the achievement rack and the page's bottom
padding clear of the fixed composer.

### Coach chat (contract for the chat PRs)

Specified in `docs/designs/coach-chat.md`; components arrive with the chat.
The rules that keep it on-system:

- **Transcript:** `YOU` (`--muted`) or `COACH` (`--text`, never blue) in the
  uppercase mono label style above each message; a day-divider row per day;
  no entrance animation; `role="log"` with `aria-live="polite"`. All model
  text is rendered as text (`createElement` + `textContent`).
- **Change summary:** a `--surface-2` box under the coach line listing the
  server-computed diff in mono, removed items struck through in `--muted`.
- **EDITED label and changed rows:** muted label on `--surface-2` and the
  neutral `--surface-hover` row tint. Never blue (not chrome) and never gold
  (not a PR).
- **Confirm chip** ("Remember: ...? Yes / No"): `.pill` look, 44px on touch;
  the saved state uses `--success`.
- **Working indicator:** cyan `--live` dot and "working... Ns"
  (`aria-live="off"`); the dot pulse stops under reduced motion.
- **Mobile:** one 56px composer row above the tab bar
  (`bottom: calc(64px + env(safe-area-inset-bottom))`) that opens the sheet.
  **Desktop:** the 320px left column swaps between Generate and Coach with a
  `.seg-toggle`.
- **Row menu:** a 44x44 `⋯` button on every exercise row opens a small menu
  (z-index 120, between the tab bar and the More menu) with 48px items: "Swap
  exercise" (drafts only) and "Why this exercise?". Arrow keys move, Escape closes
  and returns focus to the button.
- **Swap list:** unfolds under the row inside the day card: a `SWAP X FOR` label
  with a 44px close, six 48px rows (name, then equipment · muscle in muted mono),
  skeleton rows while loading, "No alternatives found for this exercise." when
  empty. Picking one closes it, marks the day EDITED and shows "Swapped X for Y ·
  Undo".
- **Prompt chips:** `.pill` buttons at 44px, only while the conversation is empty.
- **Feedback chip:** like the note chip; "Replace 'Too hard' with 'Too easy' for
  this plan?" when it would overwrite a value.
- **Touch targets:** send, undo and delete 44px minimum; inputs 16px+ so iOS
  does not zoom.

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
| Set row delete (×) and edit (✎) | 44×44px | Icon stays 12–13px; the button box is `min-width/min-height: 44px` |
| Exercise search results | 48px per result | datalist — browser-controlled |

---

## Interaction States

| Screen | Loading | Empty | Error | Success |
|--------|---------|-------|-------|---------|
| Dashboard | — (server-rendered) | "No workouts yet. [Start Workout →]" — centered, `--text-dim`, `--accent` link | — | — |
| Log Set | button text → "Logging…" | — | "Failed — try again" inline below button | Set row appends, form retains last values; row flash-blooms if PR |
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

**PR badge + flash-bloom:** Shown immediately after `POST /sets` returns `{"is_pr": true}`. Gold (`--pr-dim` bg, `--pr` text, JetBrains Mono), appears inline on the set row, which also plays the one-shot `flashbloom` animation (disabled under reduced-motion). First set of any exercise always earns one.

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
- Announcements: one persistent `#sr-announcer` (`aria-live="polite"`) in `base.html`; call `window.announce(text)`. A logged set is announced ("Set logged: Bench Press, 60 kg × 5. Personal record!"), and so is every action toast. Inline errors (`#log-error`, `#cardio-error`) are `role="alert"`.
- PR badge: `role="img" aria-label="Personal record"` (a bare span can't carry `aria-label`)
- Icon-only buttons always get an `aria-label` naming the action ("Delete set", "Decrease weight"), not the glyph
- No zoom lock: the viewport allows pinch-zoom (WCAG 1.4.4). Focus zoom on iOS is prevented by every field being 16px, not by `maximum-scale`

**Autofocus:**
- Workout form: exercise search input gets `autofocus` on load
- After logging a set: focus returns to exercise search (ready for next set)

**Color contrast (WCAG):**

| Foreground | Background | Ratio | Grade |
|-----------|-----------|-------|-------|
| `--text` #e4eaf2 | `--bg` #090b10 | ~15:1 | AAA |
| `--muted` #72849e | `--bg` #090b10 | 5.2:1 (4.5:1 on `--surface-2`) | AA |
| `--accent` #4f9cf9 | `--bg` #090b10 | ~7.5:1 | AA |
| `--live` #22e5c9 | `--bg` #090b10 | ~11.8:1 | AAA |
| `--pr` #f59e0b | `--surface` #0f1219 | ~8.2:1 | AAA |
| `#090b10` | `--accent` #4f9cf9 → #3b82f6 | 7.0:1 → 5.4:1 | AA (button text; white was 2.8:1, which fails) |
| `#000` | `--pr` #f59e0b | ~10.5:1 | AAA |

**Motion:** `radar-spin`, `scan-sweep`, `flashbloom`, and the live-dot `pulse` are all disabled under `prefers-reduced-motion: reduce`. State (color, text, the permanent PR badge) must remain fully legible with every animation removed.

---

## Background Texture

The page body has a blueprint-grid texture: a fine hairline grid (`linear-gradient`, 32px repeat, ~3.5% white lines) layered under a sparser radial-gradient of faint blue-tinted glowing "nodes" (~96px repeat, low opacity). This replaces the original plain dot-grid — more visually present, matching the console read, but still capped low enough to never compete with foreground text. Do not apply to cards or surface elements (cards use the HUD bracket treatment instead).

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
| Log set | `POST /workouts/{id}/sets` | Prepend `div#set-{id}` to `#sets-container`; show PR badge + trigger flash-bloom for 3 s if `data.is_pr` |
| Delete set | `DELETE /workouts/{id}/sets/{sid}` | `document.getElementById('set-' + id)?.remove()` |
| Finish workout | `POST /workouts/{id}/finish` | Populate `#finish-modal` fields, set `display: flex` |
| Delete workout | `DELETE /workouts/{id}` | `window.location.href = '/workouts'` |
| Load routines | `GET /routines` | Build `<option>` list inside `#routine-select` |
| Load exercises | `GET /api/exercises` | Populate `<datalist>` + `#muscle-group-select` options |
| Save routine | `POST /routines` | Close modal, call `loadRoutines()` to refresh dropdown |
| Patch notes | `PATCH /workouts/{id}` | No DOM update; debounced autosave |

**Why fetch() not HTMX on the workout form:** The log-set response drives multiple DOM mutations simultaneously (append row, show/hide PR badge + flash-bloom, update volume total, reset form). HTMX's single-target swap model can't express that without `hx-swap-oob`, which would require the server to render partial fragments it doesn't currently own. The JS approach is 30 lines and keeps the server returning clean JSON.
