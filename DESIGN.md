---
# gstack: design-md-format=spec
name: Zenkai
description: A light, crisp training log where the next set is always big and one tap away; off-white and black with one signal orange.
colors:
  paper: "#f4f4f1"
  card: "#ffffff"
  ink: "#141414"
  text-muted: "#5d5d58"
  line: "#dddcd6"
  ring: "#8f8e88"
  primary: "#ff5a1f"
  on-primary: "#141414"
  primary-edge: "#e04a12"
  primary-tint: "#fff4ee"
  done: "#f7f7f4"
  record: "#8c5a00"
  record-tint: "#fff3d6"
  error: "#c8372d"
  success: "#1f7a4d"
  rest-surface: "#141414"
  rest-text: "#ff7a47"
typography:
  display:
    fontFamily: Archivo
    fontWeight: 800
    fontSize: clamp(1.7rem, 6vw, 2rem)
    letterSpacing: -0.01em
  title:
    fontFamily: Archivo
    fontWeight: 700
    fontSize: 1.2rem
    letterSpacing: -0.005em
  number:
    fontFamily: Archivo
    fontWeight: 800
    fontStretch: 72%
    fontFeature: tnum
  body:
    fontFamily: Atkinson Hyperlegible Next
    fontSize: 1rem
    lineHeight: 1.5
  label:
    fontFamily: Atkinson Hyperlegible Next
    fontWeight: 600
    fontSize: 0.8125rem
    letterSpacing: 0.02em
rounded:
  sm: 10px
  md: 14px
  lg: 18px
  full: 9999px
spacing:
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 32px
  2xl: 48px
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.lg}"
    minHeight: 56px
  button-secondary:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
    borderColor: "#c9c8c2"
    rounded: "{rounded.lg}"
    minHeight: 56px
  input:
    backgroundColor: "{colors.card}"
    borderColor: "{colors.ring}"
    rounded: "{rounded.md}"
    height: 52px
  card:
    backgroundColor: "{colors.card}"
    rounded: "{rounded.lg}"
  set-row-done:
    backgroundColor: "{colors.done}"
  set-row-active:
    backgroundColor: "{colors.primary-tint}"
    borderColor: "{colors.primary-edge}"
  set-row-record:
    backgroundColor: "{colors.record-tint}"
    textColor: "{colors.record}"
  rest-row:
    backgroundColor: "{colors.rest-surface}"
    textColor: "{colors.rest-text}"
  nav-link:
    textColor: "{colors.text-muted}"
---

# Zenkai

## Overview

**Creative North Star:** Daylight. A crisp, light training log in which the set you are about to do is always the biggest thing on the screen and one tap away. Chosen on 2026-10-10 because it answers the brief "the fastest, calmest way to log a lift" and reads well under bright gym lighting.
**Product context:** Self-hosted strength-training log (FastAPI + Jinja/HTMX PWA on a Raspberry Pi) for one person and a few friends, used mostly on iPhones mid-workout, one-handed. Peers: Strong and Hevy (light iOS defaults, spreadsheet-like set tables) and newer one-thumb loggers like Everlift.
**Mode per surface:** Session = Operate (the most-used screen; speed above everything). Home, Progress, Plan = Read/Operate. Auth = Operate.
**Reference sites:** https://www.strong.app, https://www.hevyapp.com, https://apps.apple.com/us/app/everlift-gym-workout-log/id6760033237
**Key characteristics:**
- Off-white paper and black ink, so it is readable in a bright gym.
- One signal orange for the thing to press, and nothing else orange.
- Big condensed numbers for weight and reps; nothing on screen smaller than about 13px.
- Amber appears only when you set a record.

## Colors

**Strategy:** Restrained. Neutrals (paper, card, ink, pencil) carry the page; one accent (signal orange) marks the primary action; amber is reserved for records.
**Light or dark:** Light. The user explicitly chose Daylight over two dark directions on 2026-10-10, replacing the earlier documented dark-only position. A dark variant is not part of this system; add it only as a deliberate, separately approved decision.

