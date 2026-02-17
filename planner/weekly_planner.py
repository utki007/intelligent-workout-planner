"""
Weekly plan generation: 5-day split with warmup, 4-6 exercises per day, cooldown.
Uses KRR to get recommended (non-forbidden) exercises and filters by day focus.
"""

import os
import pandas as pd
from datetime import datetime
from typing import List, Optional

# Add project root for imports when running as script
import sys
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from models.user import User
from models.exercise import Exercise, DEFAULT_WARMUP, DEFAULT_COOLDOWN
from models.weekly_plan import WeeklyPlanEntry, day_to_focus
from krr.knowledge_base import KnowledgeBase
from data.excel_storage import (
    get_user,
    get_or_create_user,
    get_last_week_number,
    append_weekly_plan_entries,
    save_feedback,
    update_intensity_from_feedback,
)

# Day (1-5) -> target muscle keywords for filtering exercises
DAY_TARGET_MAP = {
    1: ["pectorals", "chest", "upper chest"],
    2: ["lats", "traps", "back", "spine", "rhomboids"],
    3: ["quadriceps", "hamstrings", "glutes", "calves", "adductors", "abductors", "legs"],
    4: ["biceps", "triceps", "brachialis", "forearms"],
    5: ["delts", "shoulders", "rear deltoids"],
}

EXERCISES_PATH = os.path.join(_ROOT, "data", "exercises_db.xlsx")


class WeeklyPlanner:
    """Generate weekly plans using KRR and store in Excel."""

    def __init__(self, exercises_path: str = EXERCISES_PATH):
        self.exercises_path = exercises_path
        self.kb = KnowledgeBase(exercises_path=exercises_path)

    def _load_exercises_df(self) -> pd.DataFrame:
        return self.kb.load_exercises()

    def _exercises_for_day(self, user_id: str, day: int, intensity_level: int, max_exercises: int = 6) -> List[Exercise]:
        """Get 4-6 recommended exercises for this day's muscle focus."""
        keywords = DAY_TARGET_MAP.get(day, [])
        recommended = self.kb.get_recommended_exercises(user_id, limit=500)
        df = self.kb.get_exercise_dataframe()

        # Filter by target muscle matching day focus
        chosen = []
        for ex_name in recommended:
            if len(chosen) >= max_exercises:
                break
            ex_str = str(ex_name).strip()
            row = df[df["name"].astype(str).str.strip().str.lower() == ex_str.lower()]
            if row.empty:
                continue
            row = row.iloc[0]
            target = str(row.get("target", "")).lower()
            if not any(kw in target for kw in keywords):
                continue
            try:
                ex = Exercise.from_excel_row(row.to_dict(), intensity_level=intensity_level)
                chosen.append(ex)
            except Exception:
                continue
        return chosen[:max_exercises]

    def generate_plan(self, user_id: str, week_number: Optional[int] = None) -> dict:
        """
        Generate a full week plan for the user. Returns plan dict and writes rows to weekly_plans.xlsx.
        If week_number is None, uses last_week + 1.
        """
        user = get_user(user_id)
        if user is None:
            user = get_or_create_user(user_id)
        if week_number is None:
            week_number = get_last_week_number(user_id) + 1

        self.kb.clear()
        self.kb.load_exercises()
        self.kb.set_user_injuries(user_id, user.injury_muscles)

        intensity = user.current_intensity_level
        entries: List[WeeklyPlanEntry] = []
        ts = datetime.now().isoformat()
        plan = {"user_id": user_id, "week_number": week_number, "days": {}}

        for day in range(1, 6):
            focus = day_to_focus(day)
            exercises = self._exercises_for_day(user_id, day, intensity, max_exercises=6)
            if len(exercises) < 4:
                # Fallback: any recommended for this target
                exercises = self._exercises_for_day(user_id, day, intensity, max_exercises=6)

            day_plan = {
                "focus": focus,
                "warmup": DEFAULT_WARMUP,
                "exercises": [],
                "cooldown": DEFAULT_COOLDOWN,
            }

            # KRR-level reasoning for this day
            theme_keywords = DAY_TARGET_MAP.get(day, [])
            exercise_names = [ex.name for ex in exercises]
            day_plan["krr"] = self.kb.get_krr_reasoning_for_day(
                user_id, day, focus, theme_keywords, exercise_names
            )

            # Warmup row
            entries.append(WeeklyPlanEntry(
                user_id=user_id,
                week_number=week_number,
                day=day,
                exercise_name="Warmup",
                sets=0,
                reps=0,
                weight=0.0,
                timestamp=ts,
            ))

            for ex in exercises:
                day_plan["exercises"].append({
                    "name": ex.name,
                    "sets": ex.main_sets,
                    "reps": ex.reps,
                    "weight": ex.weight,
                })
                entries.append(WeeklyPlanEntry(
                    user_id=user_id,
                    week_number=week_number,
                    day=day,
                    exercise_name=ex.name,
                    sets=ex.main_sets,
                    reps=ex.reps,
                    weight=ex.weight,
                    timestamp=ts,
                ))

            # Cooldown row
            entries.append(WeeklyPlanEntry(
                user_id=user_id,
                week_number=week_number,
                day=day,
                exercise_name="Cooldown",
                sets=0,
                reps=0,
                weight=0.0,
                timestamp=ts,
            ))

            plan["days"][day] = day_plan

        append_weekly_plan_entries(entries)
        return plan


def generate_next_week(user_id: str) -> dict:
    """Generate a new plan (week_number = last + 1), save to Excel, return plan."""
    week_number = get_last_week_number(user_id) + 1
    planner = WeeklyPlanner()
    return planner.generate_plan(user_id, week_number)
