import pandas as pd
from pyDatalog import pyDatalog

# -----------------------------------
# 1. Load Exercise Data
# -----------------------------------
exercise_dataframe = pd.read_excel('data/exercises_db.xlsx', engine='openpyxl', index_col=0)

# Normalize text fields for consistency
def normalize_text(value):
    if pd.isna(value):
        return None
    return str(value).strip().lower().replace(" ", "_")

exercise_dataframe["name"] = exercise_dataframe["name"].apply(normalize_text)
exercise_dataframe["target"] = exercise_dataframe["target"].apply(normalize_text)
exercise_dataframe["difficulty"] = exercise_dataframe["difficulty"].apply(normalize_text)

# -----------------------------------
# 2. Initialize Knowledge Base
# -----------------------------------

pyDatalog.clear()

pyDatalog.create_terms(
    "Exercise, Muscle, DifficultyLevel, SecondaryMuscles, BreakDays"
)
pyDatalog.create_terms(
    "is_exercise, trains_muscle, has_difficulty"
)
pyDatalog.create_terms(
    "muscle_recovery, injured_muscle, forbidden_exercise, recommended_exercise, on_cooldown"
)

# -----------------------------------
# 3. Define Recovery Times (Break Days)
# -----------------------------------
muscle_recovery_times = {
    # Lower body
    "glutes": 2, "quadriceps": 2, "hamstrings": 2,
    "adductors": 1, "abductors": 1, "hip_flexors": 1, "inner_thighs": 1,
    "shins": 1, "calves": 1, "soleus": 1,
    # Upper body
    "pectorals": 1, "upper_chest": 1, "delts": 1, "rear_deltoids": 1,
    "traps": 1, "trapezius": 1, "upper_back": 1, "lats": 1, "latissimus_dorsi": 1,
    "rhomboids": 1, "rotator_cuff": 1, "shoulders": 1, "biceps": 1, "triceps": 1,
    "brachialis": 1, "forearms": 0, "wrist_extensors": 0, "wrist_flexors": 0,
    "wrists": 0, "hands": 0, "grip_muscles": 0,
    # Core / small muscles
    "abs": 0, "lower_abs": 0, "obliques": 0, "core": 0,
    "back": 1, "lower_back": 2, "spine": 2, "sternocleidomastoid": 0,
    "neck": 0, "ankles": 0, "ankle_stabilizers": 0, "feet": 0,
    "cardiovascular_system": 0, "groin": 1, "serratus_anterior": 0
}
# Add muscle recovery facts
for muscle, break_days in muscle_recovery_times.items():
    + muscle_recovery(muscle, break_days)

# -----------------------------------
# 3. Convert Data Rows → Logical Facts
# -----------------------------------

for _, row in exercise_dataframe.iterrows():
    exercise_name = row["name"]
    target_muscle = row["target"]
    secondary_muscles = row["secondaryMuscles"]
    difficulty_level = row["difficulty"]

    if exercise_name:
        + is_exercise(exercise_name)

        if target_muscle:
            + trains_muscle(exercise_name, target_muscle)

        if difficulty_level:
            + has_difficulty(exercise_name, difficulty_level)

        if secondary_muscles:
            for secondary_muscle in secondary_muscles.split(", "):
                secondary_muscle = normalize_text(secondary_muscle)
                + trains_muscle(exercise_name, secondary_muscle)

# -----------------------------------
# 4. Add User Injury Information
# -----------------------------------

user_injured_muscle = "biceps"
user_muscle_cooldown = ["biceps", "triceps"]
normalized_injury = normalize_text(user_injured_muscle)

+ injured_muscle(normalized_injury)

for muscle in user_muscle_cooldown:
    + on_cooldown(muscle)

# -----------------------------------
# 5. Define Logical Rules
# -----------------------------------

# Rule: An exercise is forbidden if it trains an injured muscle
forbidden_exercise(Exercise) <= (
    trains_muscle(Exercise, Muscle) &
    injured_muscle(Muscle)
)

# Exercise is forbidden if it trains a muscle on cooldown
forbidden_exercise(Exercise) <= (
    trains_muscle(Exercise, Muscle) &
    on_cooldown(Muscle)
)

# Rule: Recommend exercises that are not forbidden
recommended_exercise(Exercise) <= (
    is_exercise(Exercise) &
    ~forbidden_exercise(Exercise)
)

# -----------------------------------
# 6. Query Top 10 Safe Exercises
# -----------------------------------

safe_exercise_results = recommended_exercise(Exercise)

top_10_safe_exercises = [result[0] for result in safe_exercise_results][:10]

print("\nTop 10 Recommended Safe Exercises:\n")
for exercise in top_10_safe_exercises:
    print("-", exercise)
print(len(safe_exercise_results))
