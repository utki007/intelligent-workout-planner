"""
Exercise model and metadata from data/exercises_db.xlsx.
Each exercise has warmup, main sets/reps/weight, and cooldown.
"""

from dataclasses import dataclass
from typing import Optional, List


# Default warmup/cooldown when not in Excel
DEFAULT_WARMUP = "5 min light cardio; dynamic stretches for target muscle."
DEFAULT_COOLDOWN = "5 min light stretch; focus on worked muscle groups."

# Base weight (kg) by target muscle — used when Excel has no weight. Typical beginner-friendly loads.
TARGET_BASE_WEIGHT_KG = {
    "abs": 0, "core": 0, "waist": 0, "obliques": 0, "hip flexors": 0, "lower abs": 0,
    "biceps": 8, "triceps": 6, "upper arms": 7, "lower arms": 4, "forearms": 4,
    "shoulders": 14, "delts": 14, "neck": 5,
    "chest": 38, "upper chest": 35, "pectorals": 38,
    "back": 35, "lats": 32, "upper back": 30, "rhomboids": 28, "lower back": 22, "traps": 25,
    "quads": 45, "quadriceps": 45, "glutes": 38, "hamstrings": 32, "adductors": 20, "abductors": 12,
    "calves": 12, "lower legs": 10,
}
# Name keywords → weight (kg). Checked before target so "barbell bench press" gets 50, not generic chest.
NAME_WEIGHT_KG = [
    # Bodyweight / no load
    ("push-up", 0), ("push up", 0), ("pull-up", 0), ("pull up", 0), ("chin-up", 0), ("dip", 0),
    ("plank", 0), ("crunch", 0), ("sit-up", 0), ("sit up", 0), ("lunge", 0), ("air bike", 0),
    ("burpee", 0), ("mountain climber", 0), ("jump", 0), ("calf raise", 0), ("bodyweight", 0),
    # Big compound
    ("deadlift", 55), ("squat", 50), ("bench press", 50), ("barbell row", 40), ("overhead press", 35),
    ("bench", 48), ("squat", 50), ("row", 32), ("press", 36), ("clean", 30), ("snatch", 25),
    # Isolation / smaller
    ("curl", 8), ("extension", 6), ("fly", 10), ("flye", 10), ("raise", 6), ("pullover", 18),
    ("kickback", 4), ("pushdown", 8), ("pull-down", 28), ("pulldown", 28), ("lat ", 28),
    ("leg curl", 15), ("leg extension", 20), ("leg press", 80), ("lunge", 0), ("step-up", 8),
    ("hip thrust", 35), ("glute bridge", 25), ("good morning", 20), ("back extension", 15),
    ("cable", 15), ("dumbbell", 12), ("kettlebell", 14), ("barbell", 40), ("machine", 25),
]
DEFAULT_BASE_WEIGHT_KG = 18.0


def _base_weight_kg(name: str, target: str) -> float:
    """Sensible base weight in kg for every exercise. Uses name keywords first, then target muscle."""
    n = (name or "").lower().strip()
    t = (target or "").lower().strip()
    # Name-based (longer phrases first to avoid "press" matching before "bench press")
    for keyword, w in sorted(NAME_WEIGHT_KG, key=lambda x: -len(x[0])):
        if keyword in n:
            return float(w)
    # Target-based
    for muscle, w in TARGET_BASE_WEIGHT_KG.items():
        if muscle in t:
            return float(w)
    return DEFAULT_BASE_WEIGHT_KG


@dataclass
class Exercise:
    """Exercise with prescription fields for the plan."""
    name: str
    target_muscle: str
    difficulty: str
    warmup_description: str
    main_sets: int
    reps: int
    weight: float  # kg
    cooldown_description: str
    secondary_muscles: Optional[str] = None

    @classmethod
    def from_excel_row(cls, row: dict, intensity_level: int = 1) -> "Exercise":
        """
        Build from exercises_db row. Weight/reps scaled by intensity_level (1-5).
        Beginner → lower weight, higher reps; Advanced → higher weight, lower reps.
        """
        name = str(row.get("name", "")).strip()
        target = str(row.get("target", "")).strip() if row.get("target") else ""
        difficulty = str(row.get("difficulty", "beginner")).strip().lower()
        # Optional columns
        warmup = str(row.get("warmup_description", DEFAULT_WARMUP)).strip() or DEFAULT_WARMUP
        cooldown = str(row.get("cooldown_description", DEFAULT_COOLDOWN)).strip() or DEFAULT_COOLDOWN
        sec = row.get("secondaryMuscles") or row.get("secondary_muscles")
        secondary_muscles = ",".join(sec) if isinstance(sec, list) else str(sec or "").strip()

        # Base prescription from row or defaults
        base_sets = int(row.get("main_sets", row.get("sets", 3)))
        base_reps = int(row.get("reps", 12))
        base_weight = float(row.get("weight", 0) or 0)
        if base_weight <= 0:
            base_weight = _base_weight_kg(name, target)

        # Scale by intensity_level (1-5): higher → more weight, more sets, fewer reps
        weight_mult = 0.5 + (intensity_level - 1) * 0.125   # 0.5 at L1 to 1.0 at L5
        main_sets = min(5, base_sets + (intensity_level - 1) // 2)  # 3→3,4→4,5→5 sets
        reps_adj = -(intensity_level - 1) * 2   # 12 at L1 down to 4 at L5
        weight = round(base_weight * weight_mult, 1)
        reps = max(4, min(20, base_reps + reps_adj))

        return cls(
            name=name,
            target_muscle=target,
            difficulty=difficulty,
            warmup_description=warmup,
            main_sets=main_sets,
            reps=reps,
            weight=weight,
            cooldown_description=cooldown,
            secondary_muscles=secondary_muscles or None,
        )
