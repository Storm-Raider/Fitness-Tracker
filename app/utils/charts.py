# Daylight chart colours (DESIGN.md). These SVGs are built server-side, so they
# use the base.html token values as literals: --data ink for a single series,
# --primary orange for today's bar (a mark, never text), --muted for axis text,
# --surface-hover for empty tracks/stubs.
DATA_COLOR = "#141414"      # --data (ink)
TODAY_COLOR = "#ff5a1f"     # --primary: today's bar only
AXIS_TEXT = "#5d5d58"       # --muted: 6.6:1 on the white card
LABEL_TEXT = "#141414"      # --text
TRACK_COLOR = "#efefea"     # --surface-hover: empty track / zero-day stub
_UI_FONT = "Atkinson Hyperlegible Next,system-ui,sans-serif"
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
