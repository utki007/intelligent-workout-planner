"""
Workout Recommender Module

This module provides intelligent workout recommendations using pyDatalog
knowledge base reasoning. It filters exercises based on injuries and muscle recovery.
"""

import os
import pandas as pd
from pyDatalog import pyDatalog
from typing import List, Optional, Dict

# ---------------------------------------------------------------------------
# pyDatalog terms must be declared at module (global) scope for queries.
# Rules are re-defined after each clear() via load() in _initialize_knowledge_base.
# ---------------------------------------------------------------------------
pyDatalog.create_terms(
    "Exercise, Muscle, DifficultyLevel, SecondaryMuscles, BreakDays"
)
pyDatalog.create_terms(
    "is_exercise, trains_muscle, has_difficulty"
)
pyDatalog.create_terms(
    "muscle_recovery, injured_muscle, forbidden_exercise, "
    "recommended_exercise, on_cooldown"
)

# Rule definitions as string (re-loaded after clear() so rules persist)
_RULES = """
forbidden_exercise(Exercise) <= trains_muscle(Exercise, Muscle) & injured_muscle(Muscle)
forbidden_exercise(Exercise) <= trains_muscle(Exercise, Muscle) & on_cooldown(Muscle)
recommended_exercise(Exercise) <= is_exercise(Exercise) & ~forbidden_exercise(Exercise)
"""


def normalize_text(value) -> Optional[str]:
    """Normalize text fields for consistency."""
    if pd.isna(value):
        return None
    return str(value).strip().lower().replace(" ", "_")


