---
# gstack: design-md-format=spec
name: Zenkai
description: A training log drawn like an engineering record: drafting paper, graphite ink, one trace blue for data, a highlighter for what matters now.
colors:
  paper: "#eef1ec"
  grid: "#dde4db"
  hairline: "#d3dbd1"
  ink: "#23262b"
  pencil: "#5b615b"
  ring: "#8a908a"
  trace: "#1e5bb8"
  datum: "#b23a26"
  highlighter: "#f5d547"
  highlighter-tint: "#f6e7a1"
  record: "#8c5a00"
  field: "#ffffff"
  error: "#c8372d"
  success: "#1f7a4d"
typography:
  family: Instrument Sans
  label: { size: 13px, weight: 500, color: pencil }
  body: { size: 15px, weight: 400 }
  head: { size: 18px, weight: 600 }
  data: { size: 22px, weight: 600, fontFeature: tnum }
  display: { size: 30px, weight: 600, letterSpacing: -0.02em, desktop: 40px }
  field-text: { size: 16px, note: "typed text only; iOS zooms on focus below 16px" }
rounded:
  all: 4px
  tick: 9999px
spacing:
  step: 8px
  section: 40px
  heading-to-content: 16px
  page-edge-phone: 24px
components:
  box: { border: "1.5px solid ink", background: paper, rounded: 4px, shadow: none }
  button-primary: { backgroundColor: ink, textColor: paper, minHeight: 56px }
  button-secondary: { backgroundColor: paper, textColor: ink, border: "1.5px solid ink", minHeight: 44px }
  input: { backgroundColor: field, border: "ink", minHeight: 48px }
  toggle: { border: "1.5px solid ink", selected: "ink fill, paper text", minHeight: 44px }
  set-row-active: { backgroundColor: highlighter-tint, border: "1.5px solid ink" }
---

# Zenkai

## Overview

**Creative north star:** a training log drawn like an engineering record. Drafting paper with a fine grid, graphite ink, boxed tables, one blue trace for anything plotted, and a yellow highlighter for the one thing that matters right now. Chosen on 2026-10-31 after three type directions and two refinements (see Decisions Log).

**What Home says first:** what changed in your strength, then what to do today, then the week. Everything else (records, volume, activity, muscles) is one tap away on Analytics.

**Mode per surface:** Session = operate (speed first). Home, Analytics, Plan = read. Auth = operate.

## Type

One family, **Instrument Sans** (SIL OFL, self-hosted in `app/static/fonts/`), numbers included, with tabular figures (`.num`) so a changing weight never moves the layout.

Five sizes, one job each. Nothing else.

| Token | Size | Job |
|---|---|---|
| `--t-label` | 13px | Units (kg, reps, h), column heads, dates, meta lines, captions, the tab bar, chart text |
| `--t-body` | 15px | Every row of text: names, plan rows, buttons, chips, toggles, notes |
| `--t-head` | 18px | Section titles, the session name, the one primary button on a screen |
| `--t-data` | 22px | The number you came to read: set weight and reps, plan targets, readouts |
| `--t-display` | 30px | One per screen, read at arm's length: Home's headline, the exercise name, the rest countdown. 40px for the desktop headline |

- **Exception:** text typed into a field is 16px (iOS zooms the page on focus below 16px).
- **Units are labels:** "77.5" at 22px, "kg" at 13px beside it.
- **Weights:** 400 for body, 600 for headings and numbers. Nothing heavier.
- **Sentence case** everywhere. Say "session", not "workout". Dates read "Sat 31 Oct", "Sep 13" in charts, "Today" for today.
- `scripts/e2e/ui_usability.test.mjs` check E11 fails if Home or the session screen uses any other size.

## Color

- **Graphite ink** for text, outlines and the one primary button per screen (ink fill, paper text).
- **Pencil** for labels and secondary text: 5.6:1 on paper.
- **Trace blue** for plotted data: trend lines, bars, the heatmap, "+2.5" progress notes. Never a button.
- **Highlighter yellow** for what matters now: today in the week strip, the set you are on, the lede's key phrase, the newest record. A fill behind ink text, never text itself.
- **Datum red** for targets and limits on a plot (dotted line), and the live-session dot.
- **Record amber** text only on a record's own badge.
- **Error red / success green:** text and status only, always with words.
- No orange. No shadows on boxes. No gradients except the highlighter's half-height underline.

## Layout and spacing

