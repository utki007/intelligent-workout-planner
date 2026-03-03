"""
Knowledge Base & Reasoning (KRR) using pyDatalog.

Defines facts (exercises, target muscles, difficulty, user injuries) and rules
(forbidden, recommended). Supports querying exercises by muscle and filtering
by injuries, plus KRR reasoning text for display.
"""

from __future__ import annotations

import os
from typing import Any

import pandas as pd
from pyDatalog import pyDatalog

# Declare predicates and variables for pyDatalog
pyDatalog.create_terms(
    "exercise_name, trains_muscle, has_difficulty, injured_muscle, "
    "forbidden, recommended, beginner_ok, is_exercise, "
    "User, Exercise, Muscle, Difficulty, E, M, U"
)


# Muscle name aliases: common names -> DB target names
MUSCLE_ALIASES = {
    "chest": "pectorals",
    "shoulders": "delts",
    "shoulder": "delts",
    "back": "lats",  # Also matches upper back, traps
    "legs": "quads",  # Also matches hamstrings, glutes, calves
    "arms": "biceps",  # Also matches triceps
    "arm": "biceps",
}

MUSCLE_GROUP_EXPANSIONS = {
    "back": ["lats", "upper back", "traps", "levator scapulae"],
    "legs": ["quads", "hamstrings", "glutes", "calves", "adductors", "abductors"],
    "arms": ["biceps", "triceps", "forearms"],
}

# Joint-level injury mapping used to project joint issues to muscle constraints.
JOINT_MUSCLE_MAP = {
    "shoulder": ["delts", "traps", "upper back", "levator scapulae"],
    "elbow": ["biceps", "triceps", "forearms"],
    "wrist": ["forearms"],
    "hip": ["glutes", "hamstrings", "adductors", "abductors"],
    "knee": ["quads", "hamstrings", "calves"],
    "ankle": ["calves"],
    "spine": ["upper back", "lats", "abs"],
}

JOINT_ALIASES = {
    "shoulders": "shoulder",
    "elbows": "elbow",
    "wrists": "wrist",
    "hips": "hip",
    "knees": "knee",
    "ankles": "ankle",
    "lower back": "spine",
}


def _normalize_muscles(target: Any) -> list[str]:
    """Normalize target to a list of muscle names (lowercase, stripped)."""
    if pd.isna(target):
        return []
    s = str(target).strip().lower()
    if not s:
        return []
    muscles = [m.strip().lower() for m in s.split(",")]
    # Map aliases to DB names
    return [MUSCLE_ALIASES.get(m, m) for m in muscles]


def normalize_injury_muscles(injury_muscles: list[str] | None) -> set[str]:
    """Expand aliases/groups to a canonical set used by KB and safety filters."""
    out: set[str] = set()
    for raw in injury_muscles or []:
        norm = str(raw).strip().lower()
        if not norm:
            continue
        norm = JOINT_ALIASES.get(norm, norm)
        if norm in JOINT_MUSCLE_MAP:
            out.update(JOINT_MUSCLE_MAP[norm])
            continue
        if norm in MUSCLE_GROUP_EXPANSIONS:
            out.update(MUSCLE_GROUP_EXPANSIONS[norm])
            continue
        mapped = MUSCLE_ALIASES.get(norm, norm)
        out.add(mapped)
    return out


def load_kb(exercises_df: pd.DataFrame, user_id: str, injury_muscles: list[str]) -> None:
    """
    Load the knowledge base: assert exercise facts and user injury facts,
    then apply rules. Clears the KB first so this can be called for different users.

    Args:
        exercises_df: DataFrame with columns 'name', 'target', 'difficulty'.
        user_id: User identifier.
        injury_muscles: List of muscle names the user has injured.
    """
    pyDatalog.clear()

    # Rules: define before asserting facts (so they apply to loaded data)
    # forbidden(User, Exercise) if exercise trains any muscle that user has injured
    forbidden(U, E) <= trains_muscle(E, M) & injured_muscle(U, M)
    # beginner_ok(E): all exercises that are in DB and marked beginner (no negation)
    beginner_ok(E) <= is_exercise(E) & has_difficulty(E, "beginner")

    # Facts: exercises (use .get so column name casing does not break)
    for _, row in exercises_df.iterrows():
        name = str(row.get("name", "") or "").strip()
        if not name:
            continue
        muscles = _normalize_muscles(row.get("target", ""))
        difficulty = str(row.get("difficulty", "beginner")).strip().lower()
        +is_exercise(name)
        +has_difficulty(name, difficulty)
        for m in muscles:
            +trains_muscle(name, m)

    # Facts: user injuries (normalize to lowercase and map aliases)
    for m in normalize_injury_muscles(injury_muscles):
        +injured_muscle(user_id, m)


def query_by_muscle(muscle: str) -> list[str]:
    """Return list of exercise names that target the given muscle."""
    muscle = str(muscle).strip().lower()
    result = trains_muscle(Exercise, muscle)
    try:
        data = result.data if hasattr(result, "data") else []
    except Exception:
        return []
    return list({ex[0] if isinstance(ex, tuple) else ex for ex in data})


def query_recommended(user_id: str) -> list[str]:
    """Return list of exercise names recommended for the user (beginner, not forbidden)."""
    # Query beginner exercises (no negation in Datalog for reliability), then exclude forbidden in Python
    result = beginner_ok(Exercise)
    try:
        data = result.data if hasattr(result, "data") else []
    except Exception:
        return []
    beginners = list({ex[0] if isinstance(ex, tuple) else ex for ex in data})
    forbidden_set = set(query_forbidden(user_id))
    return [e for e in beginners if e not in forbidden_set]


def query_forbidden(user_id: str) -> list[str]:
    """Return list of exercise names that are forbidden for the user."""
    result = forbidden(user_id, Exercise)
    try:
        data = result.data if hasattr(result, "data") else []
    except Exception:
        return []
    return list({ex[0] if isinstance(ex, tuple) else ex for ex in data})


def get_krr_reasoning(
    user_id: str,
    day_label: str,
    day_theme_muscles: list[str],
    recommended_for_day: list[str],
) -> str:
    """
    Build a short KRR reasoning string for display (CLI or Streamlit).

    Args:
        user_id: User identifier.
        day_label: e.g. 'Day1'.
        day_theme_muscles: Muscles for this day (e.g. ['chest', 'pectorals']).
        recommended_for_day: Exercise names recommended for this day (already filtered by theme).

    Returns:
        Multiline string with facts, rules, and inference summary.
    """
    lines = [
        "Facts:",
        "  (from exercise DB) is_exercise(E), trains_muscle(E, M), has_difficulty(E, D).",
        "  (from user) injured_muscle(User, M) for user's injury list.",
        "Rules:",
        "  forbidden(User, Exercise) <- trains_muscle(Exercise, M) & injured_muscle(User, M).",
        "  recommended(User, Exercise) <- is_exercise(Exercise) & has_difficulty(Exercise, beginner) & ~forbidden(User, Exercise).",
        f"Inference ({day_label} — theme: {', '.join(day_theme_muscles)}):",
        f"  Query: recommended({user_id}, E) where E targets one of {day_theme_muscles}.",
    ]
    if recommended_for_day:
        lines.append(f"  Result: {len(recommended_for_day)} exercise(s)")
        for ex in recommended_for_day[:10]:
            lines.append(f"    recommended({user_id}, \"{ex}\")")
        if len(recommended_for_day) > 10:
            lines.append(f"    ... and {len(recommended_for_day) - 10} more.")
    else:
        lines.append("  Result: (none — rest day or no matching beginner exercises after injury filter).")
    return "\n".join(lines)