class WorkoutRecommender:
    """Intelligent workout recommender using knowledge base reasoning."""
    
    # Muscle recovery times (break days)
    MUSCLE_RECOVERY_TIMES = {
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
    
    def __init__(self, 
                 data_file: str = "data/exercises_db.xlsx",
                 injured_muscles: Optional[List[str]] = None,
                 muscles_on_cooldown: Optional[List[str]] = None):
        """
        Initialize the workout recommender.
        
        Args:
            data_file: Path to the exercise database Excel file
            injured_muscles: List of injured muscle names (normalized)
            muscles_on_cooldown: List of muscles currently on recovery cooldown
        """
        self.data_file = data_file
        self.injured_muscles = injured_muscles or []
        self.muscles_on_cooldown = muscles_on_cooldown or []
        self.exercise_dataframe = None
        self._initialize_knowledge_base()
    
    def _load_exercise_data(self) -> pd.DataFrame:
        """Load exercise data from Excel or JSON file."""
        import json as _json

        def _json_path(path: str) -> str:
            return path.replace(".xlsx", ".json").replace("exercises_db.xlsx", "exercises_db.json")

        if not os.path.exists(self.data_file):
            json_path = _json_path(self.data_file)
            if json_path != self.data_file and os.path.exists(json_path):
                self.data_file = json_path
            else:
                raise FileNotFoundError(
                    f"Exercise database file not found: {self.data_file}\n"
                    f"Please ensure the file exists or run the data fetching script first."
                )

        try:
            if self.data_file.endswith(".json"):
                with open(self.data_file, "r", encoding="utf-8") as f:
                    data = _json.load(f)
                if not data:
                    raise ValueError(f"Exercise database file is empty: {self.data_file}")
                df = pd.DataFrame(data)
                if "name" not in df.columns:
                    raise ValueError(f"JSON must contain 'name' column: {self.data_file}")
            else:
                try:
                    df = pd.read_excel(self.data_file, engine="openpyxl", index_col=0)
                except ImportError:
                    # Fallback to JSON when openpyxl is not installed
                    json_path = _json_path(self.data_file)
                    if os.path.exists(json_path):
                        self.data_file = json_path
                        with open(self.data_file, "r", encoding="utf-8") as f:
                            data = _json.load(f)
                        df = pd.DataFrame(data)
                    else:
                        raise RuntimeError(
                            "Reading Excel requires 'openpyxl'. Install with: pip install openpyxl\n"
                            f"Or provide a JSON file: {json_path}"
                        )
                if df.empty:
                    raise ValueError(f"Exercise database file is empty: {self.data_file}")
            
            # Normalize text fields
            df["name"] = df["name"].apply(normalize_text)
            df["target"] = df["target"].apply(normalize_text)
            df["difficulty"] = df["difficulty"].apply(normalize_text)
            
            return df
        except FileNotFoundError:
            raise
        except Exception as e:
            raise RuntimeError(f"Failed to load exercise data: {e}")
    
    def _initialize_knowledge_base(self):
        """Initialize the pyDatalog knowledge base with exercise data."""
        try:
            # Clear previous state
            pyDatalog.clear()
            # Re-define rules (clear() removes them)
            pyDatalog.load(_RULES)
            
            # Add muscle recovery facts via assert_fact (works inside class)
            for muscle, break_days in self.MUSCLE_RECOVERY_TIMES.items():
                pyDatalog.assert_fact("muscle_recovery", muscle, break_days)
            
            # Load exercise data
            self.exercise_dataframe = self._load_exercise_data()
            
            # Convert data rows to logical facts
            for _, row in self.exercise_dataframe.iterrows():
                exercise_name = row.get("name")
                target_muscle = row.get("target")
                secondary_muscles = row.get("secondaryMuscles")
                difficulty_level = row.get("difficulty")
                
                if exercise_name:
                    pyDatalog.assert_fact("is_exercise", exercise_name)
                    
                    if target_muscle:
                        pyDatalog.assert_fact("trains_muscle", exercise_name, target_muscle)
                    
                    if difficulty_level:
                        pyDatalog.assert_fact("has_difficulty", exercise_name, difficulty_level)
                    
                    if secondary_muscles:
                        parts = (
                            list(secondary_muscles)
                            if isinstance(secondary_muscles, list)
                            else str(secondary_muscles).split(", ")
                        )
                        for secondary_muscle in parts:
                            normalized_secondary = normalize_text(secondary_muscle)
                            if normalized_secondary:
                                pyDatalog.assert_fact("trains_muscle", exercise_name, normalized_secondary)
            
            # Add user injury information
            for muscle in self.injured_muscles:
                normalized_injury = normalize_text(muscle)
                if normalized_injury:
                    pyDatalog.assert_fact("injured_muscle", normalized_injury)
            
            for muscle in self.muscles_on_cooldown:
                normalized_cooldown = normalize_text(muscle)
                if normalized_cooldown:
                    pyDatalog.assert_fact("on_cooldown", normalized_cooldown)
            
        except Exception as e:
            raise RuntimeError(f"Failed to initialize knowledge base: {e}")
    
    def get_recommended_exercises(self, limit: Optional[int] = None) -> List[str]:
        """
        Get recommended safe exercises.
        
        Args:
            limit: Maximum number of exercises to return (None = all)
        
        Returns:
            List of recommended exercise names
        """
        try:
            safe_exercise_results = recommended_exercise(Exercise)  # type: ignore
            exercises = [result[0] for result in safe_exercise_results]
            
            if limit is not None:
                exercises = exercises[:limit]
            
            return exercises
        except Exception as e:
            raise RuntimeError(f"Failed to query recommended exercises: {e}")
    
    def get_exercise_count(self) -> int:
        """Get total count of recommended exercises."""
        try:
            safe_exercise_results = recommended_exercise(Exercise)  # type: ignore
            return len(safe_exercise_results)
        except Exception as e:
            raise RuntimeError(f"Failed to get exercise count: {e}")
    
    def get_exercise_dataframe(self) -> pd.DataFrame:
        """Return the loaded exercise dataframe (from KB data source)."""
        if self.exercise_dataframe is None:
            raise RuntimeError("Exercise dataframe not loaded")
        return self.exercise_dataframe.copy()
    
    def get_recommended_beginner_exercises(self) -> List[str]:
        """
        Return exercise names that are both recommended (not forbidden) and beginner.
        Uses KB for recommended_exercise and dataframe for has_difficulty(Exercise, 'beginner').
        """
        recommended = self.get_recommended_exercises()
        df = self.get_exercise_dataframe()
        # Filter to rows that are recommended and have difficulty beginner
        rec_set = set(recommended)
        beginner_df = df[(df["name"].isin(rec_set)) & (df["difficulty"] == "beginner")]
        return sorted(beginner_df["name"].unique().tolist())
    
    def print_recommendations(self, limit: int = 10):
        """
        Print recommended exercises to console.
        
        Args:
            limit: Number of exercises to display
        """
        try:
            exercises = self.get_recommended_exercises(limit=limit)
            total_count = self.get_exercise_count()
            
            print(f"\n{'='*60}")
            print(f"RECOMMENDED SAFE EXERCISES")
            print(f"{'='*60}")
            print(f"\nShowing top {limit} of {total_count} recommended exercises:\n")
            
            for i, exercise in enumerate(exercises, 1):
                print(f"{i:3d}. {exercise}")
            
            print(f"\nTotal safe exercises available: {total_count}")
            print(f"{'='*60}\n")
            
        except Exception as e:
            print(f"\n✗ Error getting recommendations: {e}")
            raise


