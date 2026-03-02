"""
Weekly plan generation: 5-day split, up to 6 exercises per day.
Uses KRR for recommended exercises (no injured muscles) and DecisionPolicy for set counts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from agent.agent_state import AgentGoals, AgentState
from agent.decision_policy import DecisionPolicy
from agent.krr import get_krr_reasoning, load_kb, query_recommended


SPLITS_PATH = Path(__file__).resolve().parent / "splits.json"


def load_splits() -> dict[str, list[dict[str, Any]]]:
    """
    Load split presets from planner/splits.json.

    Format:
      {
        "5": [ { "theme": "Chest", "muscles": ["pectorals"] }, ... ],
        "6": [ ... ]
      }
    """
    if not SPLITS_PATH.exists():
        raise FileNotFoundError(f"Missing split config: {SPLITS_PATH}")
    with open(SPLITS_PATH) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("splits.json must be a JSON object")
    return data


def get_split(days_per_week: int) -> list[tuple[str, str, list[str]]]:
    """
    Returns an ordered list of (day_label, theme_label, theme_muscles) for this user.
    Days are generated dynamically as Day1..DayN.
    """
    splits = load_splits()
    d = int(days_per_week)
    d = max(3, min(7, d))
    preset = splits.get(str(d)) or splits.get("5") or []
    out: list[tuple[str, str, list[str]]] = []
    for i, day_cfg in enumerate(preset, start=1):
        if not isinstance(day_cfg, dict):
            continue
        theme = str(day_cfg.get("theme", f"Day {i}")).strip() or f"Day {i}"
        muscles = day_cfg.get("muscles") or []
        muscles = [str(m).strip().lower() for m in muscles if str(m).strip()]
        out.append((f"Day{i}", theme, muscles))
    return out


def _exercise_targets(row: pd.Series) -> list[str]:
    """Return list of target muscles for an exercise (from DataFrame row)."""
    target = row.get("target")
    if pd.isna(target):
        return []
    s = str(target).strip().lower()
    if not s:
        return []
    return [m.strip().lower() for m in s.split(",")]


def _matches_theme(targets: list[str], theme_muscles: list[str]) -> bool:
    """True if any of the exercise's targets are in the day theme."""
    theme_set = set(m.strip().lower() for m in theme_muscles)
    return bool(theme_set & set(targets))


def generate_weekly_plan(
    user_id: str,
    user_profile: dict[str, Any],
    exercises_df: pd.DataFrame,
    state: AgentState,
    goals: AgentGoals,
    modifications: list[str] | None = None,
) -> tuple[dict[str, list[dict]], dict[str, str], dict[str, str], dict[str, list[str]]]:
    """
    Generate a plan with up to 6 exercises per day.
    No exercise targets an injured muscle (enforced via KRR).

    Args:
        user_id: User identifier.
        user_profile: Must contain 'injury_muscles' (list) and optionally 'experience_level'.
        exercises_df: DataFrame with name, target, difficulty.
        state: AgentState for prescription.
        goals: AgentGoals for caps.
        modifications: Optional list of suggestion strings from PlanEvaluation (e.g. reduce sets for X).

    Returns:
        (plan, krr_reasoning, day_themes, day_theme_muscles):
          - plan is {"Day1": [{"name", "sets", "reps", "target"}, ...], ...}
          - krr_reasoning is {"Day1": "...", ...} for display
          - day_themes is {"Day1": "Chest", ...} for UI display
          - day_theme_muscles is {"Day1": ["pectorals", ...], ...} from split config
    """
    injury_muscles = user_profile.get("injury_muscles") or []
    days_per_week = int(user_profile.get("days_per_week") or 5)
    split = get_split(days_per_week)
    day_themes: dict[str, str] = {day: theme for day, theme, _ in split}
    day_theme_muscles: dict[str, list[str]] = {day: theme_muscles for day, _theme, theme_muscles in split}
    load_kb(exercises_df, user_id, injury_muscles)
    recommended_all = query_recommended(user_id)
    policy = DecisionPolicy(state, goals)
    prescription = policy.get_prescription()

    # Build name -> row for target lookup (case-insensitive)
    name_to_row = {}
    name_lower_to_row = {}
    for _, row in exercises_df.iterrows():
        name = str(row.get("name", "")).strip()
        if name:
            name_to_row[name] = row
            name_lower_to_row[name.lower()] = row

    plan: dict[str, list[dict]] = {}
    krr_reasoning: dict[str, str] = {}

    for day, _theme_label, theme_muscles in split:
        # Filter recommended to this day's theme (from split config)
        candidates = []
        for name in recommended_all:
            # Try exact match first, then case-insensitive
            row = name_to_row.get(name)
            if row is None:
                row = name_lower_to_row.get(name.lower())
            if row is None:
                continue
            targets = _exercise_targets(row)
            if _matches_theme(targets, theme_muscles):
                candidates.append((name, targets[0] if targets else ""))

        # Pick up to 6 exercises per day. If no candidates (e.g. all forbidden), day is rest.
        chosen = candidates[:6]
        day_exercises: list[dict] = []
        for name, primary_target in chosen:
            sets = 3
            if primary_target and primary_target in prescription:
                sets = min(3, prescription[primary_target]["target_sets"] // 2)
            sets = max(2, sets)
            # Cap sets when many exercises to keep weekly volume within goals
            if len(chosen) > 4:
                sets = min(sets, 2)
            day_exercises.append({
                "name": name,
                "sets": sets,
                "reps": 12,
                "target": primary_target or theme_muscles[0],
            })
        plan[day] = day_exercises

        recommended_names = [ex["name"] for ex in day_exercises]
        krr_reasoning[day] = get_krr_reasoning(
            user_id, day, theme_muscles, recommended_names
        )

    return plan, krr_reasoning, day_themes, day_theme_muscles
