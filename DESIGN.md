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
  rest-text: "#ffffff"
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
- Contrast: ink on paper 16.7:1, pencil on paper 6.0:1 and on card 6.6:1, record amber on its tint 5.3:1, rest countdown white on ink 18.4:1, empty tick outline 3.3:1.

## Typography

Two families, both SIL OFL, self-hosted as woff2 in `app/static/fonts/` so the app works offline (no font CDN at runtime).

- **Archivo** (Omnibus-Type), variable weight 100–900 and width 62–125.
  - Titles: weight 700–800 at normal width, normal case. Never uppercase, never expanded.
  - Numbers: weight 800, width 72%, `font-variant-numeric: tabular-nums` (every digit the same width, so a changing weight never makes the layout jump). Use the `.num` class.
- **Atkinson Hyperlegible Next** (Braille Institute), weights 400/600/700. All interface and body text. Chosen for legibility at a glance mid-workout.

Scale (phone): active-set fields 1.6rem; logged-set numbers 1.55rem; screen titles 2rem; exercise names 1.2rem; body 1rem; labels and captions no smaller than 0.8125rem (13px).

- **Sentence case** for every title, section heading and button: "Body metrics", "Start session", "Log entry". Proper names keep their capitals ("Bench Press").
- **Numbers in the number face,** including reps next to a weight ("70 kg × 8") and chart axes (`window.CHART_AXIS_FONT`, Archivo 700 13px). Atkinson's slashed zero never shows in a figure.
- **Dates read "Sep 13"** (`human_date` in templates, `window.fmtShortDate` in chart scripts), "Today" for today, and "Sunday, Oct 11" as a page subtitle. Never "09-13" or a raw ISO date.

## Layout

