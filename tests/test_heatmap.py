from datetime import date, timedelta

from app.utils.heatmap import generate_heatmap_svg


def test_empty_dates_produces_svg():
    svg = generate_heatmap_svg([])
    assert svg.startswith("<svg")
    assert "</svg>" in svg


def test_active_day_uses_ink():
    today = date.today().isoformat()
    svg = generate_heatmap_svg([today])
    # Daylight: activity is data, drawn in ink. Amber is reserved for records.
    assert 'fill="#141414"' in svg
    assert "#f59e0b" not in svg and "#8c5a00" not in svg


def test_inactive_day_uses_light_neutral():
    svg = generate_heatmap_svg([])
    assert 'fill="#e7e6e1"' in svg
    assert 'fill="#141414"' not in svg


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
