from datetime import date, timedelta


def max_streak(workout_dates: list[str]) -> int:
    """Return the longest consecutive-day run across all history. No today-guard."""
    if not workout_dates:
        return 0
    unique = sorted({d for d in workout_dates})
    current_run = 1
    best_run = 1
    for i in range(1, len(unique)):
        if date.fromisoformat(unique[i]) - date.fromisoformat(unique[i - 1]) == timedelta(days=1):
            current_run += 1
            best_run = max(best_run, current_run)
        else:
            current_run = 1
    return best_run


def compute_streak(workout_dates: list[str]) -> int:
    """
    Count consecutive calendar days ending today that have at least one workout.
    workout_dates: list of ISO date strings (YYYY-MM-DD), may contain duplicates.
    Returns 0 if no workout today.
    """
    if not workout_dates:
        return 0

    unique = sorted({d for d in workout_dates}, reverse=True)
    today = date.today().isoformat()

    if unique[0] != today:
        return 0

    streak = 0
    expected = date.today()
    for d in unique:
        if d == expected.isoformat():
            streak += 1
            expected -= timedelta(days=1)
        elif d < expected.isoformat():
            break

    return streak