- **Orange means press.** The main button on each screen, the active set's outline, session progress. Orange is always a fill with ink text on it (5.9:1) or an outline in `primary-edge` (3.7:1). Orange text is never used: it is 3.1:1 on white.
- **Ink means done and data.** Completed-set ticks, chart lines and bars, the weekly goal. A single-series chart is ink; today's bar may be orange.
- **Amber means a record.** A new personal best, an achievement, the newest record in a list. Never a button, a chart series or decoration.
- **Red means an error or delete; green means saved.** Both are used for text and status only, always paired with words.
- **Categorical charts (muscles):** each muscle keeps one color whatever its rank. The palette must be re-validated on `card` (#ffffff) with the dataviz checks before shipping; the dark-surface muscle palette from Redline Console does not carry over.
- Contrast: ink on paper 16.7:1, pencil on paper 6.0:1 and on card 6.6:1, record amber on its tint 5.3:1, rest orange on ink 7.1:1, empty tick outline 3.3:1.

## Typography

Two families, both SIL OFL, self-hosted as woff2 in `app/static/fonts/` so the app works offline (no font CDN at runtime).

- **Archivo** (Omnibus-Type), variable weight 100–900 and width 62–125.
  - Titles: weight 700–800 at normal width, normal case. Never uppercase, never expanded.
  - Numbers: weight 800, width 72%, `font-variant-numeric: tabular-nums` (every digit the same width, so a changing weight never makes the layout jump). Use the `.num` class.
- **Atkinson Hyperlegible Next** (Braille Institute), weights 400/600/700. All interface and body text. Chosen for legibility at a glance mid-workout.

Scale (phone): active-set numbers 2.6rem; logged-set numbers 1.55rem; screen titles 2rem; exercise names 1.2rem; body 1rem; labels and captions no smaller than 0.8125rem (13px).

## Layout

- Phone first, at 390px wide. Single column with 16px side gutters. Desktop max-width 1100px, reusing the same components.
- Bottom tab bar: Home, Sessions, Progress, Plan.
- **Session screen:** a header with the session name, "N of M sets" and an orange Finish button, plus a segmented progress bar. Exercises are stacked; finished ones collapse to one line with a summary. The current exercise shows "Last time" and a set list.
  - Set rows: set number, kg, reps and a round tick (44px; 50px on the active set), each row at least 54px tall.
  - Upcoming sets are pre-filled from last time.
  - The next set is outlined in orange and shows big numbers, prefilled from the last set. Tapping a number opens the phone's number keypad; the round orange tick logs the set and moves to the next. RPE and set notes sit behind a one-line "RPE and set notes" disclosure.
  - The rest timer appears inline, directly under the set just ticked: countdown, −30, +30, Skip, and a draining line. It never covers the screen or dims it.

## Elevation & Depth

- Cards: `0 1px 2px rgba(20,20,20,.06), 0 4px 14px rgba(20,20,20,.06)` on paper.
- The active set uses a tinted fill and an orange outline rather than a shadow.
- The number pad sheet uses `0 -8px 24px rgba(20,20,20,.12)`.
- No glows, no zero-offset colored halos, no textured backgrounds.

## Shapes

Radii: sm 10px (small buttons, keys), md 14px (inputs, messages), lg 18px (cards, primary buttons), full for ticks. A nested element's radius equals the outer radius minus the gap.

## Components

- **Primary button:** orange fill, ink text, min height 56px (68–72px for "Start session"). Hover darkens the fill slightly; focus shows a 2px ink outline with a 2px offset; disabled is 50% opacity with no hover.
- **Secondary button:** white with a #c9c8c2 border and ink text. **Danger:** the secondary style with red text.
- **Set row:**
  - todo: pencil numbers
  - done: `done` fill and an ink tick
  - active: orange tint, orange outline, big numbers as buttons
  - record: amber tint, amber numbers and tick, "Best ever" label, and one amber light sweep (0.9s) when ticked
- **Tick:** round. 2px `ring` outline when empty, filled ink (or amber for a record) with a white check when done. Accessible name "Complete set N".
- **Number entry:** the weight and reps on the next set are big number fields (`inputmode="decimal"` / `"numeric"`), so a tap opens the phone's own number keypad; the field being edited shows a 2px ink outline. A custom in-page pad was prototyped and dropped in favour of the native keypad (less code, works with every assistive tech).
- **Rest row:** ink background, orange countdown, ghost buttons for −30, +30 and Skip, and a 3px orange draining line.
- **Messages:** a white card with a bold lead word in the status color, for example "No connection." in red or "Saved." in green.
- Every component has defined empty, loading and error states. Icon-only buttons have an `aria-label`. Errors use `role="alert"`, and logged sets are announced through `#sr-announcer`.

## Do's and Don'ts

- Do keep the next set the biggest thing on the session screen.
- Do use orange only for the primary action, the active-set outline and session progress.
- Do pre-fill sets from the last session and keep every touch target at 44px or more (primary actions 56px or more).
- Do validate any new chart palette with the dataviz checks on `card`.
- Don't put text in orange; don't use amber for anything but records.
- Don't bring back Redline Console devices: bracket corners, glows, blueprint grid, scan sweeps, uppercase expanded headings.
- Don't nest cards or dim the screen during rest.
- Don't use text smaller than 13px, or color alone to convey state.

## Motion

- **Approach:** minimal-functional.
- **Easing:** enter ease-out, exit ease-in, move ease-in-out.
- **Duration:** micro 100–180ms (row fill on tick); short 200ms (sheet in/out); the record sweep 900ms, once.
- **The one authored moment:** the amber sweep across a record row when it is ticked.
- `prefers-reduced-motion`: no sweep and no row animation; state still shows through color, tick and label.

## Accessibility

- WCAG 2.1 AA: text at least 4.5:1, UI outlines at least 3:1 (figures in Colors).
- Pinch-zoom allowed, and every input is at least 16px (iOS focus zoom).
- Focus is always visible as a 2px ink outline.
- Keep the existing checks green: `scripts/e2e/a11y.mjs` (axe-core plus touch targets) and the offline, confirm-sheet and sheet-drag suites.

## Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-10-10 | Replaced Redline Console with Daylight | User: the old design was busy/gimmicky, hard to use mid-workout and generic. Researched Strong, Hevy and Everlift. First direction (Chalk & Iron: dark minimal, single staged set) rejected as too flat, with the serif and flow wrong. Second (Training Floor: dark graphite and teal) rejected as too dark after two rounds of adjustments. Daylight chosen from three moods shown side by side. |
| 2026-10-10 | Light mode, replacing "dark-only" | The user's explicit choice after seeing dark and light side by side. |
| 2026-10-10 | Archivo + Atkinson Hyperlegible Next, self-hosted | OFL and verified from the font files (tabular figures present). Fontshare Satoshi and Cabinet Grotesk were rejected because their licence forbids self-hosting. |
| 2026-10-10 | Set list with tap-to-type numbers and an inline rest timer | The user's adjustments: no −/+ steppers, round ticks, rest timer under the last set, softer done state. |
| 2026-10-10 | Native number keypad instead of a custom pad; activity heatmap in ink | Built the rebuild: the native keypad is the same tap-to-type flow with less to break. Activity is data (ink); amber stays for records. |

The previous system (Redline Console) is preserved in `docs/design-history/DESIGN-redline-console.md`.
