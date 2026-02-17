"""
Weekly plan model: one row per (user_id, week_number, day, exercise) for Excel.
"""

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List


@dataclass
class WeeklyPlanEntry:
    """Single row for weekly_plans.xlsx."""
    user_id: str
    week_number: int
    day: int  # 1-5
    exercise_name: str
    sets: int
    reps: int
    weight: float
    timestamp: str

    def to_row(self) -> dict:
        return asdict(self)


def day_to_focus(day: int) -> str:
    """Map day number to muscle focus (5-day split)."""
    return {1: "Chest", 2: "Back", 3: "Legs", 4: "Biceps/Triceps", 5: "Shoulders"}.get(day, "")
