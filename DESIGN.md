# FitTrack Design System

## Color Tokens
| Token | Value | Usage |
|---|---|---|
| --bg | #0d0f14 | Page background |
| --surface | #141720 | Cards, inputs |
| --surface-2 | #1a1e27 | Elevated surfaces |
| --border | #252b3b | Dividers, borders |
| --text | #e2e8f0 | Primary text |
| --muted | #64748b | Labels, timestamps |
| --accent | #4f9cf9 | Interactive (links, buttons, focus) |
| --accent-hover | #7ab8fc | Hover state for interactive |
| --accent-dim | rgba(79,156,249,0.08) | Hover backgrounds |
| --danger | #f87171 | Errors, delete actions |
| --success | #34d399 | Success states |
| --pr | #f59e0b | PRs, achievements, identity color |
| --pr-dim | rgba(245,158,11,0.12) | PR highlight backgrounds |

**Rule:** Blue = interactive chrome. Gold = data/achievement semantic. Never swap.

## Typography
- Headings (h1/h2/h3): Syne (Google Fonts), 600/700/800 — distinctive geometric, letter-spacing: -0.02em
- UI/prose: Barlow (Google Fonts), 400/500/600
- Numeric data: JetBrains Mono (Google Fonts), 500/600/700
- All weight values (kg), reps, volume, PR values → JetBrains Mono
- Labels: uppercase + letter-spacing: 0.06em + font-weight: 700
- Fallback: system-ui, sans-serif / monospace (for offline self-hosting)

## Spacing Scale
4px base. Use multiples: 4, 8, 12, 16, 20, 24, 32, 48

## Icons
Lucide v0.378.0 via CDN. Pinned — do not use @latest.
Standard size: nav 18px, cards 20px, inline 16px.
All icons: stroke-width 2, stroke: currentColor.
Re-initialize after HTMX swaps: listen on htmx:afterSwap.

## Components
- .card: --surface bg, 1px --border, border-radius 10px, box-shadow --shadow-sm
- .btn-primary: blue gradient (not gold — gold is for data display, not actions)
- .badge-pr: --pr-dim bg, --pr text, JetBrains Mono font
- .stepper-group: flex row, stepper-btn on sides, number input center
