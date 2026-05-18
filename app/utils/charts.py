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
