"""Illustrations from RepDB's free tier (github.com/RepDB/exercise-dataset) for
the curated lifts free-exercise-db has no photos for.

License: in-app use is allowed with a visible "Exercise data by RepDB
(repdb.co)" credit, which the detail page and the README carry. The images may
be resized but not republished as a dataset, so only these few are used, at
400px. Each pair was checked by eye; Toes-to-Bar (its images show a knee raise)
and Pike Push Ups (distorted pose) were rejected.

Zenkai name -> RepDB exercise name."""

REPDB_IMAGES: dict[str, str] = {
    "Assault Bike": "Air Bike",            # RepDB's Air Bike is the fan bike
    "Burpee": "Burpees",
    "Cable Tricep Kickback": "Cable Tricep Kickback",
    "Copenhagen Plank": "Copenhagen Plank",
    "Donkey Kick": "Glute Kickback",       # bodyweight, on all fours
    "Dragon Flag": "Dragon Flag",
    "Dumbbell Hip Thrust": "Dumbbell Hip Thrust",
    "Fire Hydrant": "Fire Hydrant",
    "Flutter Kicks": "Flutter Kicks",
    "Hanging Knee Raise": "Hanging Knee Raise",
    "Hollow Hold": "Hollow Body Hold",
    "Incline Walk": "Incline Treadmill Walk",
    "Jumping Jacks": "Jumping Jacks",
    "Kettlebell Halo": "Kettlebell Halo",
    "Kettlebell Overhead Triceps Extension": "Kettlebell Overhead Tricep Extension",
    "L-Sit": "L Sit",
    "Landmine Press": "Landmine Press",
    "Lateral Lunge": "Side Lunge",
    "Pause Squat": "Pause Squat",
    "Pendlay Row": "Pendlay Row",
    "Ski Erg": "SkiErg",
    "Split Squat": "Dumbbell Split Squat",
    "V-Up": "V Ups",
    "Wall Sit": "Wall Sit",
}
