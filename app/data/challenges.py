"""
Challenge templates — fixed-length daily-adherence programs (75 Hard, etc.).

Each template is data so new challenges are a config change, not code. A rule's
`kind` decides how completion is satisfied:
  - "workout": auto-satisfied if a workout OR cardio is logged that day
               (also manually tickable for sessions you didn't log here)
  - "photo":   manual tick; the image itself is stored ON-DEVICE (IndexedDB),
               never uploaded — the server only records done/not-done
  - "manual":  a plain daily tick

`optional: True` rules show in the UI but do NOT count toward day-completeness
or the reset (used for 75 Medium's optional photo).
"""

CHALLENGES = [
    {
        "key": "75_hard",
        "name": "75 Hard",
        "total_days": 75,
        "tagline": "No compromises. Miss one rule and you restart at Day 1.",
        "rules": [
            {"key": "workout1", "label": "Workout #1 — 45 min", "kind": "workout"},
            {"key": "workout2", "label": "Workout #2 — 45 min, one outdoors", "kind": "manual"},
            {"key": "diet",     "label": "Follow your diet — no alcohol, no cheat meals", "kind": "manual"},
            {"key": "water",    "label": "Drink 1 gallon (3.8 L) of water", "kind": "manual"},
            {"key": "read",     "label": "Read 10 pages (non-fiction)", "kind": "manual"},
            {"key": "photo",    "label": "Take a progress photo", "kind": "photo"},
        ],
    },
    {
        "key": "75_medium",
        "name": "75 Medium",
        "total_days": 75,
        "editable": True,
        "allow_partial": True,
        "no_fail": True,
        "tagline": "Sustainable discipline. One workout a day, a little grace.",
        "rules": [
            {"key": "workout1", "label": "Workout — 45 min", "kind": "workout"},
            {"key": "diet",     "label": "Follow your diet — 1 cheat meal/week allowed", "kind": "manual"},
            {"key": "water",    "label": "Drink ~3 L of water", "kind": "manual"},
            {"key": "read",     "label": "Read 10 pages", "kind": "manual"},
            {"key": "photo",    "label": "Progress photo", "kind": "photo", "optional": True},
        ],
    },
]

CHALLENGE_INDEX = {c["key"]: c for c in CHALLENGES}
