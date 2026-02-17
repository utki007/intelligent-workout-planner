"""
Knowledge Representation & Reasoning (KRR) using pyDatalog.

Facts:
- is_exercise(Exercise)
- trains_muscle(Exercise, Muscle)
- has_difficulty(Exercise, Difficulty)
- muscle_recovery(Muscle, RecoveryDays)
- injured_muscle(User, Muscle)  -- from user profile
- intensity_modifier(User, Level)  -- 1-5 from feedback

Rules:
- forbidden_exercise(User, Exercise) if exercise trains an injured muscle
- recommended_exercise(User, Exercise) if not forbidden AND difficulty is beginner
- intensity_modifier is updated based on feedback (handled in storage/planner)
"""

import os
import pandas as pd
from pyDatalog import pyDatalog
from typing import List, Optional

# Terms used in rules (must be created before loading rules)
pyDatalog.create_terms(
    "Exercise, Muscle, User, Difficulty, RecoveryDays, Level"
)
pyDatalog.create_terms(
    "is_exercise, trains_muscle, has_difficulty, muscle_recovery"
)
pyDatalog.create_terms(
    "injured_muscle, forbidden_exercise, recommended_exercise, intensity_modifier"
)

# Rules: forbidden if trains injured muscle; recommended if beginner and not forbidden
forbidden_exercise(User, Exercise) <= trains_muscle(Exercise, Muscle) & injured_muscle(User, Muscle)  # type: ignore
recommended_exercise(User, Exercise) <= is_exercise(Exercise) & has_difficulty(Exercise, 'beginner') & ~forbidden_exercise(User, Exercise)  # type: ignore


def _muscle_to_readable(muscle_or_focus: str) -> str:
    """Turn 'chest' / 'Day 1 — chest' into readable 'chest' or 'shoulders' etc."""
    s = (muscle_or_focus or "").strip().lower()
    # Handle "Day 1 — chest" style
    if "—" in s:
        s = s.split("—", 1)[-1].strip()
    if not s:
        return "this muscle group"
    # Already one word or two (e.g. "upper back")
    return s


# Muscle recovery days (defaults when not in Excel)
DEFAULT_RECOVERY_DAYS = {
    "glutes": 2, "quadriceps": 2, "hamstrings": 2,
    "pectorals": 1, "delts": 1, "traps": 1, "lats": 1, "biceps": 1, "triceps": 1,
    "calves": 1, "forearms": 0, "abs": 0,
}


