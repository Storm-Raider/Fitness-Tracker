# Daylight chart colours (DESIGN.md). These SVGs are built server-side, so they
# use the base.html token values as literals: --data ink for a single series,
# --primary orange for today's bar (a mark, never text), --muted for axis text,
# --surface-hover for empty tracks/stubs.
DATA_COLOR = "#1e5bb8"      # --data (trace blue)
TODAY_COLOR = "#23262b"     # ink: today's bar only
AXIS_TEXT = "#5b615b"       # --muted: 5.6:1 on paper
LABEL_TEXT = "#23262b"      # --text
TRACK_COLOR = "#dde4db"     # --grid: empty track / zero-day stub
_UI_FONT = "Instrument Sans,system-ui,sans-serif"
_NUM_FONT = "Archivo,system-ui,sans-serif"

# One colour per muscle, used wherever muscles are charted (dashboard bars, the
# muscle map on analytics and the workout page via the MUSCLE_COLORS template
# global), so a muscle keeps its colour whatever its rank. Re-derived for the
# white card (#ffffff) with the dataviz validator; it avoids the reserved roles
# (signal orange #ff5a1f, record amber #8c5a00, ink #141414): each hue sits
# OKLab ΔE >= 16 from them. Hues were searched one per hue family and assigned
# so muscles that touch on the body map (and neighbours in this dict order)
# stay furthest apart.
#
#   validate_palette.js "<these 8, in this order>" --mode light --surface "#ffffff"
#     Lightness band PASS (all inside L 0.43-0.77) · Chroma floor PASS
#     CVD separation PASS (worst adjacent Forearms/Abs ΔE 10.3 protan)
#     Normal-vision floor PASS (worst adjacent Triceps/Forearms ΔE 20.8)
#     Contrast vs surface PASS (all 8 >= 3:1 on #ffffff)
#   --pairs all: CVD PASS (worst ΔE 9.5), normal-vision FAIL Chest/Triceps
#     ΔE 12.2 (eight hues can't all clear 15 pairwise; those two never touch on
#     the map). Every muscle chart names its muscles, so colour is a secondary cue.
MUSCLE_COLORS = {
    "Chest":     "#159cb7",  # cyan
    "Shoulders": "#aa71fc",  # lavender
    "Biceps":    "#829821",  # olive green
    "Triceps":   "#2773c0",  # blue
    "Forearms":  "#853275",  # plum
    "Abs":       "#fb0086",  # magenta
    "Back":      "#068665",  # teal green
    "Legs":      "#5e07dc",  # violet
}
_OTHER_MUSCLE = "#8f8e88"  # --ring neutral grey, 3.3:1 on white