- An 8px step for everything. 40px between sections, 16px from a heading to its content, 24px page edge on a phone, rows at least 60px tall.
- Phone first at 390px. Desktop: max width 1120px, two columns on Home (story left, Today and week right).
- **Boxes:** a 1.5px ink outline on paper, 4px corners. Rows inside a box are split by hairlines. Do not nest boxes.
- The page background is the drafting grid (13px). Boxes cover it with paper.
- Nothing scrolls sideways inside a box, down to 320px (check E8). The live session's exercise strip is the one deliberate exception.

## Home

1. **Top line:** the date, and Oura readiness when connected ("Waiting for today's Oura sync." / "Reconnect Oura in Settings").
2. **Story:** the lift whose estimated 1RM rose most over 12 weeks (at least 4 sessions), as a sentence ("Bench is up 12 kg since August."), then the projection to the next round target when the trend is rising ("At this rate you reach 100 kg in mid-February."). Underneath, the plot: trace line, today's point in highlighter, dashed projection, dotted red target. Logic: `app/utils/home_story.py`; plot: `charts.generate_progress_plot`. With no trend, the page says so plainly and shows less.
3. **Today:** the next day of the newest saved plan (the day trained longest ago), its first two lifts as "3 × 5 at 77.5 kg" with any "+2.5" weight suggestion, the rest as "+ N more". One button, one tap: "Start Push A" starts the session with that routine. With no plan: "Start session". With a session open: "Resume session".
4. **This week:** Monday to Sunday as squares (filled = trained, today highlighted), the count against the weekly goal, last week's one-line recap on Monday to Wednesday, and the daily-log nudge when today's log is empty.
5. A ruled link to Analytics: records, volume and activity live there.

## Session screen

- **Header:** session name, the live line (red dot · elapsed · sets · volume), ⋯, Finish.
- **Exercise strip:** chips. Done ones ticked, the current one filled ink, planned ones dashed.
- **The exercise box:** name (display), muscles, last time, then the set table. Logged sets are rows with a filled round tick. The set you are on is highlighter yellow with an ink outline, two white 56px fields and the 56px log tick. Then effort (Easy, Medium, Hard, Max: the boxed toggle) and a note.
- **The rest timer** is pinned above the tab bar: ink, a white countdown at display size, −30 / +30 / Skip, and a yellow line that shrinks as time runs down.

## Components

- **Primary button:** ink fill, paper text, at least 56px tall. One per screen.
- **Secondary button:** paper with an ink outline, at least 44px tall.
- **Toggle (`.seg-toggle`):** an ink-outlined row of real `<button aria-pressed>`. The picked option is an ink fill with paper text.
- **Fields:** white with an ink outline. The field being edited gets a 3px trace-blue focus outline.
- **Tick:** round. Empty is a ring outline; done is filled ink with a white check; a record gets the highlighter.
- **Focus:** always visible, 2px ink outline with a 2px offset.

## Accessibility

- WCAG 2.1 AA. Text at least 4.5:1 (check E5), UI outlines at least 3:1. Touch targets at least 44px, primary actions at least 56px (E3). No text under 13px (E4).
- Pinch-zoom allowed. Reflow at 320px (E8). Reduced motion respected (E9).
- Keep `scripts/e2e/` green: `ui_usability`, `a11y`, `no_overflow`, `js_errors`, `confirm_sheet`, `sheet_drag`, `journal_zero`, `offline`.

## Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-10-31 | Replaced Daylight with the drafting style (Instrument Sans) | The user asked for a distinct design. Rejected in turn: a tidied Daylight ("too basic"), a today-first Daylight, then three new directions: Loaded bar (plates), Test rig (engineering plot) and Platform (meet board). Picked Test rig, then rejected its font (Schibsted), uneven sizes and density. Of three type options (Barlow, Instrument Sans, Bricolage), picked Instrument Sans; a refined variant (hairlines, dots, one number per row) was rejected in favour of the original boxed version, with more spacing. |
| 2026-10-31 | Five type sizes, one job each | The user: sizes were "all over the place with no distinct reason". 13 / 15 / 18 / 22 / 30, checked in the browser suite. |
| 2026-10-31 | Home leads with the strength story; records, volume and activity move to Analytics | Less on Home. The activity heatmap moved to Analytics with them. |
| 2026-10-31 | One tap from Home into a session | Home starts the next plan day directly (was 2 taps via Sessions). |
| 2026-10-31 | No orange; primary is ink | Orange belonged to Daylight. Ink fill reads as the thing to press on drafting paper, and leaves color for data (blue) and now (yellow). |

Earlier systems: `docs/design-history/DESIGN-daylight.md`, `docs/design-history/DESIGN-redline-console.md`.
