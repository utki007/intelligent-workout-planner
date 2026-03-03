"""
Knowledge Base & Reasoning (KRR) using pyDatalog.

Defines facts (exercises, target muscles, difficulty, user injuries) and rules
(forbidden, recommended). Supports querying exercises by muscle and filtering
by injuries, plus KRR reasoning text for display.
"""

from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path
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
    "deltoids": "delts",
    "trapezius": "traps",
    "quadriceps": "quads",
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


_INJURY_MAP_PATH = Path(__file__).resolve().parent.parent / "data" / "injury.json"


def _normalize_label(value: Any) -> str:
    s = str(value or "").strip().lower().replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", s)


def _parse_muscle_values(raw: Any) -> list[str]:
    """Parse DB muscle fields that may be plain strings or list-like strings."""
    if raw is None or pd.isna(raw):
        return []

    values: list[str] = []
    if isinstance(raw, (list, tuple, set)):
        values = [str(v) for v in raw]
    else:
        s = str(raw).strip()
        if not s:
            return []
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = ast.literal_eval(s)
                if isinstance(parsed, (list, tuple, set)):
                    values = [str(v) for v in parsed]
                else:
                    values = [s]
            except Exception:
                values = [s]
        else:
            values = [part for part in re.split(r"\s*,\s*", s) if part.strip()]

    out: list[str] = []
    for item in values:
        norm = _normalize_label(item).strip("'\"")
        if norm:
            out.append(MUSCLE_ALIASES.get(norm, norm))
    return out


def _load_injury_map_entries() -> list[dict[str, Any]]:
    """Load injury mapping rows from data/injury.json."""
    if not _INJURY_MAP_PATH.exists():
        return []
    try:
        payload = json.loads(_INJURY_MAP_PATH.read_text())
    except Exception:
        return []

    raw_entries = payload.get("muscle_groups", []) if isinstance(payload, dict) else []
    out: list[dict[str, Any]] = []
    for row in raw_entries:
        if not isinstance(row, dict):
            continue
        muscle = _normalize_label(row.get("name"))
        if not muscle:
            continue
        avoid_with = {
            _normalize_label(v)
            for v in (row.get("avoid_with") or [])
            if _normalize_label(v)
        }
        out.append({"muscle": MUSCLE_ALIASES.get(muscle, muscle), "avoid_with": avoid_with})
    return out


def _normalize_muscles(target: Any) -> list[str]:
    """Normalize target to a list of muscle names (lowercase, stripped)."""
    return _parse_muscle_values(target)


def normalize_injury_muscles(injury_muscles: list[str] | None) -> set[str]:
    """Expand user injury inputs to forbidden muscles via aliases, groups, and injury.json mapping."""
    injury_rows = _load_injury_map_entries()
    out: set[str] = set()
    inputs: set[str] = set()

    for raw in injury_muscles or []:
        norm = _normalize_label(raw)
        if not norm:
            continue
        inputs.add(norm)
        if norm in MUSCLE_GROUP_EXPANSIONS:
            out.update(MUSCLE_GROUP_EXPANSIONS[norm])
        mapped = MUSCLE_ALIASES.get(norm, norm)
        out.add(mapped)

    # Use injury map semantics: if user provides a condition in avoid_with
    # (e.g., knee/shoulder/lower_back), forbid all mapped muscle groups.
    for token in inputs:
        for row in injury_rows:
            muscle = row["muscle"]
            avoid_with = row["avoid_with"]
            if token == muscle or token in avoid_with:
                out.add(muscle)

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
        target_muscles = _normalize_muscles(row.get("target", ""))
        secondary_raw = row.get("secondaryMuscles", row.get("secondarymuscles", ""))
        secondary_muscles = _normalize_muscles(secondary_raw)
        muscles = list({m for m in (target_muscles + secondary_muscles) if m})
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
        "  (from exercise DB) is_exercise(E), trains_muscle(E, M) from target + secondary muscles, has_difficulty(E, D).",
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
        if day_theme_muscles:
            lines.append(
                "  Result: (none — recommend Rest Day because no injury-safe beginner exercises matched this day theme)."
            )
        else:
            lines.append("  Result: (none — Rest Day recommended by planner constraints).")
    return "\n".join(lines)
