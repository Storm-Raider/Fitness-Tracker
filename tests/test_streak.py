from datetime import date, timedelta
import pytest

from app.utils.streak import compute_streak


def today():
    return date.today().isoformat()


def days_ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


def test_empty_list_returns_zero():
    assert compute_streak([]) == 0


def test_streak_with_only_today():
    assert compute_streak([today()]) == 1


def test_streak_consecutive_days():
    dates = [today(), days_ago(1), days_ago(2), days_ago(3)]
    assert compute_streak(dates) == 4


def test_gap_breaks_streak():
    # Today and 2 days ago, but not yesterday
    dates = [today(), days_ago(2)]
    assert compute_streak(dates) == 1


def test_no_workout_today_returns_zero():
    dates = [days_ago(1), days_ago(2), days_ago(3)]
    assert compute_streak(dates) == 0


def test_duplicates_handled():
    dates = [today(), today(), days_ago(1), days_ago(1)]
    assert compute_streak(dates) == 2
