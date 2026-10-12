from datetime import date, timedelta


def heatmap_start(today: date | None = None, weeks: int = 52) -> date:
    """First day drawn: the Monday of the week `weeks` weeks ago. The query
    feeding the heatmap must start at the 52-week start, or that first column
    shows real days blank (it used to fetch only the last 364 days)."""
    today = today or date.today()
    start = today - timedelta(weeks=weeks)
    return start - timedelta(days=start.weekday())


def generate_heatmap_svg(workout_dates: list[str], weeks: int = 52) -> str:
    """
    Generate an activity heatmap SVG of the last `weeks` weeks (52, or 26 for a
    phone, where 52 columns can't fit at a readable size).
    workout_dates: list of ISO date strings (YYYY-MM-DD) within the last 52 weeks.
    Each week is a column; each day is a 10x10 cell with 2px gap.
    """
    workout_set = set(workout_dates)
    today = date.today()
    start = heatmap_start(today, weeks)

    CELL = 10
    GAP = 2
    STEP = CELL + GAP
    WEEKS = weeks + 1  # the weeks plus the partial current one
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
        color = "#141414" if filled else "#e7e6e1"  # ink = trained (data); amber is records only
        title = d.isoformat()
        cells.append(
            f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" '
            f'rx="2" fill="{color}" opacity="1">'
            f'<title>{title}</title></rect>'
        )
        d += timedelta(days=1)
        if row == 6:
            col += 1

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" '
        # Scales to its card; never wider, so the card never scrolls sideways.
        f'style="display:block;width:100%;height:auto">'
        + "".join(cells)
        + "</svg>"
    )