def generate_weekly_bar_chart(
    day_volumes: list[tuple[str, float]],
    color: str = DATA_COLOR,
) -> str:
    """Return an inline SVG bar chart for 7-day volume. day_volumes is [(date_str, volume_kg), ...].

    Bars are ink; today's bar is signal orange. Day letters are muted text,
    today's in bold ink (orange is never text). Sizes are viewBox units: the
    chart renders ~320px wide on a phone, so 18 units is ~13.7px.
    """
    from datetime import date as _date

    if not day_volumes:
        return ""

    W, H = 420, 108
    pad_l, pad_r, pad_t, pad_b = 6, 6, 6, 30
    n = len(day_volumes)
    gap = 8
    bar_w = (W - pad_l - pad_r - gap * (n - 1)) / n
    chart_h = H - pad_t - pad_b

    max_vol = max((v for _, v in day_volumes), default=0) or 1
    today_str = _date.today().isoformat()
    day_chars = "MTWTFSS"

    parts = [
        f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%;height:auto;display:block;">'
    ]

    for i, (d, vol) in enumerate(day_volumes):
        x = pad_l + i * (bar_w + gap)
        cx = x + bar_w / 2
        is_today = d == today_str
        weekday = _date.fromisoformat(d).weekday()
        label = day_chars[weekday]

        if vol > 0:
            bar_h = max(4.0, (vol / max_vol) * chart_h)
            bar_y = pad_t + chart_h - bar_h
            fill = TODAY_COLOR if is_today else color
            vol_tip = f"{vol:,.0f} kg"
            parts.append(
                f'<rect x="{x:.1f}" y="{bar_y:.1f}" width="{bar_w:.1f}" '
                f'height="{bar_h:.1f}" rx="4" fill="{fill}">'
                f'<title>{vol_tip}</title></rect>'
            )
        else:
            stub_y = pad_t + chart_h - 3
            parts.append(
                f'<rect x="{x:.1f}" y="{stub_y:.1f}" width="{bar_w:.1f}" '
                f'height="3" rx="1.5" fill="{TRACK_COLOR}"/>'
            )

        day_fill = LABEL_TEXT if is_today else AXIS_TEXT
        day_fw = "700" if is_today else "600"
        parts.append(
            f'<text x="{cx:.1f}" y="{H - 5}" text-anchor="middle" font-size="18" '
            f'font-family="{_UI_FONT}" fill="{day_fill}" font-weight="{day_fw}">{label}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


def generate_muscle_bars(muscle_volumes: list[tuple[str, float]]) -> str:
    """Return an inline SVG horizontal bar chart for muscle group volume breakdown.

    Each bar wears its muscle's MUSCLE_COLORS hue and is named in ink text;
    the percentage is muted text. Font sizes are viewBox units (18 units is
    ~13.7px when the chart is ~320px wide on a phone).
    """
    if not muscle_volumes:
        return ""

    W      = 420
    ROW_H  = 28
    GAP    = 10
    BAR_H  = 16
    PAD_L  = 108
    PAD_R  = 60
    n      = len(muscle_volumes)
    H      = n * (ROW_H + GAP) - GAP
    bar_W  = W - PAD_L - PAD_R
    max_v  = max(v for _, v in muscle_volumes) or 1
    total  = sum(v for _, v in muscle_volumes) or 1

    parts = [
        f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%;height:auto;display:block;">'
    ]

    for i, (muscle, vol) in enumerate(muscle_volumes):
        cy   = i * (ROW_H + GAP) + ROW_H / 2
        bar_y = cy - BAR_H / 2
        fill_w = max(4.0, (vol / max_v) * bar_W)
        pct   = round(vol / total * 100)
        color = MUSCLE_COLORS.get(muscle, _OTHER_MUSCLE)

        parts.append(
            f'<text x="{PAD_L - 10}" y="{cy:.1f}" font-size="18" font-weight="600" '
            f'fill="{LABEL_TEXT}" font-family="{_UI_FONT}" text-anchor="end" '
            f'dominant-baseline="middle">{muscle}</text>'
        )
        parts.append(
            f'<rect x="{PAD_L}" y="{bar_y:.1f}" width="{bar_W}" height="{BAR_H}" '
            f'rx="4" fill="{TRACK_COLOR}"/>'
        )
        parts.append(
            f'<rect x="{PAD_L}" y="{bar_y:.1f}" width="{fill_w:.1f}" height="{BAR_H}" '
            f'rx="4" fill="{color}">'
            f'<title>{muscle}: {vol:,.0f} kg</title></rect>'
        )
        parts.append(
            f'<text x="{PAD_L + bar_W + 10}" y="{cy:.1f}" font-size="18" font-weight="700" '
            f'fill="{AXIS_TEXT}" font-family="{_NUM_FONT}" '
            f'style="font-variant-numeric:tabular-nums" dominant-baseline="middle">{pct}%</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


def generate_progress_plot(points: list[tuple[str, float]], target: float | None = None,
                           eta: str | None = None, width: int = 640, height: int = 240) -> str:
    """Home's lead chart: each session's best estimated 1RM as a trace-blue line,
    today's value marked in highlighter yellow, and (when there is one) a dashed
    projection to the target with the target as a red dotted datum line.

    points: [(iso_date, kg), ...] in date order. Text is 13px (the label size),
    so the chart reads the same at every width it is drawn."""
    from datetime import date as _date
    if len(points) < 2:
        return ""
    xs = [_date.fromisoformat(d) for d, _ in points]
    ys = [v for _, v in points]
    end = _date.fromisoformat(eta) if eta else xs[-1]
    x0, x1 = xs[0], max(end, xs[-1])
    lo = min(ys); hi = max(ys + ([target] if target else []))
    pad = max(2.0, (hi - lo) * 0.12)
    lo, hi = lo - pad, hi + pad
    L, R, T, B = 40, 10, 22, 30
    cw, ch = width - L - R, height - T - B
    span = max(1, (x1 - x0).days)
    X = lambda d: L + (d - x0).days / span * cw
    Y = lambda v: T + (1 - (v - lo) / (hi - lo)) * ch
    f = f'font-family="{_UI_FONT}" font-size="13" fill="{AXIS_TEXT}"'
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" xmlns="http://www.w3.org/2000/svg" '
           f'role="img" aria-label="Estimated one-rep max from {ys[0]:.0f} to {ys[-1]:.0f} kg'
           f'{f", projected {target:.0f} kg" if target else ""}" style="display:block">']
    out.append(f'<line x1="{L}" y1="{T}" x2="{L}" y2="{T + ch}" stroke="{LABEL_TEXT}" stroke-width="1"/>')
    out.append(f'<line x1="{L}" y1="{T + ch}" x2="{L + cw}" y2="{T + ch}" stroke="{LABEL_TEXT}" stroke-width="1"/>')
    # y labels: bottom, top and the target
    step = 10 if hi - lo > 25 else 5
    import math as _m
    for v in range(int(_m.ceil(lo / step) * step), int(hi) + 1, step):
        out.append(f'<text x="{L - 6}" y="{Y(v) + 4:.1f}" text-anchor="end" {f}>{v}</text>')
    if target:
        out.append(f'<line x1="{L}" y1="{Y(target):.1f}" x2="{L + cw}" y2="{Y(target):.1f}" '
                   f'stroke="#b23a26" stroke-width="1.25" stroke-dasharray="2 4"/>')
    # month labels
    seen, m = set(), _date(x0.year, x0.month, 1)
    while m <= x1:
        if m >= x0 and m.strftime("%b") not in seen:
            seen.add(m.strftime("%b"))
            out.append(f'<text x="{X(m):.1f}" y="{height - 8}" {f}>{m.strftime("%b")}</text>')
        m = _date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    pts = " ".join(f"{X(d):.1f},{Y(v):.1f}" for d, v in zip(xs, ys))
    out.append(f'<polyline points="{pts}" fill="none" stroke="{DATA_COLOR}" stroke-width="2.5" '
               f'stroke-linejoin="round" stroke-linecap="round"/>')
    cx, cy = X(xs[-1]), Y(ys[-1])
    if target and eta:
        out.append(f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{X(end):.1f}" y2="{Y(target):.1f}" '
                   f'stroke="{DATA_COLOR}" stroke-width="2" stroke-dasharray="5 5"/>')
        out.append(f'<circle cx="{X(end):.1f}" cy="{Y(target):.1f}" r="4" fill="none" stroke="{DATA_COLOR}" stroke-width="2"/>')
    out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5.5" fill="#f5d547" stroke="{LABEL_TEXT}" stroke-width="2"/>')
    out.append("</svg>")
    return "".join(out)
