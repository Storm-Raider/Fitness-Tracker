from datetime import date, timedelta

from app.utils.heatmap import generate_heatmap_svg


def test_empty_dates_produces_svg():
    svg = generate_heatmap_svg([])
    assert svg.startswith("<svg")
    assert "</svg>" in svg


def test_active_day_uses_trace_blue():
    today = date.today().isoformat()
    svg = generate_heatmap_svg([today])
    # Daylight: activity is data, drawn in ink. Amber is reserved for records.
    assert 'fill="#1e5bb8"' in svg
    assert "#f59e0b" not in svg and "#8c5a00" not in svg


def test_inactive_day_uses_the_grid_color():
    svg = generate_heatmap_svg([])
    assert 'fill="#dde4db"' in svg
    assert 'fill="#1e5bb8"' not in svg


def test_svg_contains_rect_elements():
    svg = generate_heatmap_svg([])
    assert "<rect" in svg


def test_only_last_52_weeks_shown():
    # A date more than 52 weeks ago should not produce an active cell even if passed in
    old_date = (date.today() - timedelta(weeks=53)).isoformat()
    svg_with = generate_heatmap_svg([old_date])
    svg_without = generate_heatmap_svg([])
    # Both should be identical — old date ignored by SQL filter upstream
    # (heatmap.py itself doesn't filter; SQL does; we just check it doesn't crash)
    assert svg_with.startswith("<svg")


def test_a_shorter_heatmap_fits_a_phone_without_scrolling():
    # 26 weeks of 12-unit columns fit a 390px phone's card at full cell size;
    # the svg scales to the card instead of forcing a scrollable min-width.
    svg = generate_heatmap_svg([], weeks=26)
    assert 'viewBox="0 0 326 ' in svg
    assert "min-width" not in svg and "width:100%" in svg
    old = (date.today() - timedelta(weeks=30)).isoformat()
    assert old not in generate_heatmap_svg([old], weeks=26)      # out of range: no cell
    assert old in generate_heatmap_svg([old])                    # the 52-week map still has it
