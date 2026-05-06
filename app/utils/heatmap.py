from datetime import date, timedelta


def generate_heatmap_svg(workout_dates: list[str]) -> str:
    """
    Generate a 52-week activity heatmap SVG.
    workout_dates: list of ISO date strings (YYYY-MM-DD) within the last 52 weeks.
    Each week is a column; each day is a 10x10 cell with 2px gap.
    """
    workout_set = set(workout_dates)
    today = date.today()
    # Start from 52 weeks ago, aligned to Monday of that week
    start = today - timedelta(weeks=52)
    start -= timedelta(days=start.weekday())  # back to Monday

    CELL = 10
    GAP = 2
    STEP = CELL + GAP
    WEEKS = 53  # enough columns to cover 52 weeks + partial
    DAYS = 7
    WIDTH = WEEKS * STEP + 2
    HEIGHT = DAYS * STEP + 2

    cells = []
    d = start
    col = 0
    while d <= today:
        row = d.weekday()  # 0=Mon … 6=Sun
        x = col * STEP + 1
        y = row * STEP + 1
        filled = d.isoformat() in workout_set
        color = "#60a5fa" if filled else "#262b35"
        title = d.isoformat()
        cells.append(
            f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" '
            f'rx="2" fill="{color}" opacity="{"0.9" if filled else "1"}">'
            f'<title>{title}</title></rect>'
        )
        d += timedelta(days=1)
        if row == 6:
            col += 1

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'style="display:block">'
        + "".join(cells)
        + "</svg>"
    )