class KnowledgeBase:
    """Load exercises and user injuries into pyDatalog; query recommended exercises."""

    def __init__(
        self,
        exercises_path: str = "data/exercises_db.xlsx",
        recovery_map: Optional[dict] = None,
    ):
        self.exercises_path = exercises_path
        self.recovery_map = recovery_map or DEFAULT_RECOVERY_DAYS
        self._df: Optional[pd.DataFrame] = None
        self._last_injury_list: List[str] = []

    def clear(self) -> None:
        """Clear pyDatalog state (e.g. before reloading)."""
        pyDatalog.clear()
        pyDatalog.create_terms(
            "Exercise, Muscle, User, Difficulty, RecoveryDays, Level"
        )
        pyDatalog.create_terms(
            "is_exercise, trains_muscle, has_difficulty, muscle_recovery"
        )
        pyDatalog.create_terms(
            "injured_muscle, forbidden_exercise, recommended_exercise, intensity_modifier"
        )
        forbidden_exercise(User, Exercise) <= trains_muscle(Exercise, Muscle) & injured_muscle(User, Muscle)  # type: ignore
        recommended_exercise(User, Exercise) <= is_exercise(Exercise) & has_difficulty(Exercise, 'beginner') & ~forbidden_exercise(User, Exercise)  # type: ignore

    def load_exercises(self) -> pd.DataFrame:
        """Load exercise DB and assert is_exercise, trains_muscle, has_difficulty, muscle_recovery."""
        if not os.path.exists(self.exercises_path):
            raise FileNotFoundError(f"Exercise database not found: {self.exercises_path}")
        df = pd.read_excel(self.exercises_path, engine="openpyxl")
        self._df = df

        for _, row in df.iterrows():
            name = str(row.get("name", "")).strip()
            if not name:
                continue
            # Fact: is_exercise(Exercise)
            pyDatalog.assert_fact("is_exercise", name)

            target = row.get("target")
            if pd.notna(target) and str(target).strip():
                muscle = str(target).strip().lower()
                pyDatalog.assert_fact("trains_muscle", name, muscle)
            sec = row.get("secondaryMuscles")
            if sec is not None:
                if isinstance(sec, list):
                    for m in sec:
                        if m and str(m).strip():
                            pyDatalog.assert_fact("trains_muscle", name, str(m).strip().lower())
                else:
                    for part in str(sec).split(","):
                        if part.strip():
                            pyDatalog.assert_fact("trains_muscle", name, part.strip().lower())

            diff = row.get("difficulty")
            if pd.notna(diff) and str(diff).strip():
                pyDatalog.assert_fact("has_difficulty", name, str(diff).strip().lower())

        for muscle, days in self.recovery_map.items():
            pyDatalog.assert_fact("muscle_recovery", muscle.lower(), days)

        return df

    def set_user_injuries(self, user_id: str, injury_muscles: List[str]) -> None:
        """Assert injured_muscle(User, Muscle) for each injured muscle."""
        self._last_injury_list = [str(m).strip().lower() for m in injury_muscles if str(m).strip()]
        for muscle in self._last_injury_list:
            pyDatalog.assert_fact("injured_muscle", user_id, muscle)

    def get_recommended_exercises(self, user_id: str, limit: Optional[int] = None) -> List[str]:
        """Query: recommended_exercise(User, Exercise). Returns list of exercise names."""
        try:
            result = recommended_exercise(user_id, Exercise)  # type: ignore
            names = [r[0] if isinstance(r, tuple) else r for r in result]
            if limit is not None:
                names = names[:limit]
            return names
        except Exception:
            return []

    def get_exercise_dataframe(self) -> pd.DataFrame:
        """Return the loaded exercise DataFrame."""
        if self._df is None:
            self.load_exercises()
        return self._df

    def explain_exercise(self, user_id: str, exercise_name: str) -> dict:
        """
        Plain-language explanation: why an exercise is avoided or recommended.
        Returns {"status": "forbidden"|"recommended", "reason": "user-friendly sentence"}.
        """
        try:
            forbidden_result = forbidden_exercise(user_id, exercise_name)  # type: ignore
            if forbidden_result:
                injured = getattr(self, "_last_injury_list", [])
                muscles_trained = []
                try:
                    for m in self.recovery_map:
                        train_res = trains_muscle(exercise_name, m)  # type: ignore
                        if train_res:
                            muscles_trained.append(m)
                except Exception:
                    pass
                overlap = [m for m in muscles_trained if m in injured] or injured[:1] or ["that area"]
                muscle = _muscle_to_readable(overlap[0]) if overlap else "that area"
                return {
                    "status": "forbidden",
                    "reason": "We're skipping this exercise because it targets your {}, which you're protecting.".format(muscle),
                }
        except Exception:
            pass
        return {
            "status": "recommended",
            "reason": "This exercise fits your level and doesn't stress any areas you've marked as injured.",
        }

    def get_krr_reasoning_for_day(
        self,
        user_id: str,
        day: int,
        day_focus: str,
        theme_keywords: List[str],
        exercise_names: List[str],
    ) -> List[str]:
        """
        Build detailed KRR-style reasoning for one day:
        - Facts (injuries)
        - Rules (forbidden / recommended)
        - Inference with concrete examples (why exercises are chosen or all forbidden)
        """
        injury_list = getattr(self, "_last_injury_list", [])
        lines: List[str] = []

        # Facts (user profile)
        lines.append("Facts:")
        if injury_list:
            for m in injury_list:
                lines.append("  injured_muscle({}, {})".format(user_id, m))
        else:
            lines.append("  (no injured muscles)")
        lines.append("")

        # Rules
        lines.append("Rules:")
        lines.append("  forbidden(U, E) ← trains_muscle(E, M) ∧ injured_muscle(U, M)")
        lines.append("  recommended(U, E) ← is_exercise(E) ∧ difficulty(E, beginner) ∧ ¬forbidden(U, E)")
        lines.append("")

        # Inference for this day
        lines.append("Inference (Day {} — {}):".format(day, day_focus))
        lines.append("  Query: recommended({}, E) where E matches theme {}".format(user_id, theme_keywords))

        # Try to look up extra details from the exercise DataFrame
        try:
            df = self.get_exercise_dataframe()
        except Exception:
            df = None

        if not exercise_names:
            lines.append("  Result: ∅")
            lines.append("  ⇒ All exercises for this theme are forbidden (train injured muscles).")

            # Give one concrete forbidden example if possible
            if df is not None and theme_keywords:
                try:
                    example_line_added = False
                    for _, row in df.iterrows():
                        name = str(row.get("name", "")).strip()
                        if not name:
                            continue
                        target = str(row.get("target", "")).strip().lower()
                        if not any(kw.lower() in target for kw in theme_keywords):
                            continue
                        try:
                            forb_res = forbidden_exercise(user_id, name)  # type: ignore
                        except Exception:
                            forb_res = ()
                        if forb_res:
                            # Try to match which injured muscle explains the forbiddance
                            offending = ""
                            for inj in injury_list:
                                if inj in target:
                                    offending = inj
                                    break
                            offending = offending or (target or "injured area")
                            lines.append(
                                "  Example: forbidden({}, \"{}\") because trains_muscle({}, {}) ∧ injured_muscle({}, {}).".format(
                                    user_id, name, name, offending, user_id, offending
                                )
                            )
                            example_line_added = True
                            break
                    if not example_line_added and injury_list:
                        lines.append(
                            "  (No specific example found in DB, but any exercise that trains {} would be forbidden.)".format(
                                ", ".join(injury_list)
                            )
                        )
                except Exception:
                    pass
        else:
            lines.append("  Result: {} exercise(s)".format(len(exercise_names)))
            # For a few selected exercises, explain *why* they are recommended
            for en in exercise_names[:4]:
                target = ""
                diff = "beginner"
                if df is not None:
                    try:
                        row = df[df["name"].astype(str).str.strip().str.lower() == str(en).strip().lower()]
                        if not row.empty:
                            r0 = row.iloc[0]
                            target = str(r0.get("target", "")).strip().lower()
                            d = str(r0.get("difficulty", "")).strip().lower()
                            if d:
                                diff = d
                    except Exception:
                        pass

                # Build a compact logical justification
                if target:
                    if injury_list and any(inj in target for inj in injury_list):
                        # This *shouldn't* happen for recommended exercises, but handle defensively.
                        # recommended because beginner, but NOTE: target overlaps injuries (data inconsistency)
                        reason = (
                            "is_exercise({e}) ∧ difficulty({e}, {d}) ∧ trains_muscle({e}, {m}) ∧ injured_muscle({u}, {m}) (should be forbidden)."
                        ).format(e=en, d=diff, m=target, u=user_id)
                    else:
                        reason = (
                            "is_exercise({e}) ∧ difficulty({e}, {d}) ∧ trains_muscle({e}, {m}) ∧ ¬injured_muscle({u}, {m})"
                        ).format(e=en, d=diff, m=target or "target", u=user_id)
                else:
                    reason = "is_exercise({e}) ∧ difficulty({e}, {d}) ∧ ¬forbidden({u}, {e})".format(
                        e=en, d=diff, u=user_id
                    )

                lines.append('    ✓ recommended({}, "{}") because {}'.format(user_id, en, reason))

            if len(exercise_names) > 4:
                lines.append("    ... +{} more".format(len(exercise_names) - 4))

        return lines
