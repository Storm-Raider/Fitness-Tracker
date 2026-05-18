def generate_weekly_bar_chart(
    day_volumes: list[tuple[str, float]],
    color: str = "#4f9cf9",
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

    COLORS = ["#4f9cf9", "#f59e0b", "#34d399", "#f472b6", "#a78bfa", "#fb923c", "#67e8f9", "#86efac"]
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
        color = COLORS[i % len(COLORS)]

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


def generate_sparkline(
    values: list[float],
    labels: list[str] | None = None,
    color: str = "#4f9cf9",
    width: int = 600,
    height: int = 140,
    unit: str = "",
) -> str:
    """Return an inline SVG line chart. Returns '' when fewer than 2 data points."""
    n = len(values)
    if n < 2:
        return ""

    data_min = min(values)
    data_max = max(values)
    span = (data_max - data_min) if data_max > data_min else max(abs(data_min) * 0.1, 1.0)
    v_min = data_min - span * 0.12
    v_max = data_max + span * 0.12

    pad_l, pad_r, pad_t, pad_b = 48, 16, 10, 24
    cw = width - pad_l - pad_r
    ch = height - pad_t - pad_b

    def px(i: int) -> float:
        return pad_l + (i / (n - 1)) * cw

    def py(v: float) -> float:
        return pad_t + (1.0 - (v - v_min) / (v_max - v_min)) * ch

    points = [(px(i), py(v)) for i, v in enumerate(values)]
    line_pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    area_pts = (
        f"{line_pts} "
        f"{points[-1][0]:.1f},{pad_t + ch:.1f} "
        f"{points[0][0]:.1f},{pad_t + ch:.1f}"
    )

    font = "Inter,system-ui,sans-serif"

    parts: list[str] = [
        f'<svg viewBox="0 0 {width} {height}" style="width:100%;height:auto;" '
        f'xmlns="http://www.w3.org/2000/svg">',
    ]

    # Horizontal grid lines at min / mid / max
    for frac in (0.0, 0.5, 1.0):
        gv = data_min + frac * (data_max - data_min)
        gy = py(gv)
        parts.append(
            f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{pad_l + cw}" y2="{gy:.1f}" '
            f'stroke="#1f2535" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{pad_l - 4}" y="{gy:.1f}" font-size="10" fill="#4a5568" '
            f'font-family="{font}" text-anchor="end" dominant-baseline="middle">'
            f'{gv:.1f}{unit}</text>'
        )

    # Area fill
    parts.append(
        f'<polygon points="{area_pts}" fill="{color}" opacity="0.07"/>'
    )

    # Line
    parts.append(
        f'<polyline points="{line_pts}" fill="none" stroke="{color}" '
        f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'
    )

    # Dots (skip if very dense)
    if n <= 52:
        for x, y in points:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.5" fill="{color}"/>')
    else:
        for x, y in (points[0], points[-1]):
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}"/>')

    # Current value label above last dot — anchor end near right edge to avoid clipping
    lx, ly = points[-1]
    label_anchor = "end" if lx > pad_l + cw * 0.7 else "middle"
    parts.append(
        f'<text x="{lx:.1f}" y="{ly - 9:.1f}" font-size="11" fill="{color}" '
        f'font-family="{font}" text-anchor="{label_anchor}" font-weight="600">'
        f'{values[-1]:.1f}{unit}</text>'
    )

    # X-axis date labels (first / last)
    if labels:
        parts.append(
            f'<text x="{points[0][0]:.1f}" y="{height - 2}" font-size="9" fill="#4a5568" '
            f'font-family="{font}" text-anchor="start">{labels[0]}</text>'
        )
        parts.append(
            f'<text x="{points[-1][0]:.1f}" y="{height - 2}" font-size="9" fill="#4a5568" '
            f'font-family="{font}" text-anchor="end">{labels[-1]}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)
