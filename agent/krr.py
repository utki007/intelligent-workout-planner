"""
Knowledge Base & Reasoning (KRR) using pyDatalog.

Defines facts (exercises, target muscles, difficulty, user injuries) and rules
(forbidden, equipment_allowed, recommended). Supports querying exercises by
muscle and filtering by injuries/equipment, plus KRR reasoning text for display.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd
from pyDatalog import pyDatalog

# Declare predicates and variables for pyDatalog
pyDatalog.create_terms(
    "exercise_name, trains_muscle, has_difficulty, injured_muscle, "
    "uses_equipment, preferred_equipment, equipment_allowed, "
    "forbidden, recommended, beginner_ok, is_exercise, "
    "User, Exercise, Muscle, Difficulty, Eq, E, M, U"
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

EQUIPMENT_ALIASES = {
    "bodyweight": "body weight",
    "body-weight": "body weight",
    "dumbbells": "dumbbell",
    "barbells": "barbell",
    "kettlebells": "kettlebell",
    "bands": "band",
    "resistance bands": "resistance band",
    "cables": "cable",
    "machines": "leverage machine",
    "any": "",
    "all": "",
    "no preference": "",
}


def _normalize_equipment_tokens(value: Any) -> list[str]:
    """Normalize DB/free-text equipment fields to canonical lowercase tokens."""
    if value is None or pd.isna(value):
        return []
    text = str(value).strip().lower()
    if not text:
        return []

    # Drop parenthetical hints then split common compound separators.
    text = re.sub(r"\([^)]*\)", "", text)
    text = text.replace("&", ",").replace("/", ",")
    text = re.sub(r"\s+and\s+", ",", text)
    text = re.sub(r"\s+with\s+", ",", text)

    out: list[str] = []
    seen: set[str] = set()
    for raw in text.split(","):
        token = str(raw).strip().lower()
        if not token:
            continue
        canonical = EQUIPMENT_ALIASES.get(token, token)
        if canonical and canonical not in seen:
            seen.add(canonical)
            out.append(canonical)
    return out


def normalize_equipment_preferences(preferred_equipment: list[str] | None) -> set[str]:
    """Return canonical equipment preferences used by KB filtering."""
    out: set[str] = set()
    for raw in preferred_equipment or []:
        for token in _normalize_equipment_tokens(raw):
            out.add(token)
    return out


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


def load_kb(
    exercises_df: pd.DataFrame,
    user_id: str,
    injury_muscles: list[str],
    equipment_preferences: list[str] | None = None,
) -> None:
    """
    Load the knowledge base: assert exercise facts and user injury facts,
    then apply rules. Clears the KB first so this can be called for different users.

    Args:
        exercises_df: DataFrame with columns 'name', 'target', 'difficulty', 'equipment'.
        user_id: User identifier.
        injury_muscles: List of muscle names the user has injured.
        equipment_preferences: Optional preferred equipment list (e.g., ["dumbbell"]).
    """
    pyDatalog.clear()

    # Rules: define before asserting facts (so they apply to loaded data)
    # forbidden(User, Exercise) if exercise trains any muscle that user has injured
    forbidden(U, E) <= trains_muscle(E, M) & injured_muscle(U, M)
    # equipment_allowed(User, Exercise) if exercise uses one of the user's preferred equipment
    equipment_allowed(U, E) <= preferred_equipment(U, Eq) & uses_equipment(E, Eq)
    # Bodyweight movements are always allowed even when equipment preferences are set
    equipment_allowed(U, E) <= uses_equipment(E, "body weight")
    # beginner_ok(E): all exercises that are in DB and marked beginner (no negation)
    beginner_ok(E) <= is_exercise(E) & has_difficulty(E, "beginner")

    # Facts: exercises (use .get so column name casing does not break)
    for _, row in exercises_df.iterrows():
        name = str(row.get("name", "") or "").strip()
        if not name:
            continue
        muscles = _normalize_muscles(row.get("target", ""))
        equipment_tokens = _normalize_equipment_tokens(row.get("equipment", ""))
        difficulty = str(row.get("difficulty", "beginner")).strip().lower()
        +is_exercise(name)
        +has_difficulty(name, difficulty)
        for m in muscles:
            +trains_muscle(name, m)
        for eq in equipment_tokens:
            +uses_equipment(name, eq)

    # Facts: user injuries (normalize to lowercase and map aliases)
    for m in normalize_injury_muscles(injury_muscles):
        +injured_muscle(user_id, m)
    # Facts: optional user equipment preferences
    for eq in normalize_equipment_preferences(equipment_preferences):
        +preferred_equipment(user_id, eq)


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
    injury_filtered = [e for e in beginners if e not in forbidden_set]
    preferred_eq = set(query_preferred_equipment(user_id))
    if not preferred_eq:
        return injury_filtered
    allowed_by_equipment = set(query_equipment_allowed(user_id))
    return [e for e in injury_filtered if e in allowed_by_equipment]


def query_forbidden(user_id: str) -> list[str]:
    """Return list of exercise names that are forbidden for the user."""
    result = forbidden(user_id, Exercise)
    try:
        data = result.data if hasattr(result, "data") else []
    except Exception:
        return []
    return list({ex[0] if isinstance(ex, tuple) else ex for ex in data})


def query_preferred_equipment(user_id: str) -> list[str]:
    """Return canonical preferred equipment configured for the user in KB facts."""
    result = preferred_equipment(user_id, Eq)
    try:
        data = result.data if hasattr(result, "data") else []
    except Exception:
        return []
    return list({eq[0] if isinstance(eq, tuple) else eq for eq in data})


def query_equipment_allowed(user_id: str) -> list[str]:
    """Return exercises that satisfy equipment_allowed(User, Exercise)."""
    result = equipment_allowed(user_id, Exercise)
    try:
        data = result.data if hasattr(result, "data") else []
    except Exception:
        return []
    return list({ex[0] if isinstance(ex, tuple) else ex for ex in data})


KRR_LOGIC_VERSION = "1.0"

CONSTRAINT_PIPELINE = [
    "injury_filter",
    "equipment_filter",
    "level_filter",
    "theme_selection",
]


def get_krr_logic_summary(
    user_id: str,
    injury_muscles: list[str],
    equipment_preferences: list[str] | None = None,
    day_label: str | None = None,
    day_theme_muscles: list[str] | None = None,
) -> dict[str, Any]:
    """
    Build structured KRR logic summary from actual pyDatalog queries.

    Requires KB to be loaded via load_kb() for the user. Returns structured data
    only; frontend owns formatting. No "formatted" string.

    Args:
        user_id: User identifier.
        injury_muscles: Raw injury list from profile (e.g. ["shoulder", "knee"]).
        equipment_preferences: Raw equipment list from profile (e.g. ["dumbbell"]).
        day_label: Optional day label (e.g. "Day1") for theme-injury conflict check.
        day_theme_muscles: Optional muscles for this day (e.g. ["chest", "pectorals"]).

    Returns:
        Dict with: injuries, equipment, constraint_pipeline, forbidden_exercises,
        forbidden_count, allowed_exercises_count, candidate_pool_size_after_constraints,
        how_avoidance, theme_adjusted_due_to_injury, krr_logic_version.
    """
    injuries_set = normalize_injury_muscles(injury_muscles)
    injuries = sorted(injuries_set)
    equipment_set = normalize_equipment_preferences(equipment_preferences)
    equipment = sorted(equipment_set) if equipment_set else []

    forbidden_exercises = query_forbidden(user_id)
    forbidden_count = len(forbidden_exercises)
    allowed = query_recommended(user_id)
    allowed_exercises_count = len(allowed)
    candidate_pool_size_after_constraints = allowed_exercises_count

    # Build how_avoidance from real counts
    if forbidden_count > 0 and injuries:
        injury_str = ", ".join(injuries[:5])
        if len(injuries) > 5:
            injury_str += ", ..."
        how_avoidance = (
            f"{forbidden_count} exercise(s) were excluded because they target "
            f"your injured muscle(s): {injury_str}. "
        )
    else:
        how_avoidance = "No injury-based exclusions applied. "

    if equipment:
        how_avoidance += (
            f"Only beginner exercises using your equipment ({', '.join(equipment)}) "
            "or body weight were recommended."
        )
    else:
        how_avoidance += (
            "Only beginner exercises were recommended "
            "(no equipment preference filter applied)."
        )

    # Theme-injury conflict: day theme overlaps with injured muscles
    theme_adjusted_due_to_injury = False
    if day_theme_muscles and injuries_set:
        theme_lower = {str(m).strip().lower() for m in day_theme_muscles if m}
        if theme_lower & injuries_set:
            theme_adjusted_due_to_injury = True

    return {
        "injuries": injuries,
        "equipment": equipment if equipment else [],  # empty list, not ["any"]
        "constraint_pipeline": list(CONSTRAINT_PIPELINE),
        "forbidden_exercises": forbidden_exercises,
        "forbidden_count": forbidden_count,
        "allowed_exercises_count": allowed_exercises_count,
        "candidate_pool_size_after_constraints": candidate_pool_size_after_constraints,
        "how_avoidance": how_avoidance.strip(),
        "theme_adjusted_due_to_injury": theme_adjusted_due_to_injury,
        "krr_logic_version": KRR_LOGIC_VERSION,
    }


def build_global_krr_reasoning(
    user_id: str,
    injury_muscles: list[str],
    equipment_preferences: list[str] | None,
    krr_logic: dict[str, Any] | None,
) -> str:
    """
    Build a global KRR-style reasoning trace for the user's constraints.

    This is intentionally \"symbolic\" in flavor: it restates the facts,
    rules, and inference steps that the pyDatalog program applies, with
    counts taken from the structured krr_logic summary.
    """
    injuries_norm = sorted(normalize_injury_muscles(injury_muscles))
    equipment_norm = sorted(normalize_equipment_preferences(equipment_preferences))

    injuries_line = (
        f"{user_id} has injured_muscle({user_id}, M) for M in {{{', '.join(injuries_norm)}}}"
        if injuries_norm
        else f"{user_id} has no injured_muscle facts (no injury-based filter)."
    )
    if equipment_norm:
        equipment_line = (
            f"{user_id} has preferred_equipment({user_id}, Eq) for Eq in "
            f"{{{', '.join(equipment_norm)}}}."
        )
    else:
        equipment_line = (
            f"{user_id} has no preferred_equipment facts (equipment filter disabled; "
            "body weight always allowed)."
        )

    forbidden_count = int(krr_logic.get("forbidden_count", 0)) if krr_logic else 0
    allowed_count = int(krr_logic.get("allowed_exercises_count", 0)) if krr_logic else 0
    pipeline = krr_logic.get("constraint_pipeline", list(CONSTRAINT_PIPELINE)) if krr_logic else list(
        CONSTRAINT_PIPELINE
    )
    how_avoidance = str(krr_logic.get("how_avoidance", "")).strip() if krr_logic else ""

    lines: list[str] = [
        "Facts:",
        "  (from exercise DB) is_exercise(E), trains_muscle(E, M), has_difficulty(E, D), uses_equipment(E, Eq).",
        f"  (from user) {injuries_line}",
        f"  (from user) {equipment_line}",
        "",
        "Rules:",
        "  forbidden(User, Exercise) <- trains_muscle(Exercise, M) & injured_muscle(User, M).",
        "  equipment_allowed(User, Exercise) <- preferred_equipment(User, Eq) & uses_equipment(Exercise, Eq).",
        "  equipment_allowed(User, Exercise) <- uses_equipment(Exercise, body weight).",
        "  recommended(User, Exercise) <- is_exercise(Exercise) & has_difficulty(Exercise, beginner)",
        "    & ~forbidden(User, Exercise) and (no preferred equipment OR equipment_allowed(User, Exercise)).",
        "",
        "Inference (global constraints):",
        f"  Constraint pipeline: {' -> '.join(pipeline)}.",
        f"  forbidden_count = {forbidden_count} exercise(s) excluded due to injury filter.",
        f"  allowed_exercises_count = {allowed_count} beginner exercise(s) remain after applying all constraints.",
    ]
    if how_avoidance:
        lines.append(f"  Narrative summary: {how_avoidance}")
    return "\n".join(lines)


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
    preferred_eq = query_preferred_equipment(user_id)
    lines = [
        "Facts:",
        "  (from exercise DB) is_exercise(E), trains_muscle(E, M), has_difficulty(E, D), uses_equipment(E, Eq).",
        "  (from user) injured_muscle(User, M) for user's injury list.",
        "  (from user) preferred_equipment(User, Eq) for optional equipment constraints.",
        "Rules:",
        "  forbidden(User, Exercise) <- trains_muscle(Exercise, M) & injured_muscle(User, M).",
        "  equipment_allowed(User, Exercise) <- preferred_equipment(User, Eq) & uses_equipment(Exercise, Eq).",
        "  equipment_allowed(User, Exercise) <- uses_equipment(Exercise, body weight).",
        "  recommended(User, Exercise) <- is_exercise(Exercise) & has_difficulty(Exercise, beginner) & ~forbidden(User, Exercise)"
        " and (no preferred equipment OR equipment_allowed(User, Exercise)).",
        f"Inference ({day_label} — theme: {', '.join(day_theme_muscles)}):",
        f"  Query: recommended({user_id}, E) where E targets one of {day_theme_muscles}.",
    ]
    if preferred_eq:
        lines.append(f"  User preferred equipment: {', '.join(sorted(preferred_eq))}.")
    else:
        lines.append("  User preferred equipment: (none).")
    if recommended_for_day:
        lines.append(f"  Result: {len(recommended_for_day)} exercise(s)")
        for ex in recommended_for_day[:10]:
            lines.append(f"    recommended({user_id}, \"{ex}\")")
        if len(recommended_for_day) > 10:
            lines.append(f"    ... and {len(recommended_for_day) - 10} more.")
    else:
        lines.append("  Result: (none — rest day or no matching beginner exercises after injury filter).")
    return "\n".join(lines)


def build_symbolic_summary(krr_logic: dict[str, Any] | None) -> list[str]:
  """
  Build a compact, symbolic summary from structured KRR logic.

  Returns a list of bullet-ready strings describing injuries, equipment,
  difficulty, and safe pool size. Caller is responsible for formatting.
  """
  if not isinstance(krr_logic, dict) or not krr_logic:
      return []

  injuries = krr_logic.get("injuries") or []
  equipment = krr_logic.get("equipment") or []
  pool_size = krr_logic.get("candidate_pool_size_after_constraints")
  forbidden_count = krr_logic.get("forbidden_count")

  bullets: list[str] = []
  if injuries:
      bullets.append(
          "Injuries: " + ", ".join(str(m).strip() for m in injuries if str(m).strip())
      )
  else:
      bullets.append("Injuries: No injuries blocking exercises")

  if equipment:
      bullets.append(
          "Equipment: " + ", ".join(str(eq).strip() for eq in equipment if str(eq).strip())
      )
  else:
      bullets.append("Equipment: Any available equipment")

  bullets.append("Difficulty: Beginner-focused filter applied")

  if pool_size is not None:
      bullets.append(f"Safe pool: {int(pool_size)} exercises after filters")
  elif forbidden_count is not None and krr_logic.get("allowed_exercises_count") is not None:
      bullets.append(
          f"Safe pool: {int(krr_logic['allowed_exercises_count'])} exercises after filters "
          f"({int(forbidden_count)} exercise(s) excluded)"
      )

  return bullets
