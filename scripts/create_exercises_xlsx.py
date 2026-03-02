"""One-off script to create data/exercises.xlsx and data/exercises_db.xlsx. Run from repo root."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd

os.makedirs("data", exist_ok=True)
# Each theme (chest, back, legs, biceps, triceps, shoulders) has 5-6 exercises for variety.
df = pd.DataFrame({
    "name": [
        # Chest (6)
        "Barbell Bench Press", "Push-up", "Cable Chest Fly", "Dumbbell Chest Press",
        "Incline Push-up", "Chest Dip",
        # Back (6)
        "Lat Pulldown", "Bent Over Row", "Dumbbell Row", "Seated Cable Row",
        "Pull-up", "Deadlift",
        # Legs (6)
        "Bodyweight Squat", "Leg Press", "Romanian Deadlift", "Leg Curl",
        "Leg Extension", "Goblet Squat",
        # Biceps (6)
        "Bicep Curl", "Hammer Curl", "Preacher Curl", "Concentration Curl",
        "Cable Bicep Curl", "Incline Dumbbell Curl",
        # Triceps (6)
        "Tricep Pushdown", "Tricep Dip", "Overhead Tricep Extension", "Close-Grip Push-up",
        "Skull Crusher", "Diamond Push-up",
        # Shoulders (6)
        "Overhead Press", "Lateral Raise", "Front Raise", "Face Pull",
        "Arnold Press", "Reverse Fly",
    ],
    "target": [
        "chest", "chest", "chest", "chest", "chest", "chest",
        "back", "back", "back", "back", "back", "back",
        "legs", "legs", "legs", "legs", "legs", "legs",
        "biceps", "biceps", "biceps", "biceps", "biceps", "biceps",
        "triceps", "triceps", "triceps", "triceps", "triceps", "triceps",
        "shoulders", "shoulders", "shoulders", "shoulders", "shoulders", "shoulders",
    ],
    "difficulty": ["beginner"] * 36,
})
for filename in ("exercises.xlsx", "exercises_db.xlsx"):
    path = os.path.join("data", filename)
    df.to_excel(path, index=False)
    print("Created", path)