- Phone first, at 390px wide. Single column with 16px side gutters. Desktop max-width 1100px, reusing the same components.
- Tables fit their card on a phone: secondary columns carry `.col-wide` and drop out below 560px, so a number is never cut in half at the card edge.
- Desktop nav and the phone tab bar list sections in the same order: Home, Sessions, Analytics, Plan (then Exercises and More).
- Bottom tab bar: Home, Sessions, Analytics, Plan, More.
- **Session screen:** one exercise at a time.
  - Header: "In session", a live line (elapsed · N sets · volume), a ⋯ button and the orange Finish button. The ⋯ opens a sheet with the weight unit, Save as routine and Delete session, so nothing destructive sits next to Finish.
  - Exercise strip: a sideways-scrolling row of chips for this session's exercises: logged ones with their set count and a tick, planned ones (routine, template or repeat) dashed with "0/3" when the routine sets a target, the current one filled in ink, and "+ Exercise" last. A tap switches exercise.
  - The current exercise card: name and muscles with a Change button, "Last time" (from before this session), the coach target and cue if any, and +2.5 kg / +1 rep chips before the first set. Then the logged sets and the next set.
  - Search, routine and muscle filters live in a "Choose exercise" sheet (Change, + Exercise, or the empty state's button). It opens partway with the list showing and focus on its title, so the keyboard stays down until the search box is tapped (focusing the box raised the iPhone keyboard at once, and with the keyboard up the sheet fills the screen). A name with no match offers to log it as a new exercise.
  - Set rows: set number, the set ("60 kg × 8") and a round tick (44px), each row at least 54px tall. Tap a logged set to fix its numbers or delete it; swipe left also deletes. Both ask first.
  - The next set is outlined in orange; its weight and reps are fields that show this session's last set of the exercise, else last time's, else blank (never a number carried over from another exercise). Tapping a number selects it, so typing replaces it. The round orange tick logs the set. Under it, always shown: "How hard was it?" (Easy, Medium, Hard, Max, stored as RPE 5/7/9/10 so the coach's effort signals still work; nobody knew what "RPE" meant) and a Note. Logged sets read "Hard effort".
  - After a set, when the session has a plan, a "Next: <exercise>" button moves to the next planned exercise not started yet.
  - The rest timer is pinned above the tab bar: countdown, −30, +30, Skip and a draining line. Fixed, so starting or stopping it never moves the sets; the page gets matching bottom padding, so it never covers them. A routine's prescribed rest sets its length.
  - Session notes, Cardio and Muscles worked are collapsed rows in one card under the exercise.

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
- **Finished session:** the same tap-to-edit set rows as a live session (Save, Cancel, Delete in the editor; swipe also deletes), no per-row icons. "Repeat this session" and the header's delete live here, not on each row of the session list.
- **Toggle:** every one-of-N choice (sort, chart metric, page tabs, units, Low/Medium/High) is the shared `.seg-toggle`: a grey track with the picked option as a white raised segment, real `<button aria-pressed>`, 44px tall; `.seg-fill` spans the row. Choices that carry a description (Plan goal, feedback type) are option cards instead.
- **Confirm sheet:** red confirm for anything destructive; `primary: true` (orange, ink text) for a confirm that destroys nothing, such as starting another session.
- **Achievement toasts:** at most two (best tier first), then one "+N more achievements unlocked" link; above the tab bar on a phone.
- **Bodyweight records:** a bodyweight exercise's record is its best set, ranked by added load, then hold, then reps, and read "BW × 12", "BW +10 kg × 5" or "BW · 1:30". Its weight is your bodyweight, so it never shows as a kg record, an est. 1RM, a weight goal or a weight chart.
- **Server setup** (API keys, env vars, model ids) shows only to the admin. Everyone else gets a plain note of what's off and what still works.
- **Set row:**
  - todo: pencil numbers
  - done: `done` fill and an ink tick
  - active: orange tint, orange outline, weight and reps as white fields
  - record: amber tint, amber numbers and tick, "Best ever" label, and one amber light sweep (0.9s) when ticked
- **Tick:** round. 2px `ring` outline when empty, filled ink (or amber for a record) with a white check when done. Accessible name "Complete set N".
- **Muscle map:** the body outline is mirrored about the centre line like every muscle shape (it used to be wider on the right, which made the colours look shifted sideways).
- **Number entry:** the weight and reps on the next set look like every other input: a white box with a `ring` outline, 56px tall (a primary tap target), numbers in the number face at 1.6rem (`inputmode="decimal"` / `"numeric"`, so a tap opens the phone's own number keypad); the field being edited gets an ink outline. They were borderless 2.6rem numbers until the user found them neither obviously editable nor in proportion (2026-10-11). A custom in-page pad was prototyped and dropped in favour of the native keypad (less code, works with every assistive tech).
- **Rest bar:** ink background, white countdown (green "Done!"), ghost buttons for −30, +30 and Skip, and a 3px orange draining line.
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
- No page is ever wider than the screen. A page that overflows sideways makes iOS zoom out to fit and zoom back in when a field is focused. Grid tracks are `minmax(0, 1fr)`, never a bare `1fr` that a long unbroken line can stretch; `html, body { overflow-x: clip }` is the backstop. `scripts/e2e/no_overflow.test.mjs` checks every main page with long text.
- Focus is always visible as a 2px ink outline.
- Keep the existing checks green: `scripts/e2e/a11y.mjs` (axe-core plus touch targets) and the offline, confirm-sheet, sheet-drag and no-overflow suites.

## Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-10-10 | Replaced Redline Console with Daylight | User: the old design was busy/gimmicky, hard to use mid-workout and generic. Researched Strong, Hevy and Everlift. First direction (Chalk & Iron: dark minimal, single staged set) rejected as too flat, with the serif and flow wrong. Second (Training Floor: dark graphite and teal) rejected as too dark after two rounds of adjustments. Daylight chosen from three moods shown side by side. |
| 2026-10-10 | Light mode, replacing "dark-only" | The user's explicit choice after seeing dark and light side by side. |
| 2026-10-10 | Archivo + Atkinson Hyperlegible Next, self-hosted | OFL and verified from the font files (tabular figures present). Fontshare Satoshi and Cabinet Grotesk were rejected because their licence forbids self-hosting. |
| 2026-10-10 | Set list with tap-to-type numbers and an inline rest timer | The user's adjustments: no −/+ steppers, round ticks, rest timer under the last set, softer done state. |
| 2026-10-10 | Native number keypad instead of a custom pad; activity heatmap in ink | Built the rebuild: the native keypad is the same tap-to-type flow with less to break. Activity is data (ink); amber stays for records. |
| 2026-10-11 | Session screen reworked: exercise strip, picker sheet, pinned rest timer | The user found the screen cluttered and switching exercises slow. Chose "focused, cleaned up" over a Strong/Hevy-style list of every exercise. Filters moved into the picker, delete and units into a ⋯ menu, the duplicate "This session" list and the segmented progress bar removed. The rest timer moved from inline to pinned so it stops shifting the sets. Its countdown is now white: orange text broke the no-orange-text rule. |
| 2026-10-11 | Effort instead of RPE, always shown; symmetric muscle-map body | The user: keep effort and notes open, nobody knows what RPE is, and the muscle overlay looked shifted sideways. |
| 2026-10-11 | Weight/reps as visible fields; picker opens without the keyboard | From the user's iPhone test: the borderless 2.6rem numbers didn't read as fields and were too big, and the exercise picker opened full screen because focusing its search box raised the keyboard. |
| 2026-10-11 | Audit follow-ups, the user's call on all five | One toggle style (the existing `.seg-toggle`); repeat/delete moved off session rows; bodyweight records are best sets; the Plan page hides the model id and shows setup to the admin only; achievement toasts capped at two. |
| 2026-10-11 | Design audit consistency pass | A full audit at 390px and 1280px found drift, not new direction: Title Case next to sentence case, reps and chart axes in the body face, "09-13" dates, amber on every row of the records table, two buttons back into a live session, pencil and × icons on every finished set, tables cutting numbers off on a phone, the heatmap opening on last year. Each was brought back to the rules above. |

The previous system (Redline Console) is preserved in `docs/design-history/DESIGN-redline-console.md`.
