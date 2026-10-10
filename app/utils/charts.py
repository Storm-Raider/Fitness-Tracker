# --data in base.html: single-series charts. Blue is chrome, gold a PR, cyan "running".
DATA_COLOR = "#9085e9"

# One colour per muscle, used wherever muscles are charted (dashboard bars, the
# workout page's muscle map via the MUSCLE_COLORS template global), so a muscle
# keeps its colour whatever its rank. No blue, gold or live cyan. Eight hues
# can't all be told apart (worst pair Abs/Back, OKLab ΔE 9.7 < 15), so every
# muscle chart labels its muscles and colour is a secondary cue.
MUSCLE_COLORS = {
    "Chest":     "#d55181",
    "Shoulders": "#a78bfa",
    "Biceps":    "#34d399",
    "Triceps":   "#de47f5",
    "Forearms":  "#8f2af4",
    "Abs":       "#f472b6",
    "Back":      "#f87171",
    "Legs":      "#fb923c",
}
_OTHER_MUSCLE = "#94a3b8"


def generate_weekly_bar_chart(
    day_volumes: list[tuple[str, float]],
    color: str = DATA_COLOR,
) -> str:
    """Return an inline SVG bar chart for 7-day volume. day_volumes is [(date_str, volume_kg), ...]."""
    from datetime import date as _date

    if not day_volumes:
        return ""

    W, H = 420, 88
    pad_l, pad_r, pad_t, pad_b = 6, 6, 6, 18
    n = len(day_volumes)
    gap = 5
    bar_w = (W - pad_l - pad_r - gap * (n - 1)) / n
    chart_h = H - pad_t - pad_b

    max_vol = max((v for _, v in day_volumes), default=0) or 1
    today_str = _date.today().isoformat()
    font = "JetBrains Mono,monospace"
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
            opacity = "1" if is_today else "0.48"
            vol_tip = f"{vol:,.0f} kg"
            parts.append(
                f'<rect x="{x:.1f}" y="{bar_y:.1f}" width="{bar_w:.1f}" '
                f'height="{bar_h:.1f}" rx="3" fill="{color}" opacity="{opacity}">'
                f'<title>{vol_tip}</title></rect>'
            )
        else:
            stub_y = pad_t + chart_h - 3
            parts.append(
                f'<rect x="{x:.1f}" y="{stub_y:.1f}" width="{bar_w:.1f}" '
                f'height="3" rx="1.5" fill="#1e2334" opacity="0.9"/>'
            )

        day_fill = color if is_today else "#5a6a82"
        day_fw = "600" if is_today else "400"
        parts.append(
            f'<text x="{cx:.1f}" y="{H - 3}" text-anchor="middle" font-size="9" '
            f'font-family="{font}" fill="{day_fill}" font-weight="{day_fw}">{label}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


def generate_muscle_bars(muscle_volumes: list[tuple[str, float]]) -> str:
    """Return an inline SVG horizontal bar chart for muscle group volume breakdown."""
    if not muscle_volumes:
        return ""

    W      = 420
    ROW_H  = 22
    GAP    = 8
    PAD_L  = 108
    PAD_R  = 52
    n      = len(muscle_volumes)
    H      = n * (ROW_H + GAP) - GAP
    bar_W  = W - PAD_L - PAD_R
    max_v  = max(v for _, v in muscle_volumes) or 1
    total  = sum(v for _, v in muscle_volumes) or 1
    font   = "Inter,system-ui,sans-serif"
    mono   = "JetBrains Mono,monospace"

    parts = [
        f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%;height:auto;display:block;">'
    ]

    for i, (muscle, vol) in enumerate(muscle_volumes):
        cy   = i * (ROW_H + GAP) + ROW_H / 2
        bar_y = cy - 7
        fill_w = max(4.0, (vol / max_v) * bar_W)
        pct   = round(vol / total * 100)
        color = MUSCLE_COLORS.get(muscle, _OTHER_MUSCLE)

        parts.append(
            f'<text x="{PAD_L - 8}" y="{cy:.1f}" font-size="10" fill="#7a8a9a" '
            f'font-family="{font}" text-anchor="end" dominant-baseline="middle">'
            f'{muscle}</text>'
        )
        parts.append(
            f'<rect x="{PAD_L}" y="{bar_y:.1f}" width="{bar_W}" height="14" '
            f'rx="3" fill="#151c2c"/>'
        )
        parts.append(
            f'<rect x="{PAD_L}" y="{bar_y:.1f}" width="{fill_w:.1f}" height="14" '
            f'rx="3" fill="{color}" opacity="0.82">'
            f'<title>{muscle}: {vol:,.0f} kg</title></rect>'
        )
        parts.append(
            f'<text x="{PAD_L + bar_W + 8}" y="{cy:.1f}" font-size="9.5" fill="#7a8a9a" '
            f'font-family="{mono}" dominant-baseline="middle">{pct}%</text>'
        )

    parts.append("</svg>")
    return "".join(parts)
