"""
MCP (meta-control) layer: orchestrates the agentic planning cycle.

Flow: read memory (AgentState) -> apply goals (AgentGoals) -> adjust plan (weekly_planner)
-> evaluate (PlanEvaluation) -> optionally re-adjust if overtraining.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from .agent_state import AgentGoals, AgentState
from .decision_policy import DecisionPolicy, PlanEvaluation
from .krr import normalize_injury_muscles
from planner.weekly_planner import generate_weekly_plan


logger = logging.getLogger("workout.mcp")


def _enforce_injury_safety(plan: dict[str, list[dict]], injury_muscles: list[str] | None) -> int:
    """Hard post-check: remove any exercise that targets injured muscles."""
    forbidden = normalize_injury_muscles(injury_muscles or [])
    if not forbidden:
        return 0

    removed = 0
    for day, day_exercises in (plan or {}).items():
        safe_exercises = []
        for ex in day_exercises or []:
            target = str(ex.get("target", "")).strip().lower()
            if target and target in forbidden:
                removed += 1
                continue
            safe_exercises.append(ex)
        plan[day] = safe_exercises
    return removed


def run_plan_cycle(
    user_id: str,
    exercises_df: pd.DataFrame,
    user_profile: dict[str, Any],
    goals: AgentGoals | None = None,
) -> tuple[dict[str, list[dict]], bool, list[str], dict[str, str], dict[str, str], dict[str, list[str]]]:
    """
    Run one agentic cycle: load state and goals, generate plan, evaluate.

    Args:
        user_id: User identifier.
        exercises_df: Exercise DataFrame (name, target, difficulty).
        user_profile: Must contain 'injury_muscles' (list).
        goals: AgentGoals to use; if None, use default.

    Returns:
        (plan, evaluation_ok, suggestions, krr_reasoning, day_themes, day_theme_muscles):
        - plan: {"Day 1": [{"name", "sets", "reps", "target"}, ...], ...}
        - evaluation_ok: True if no muscle exceeds max sets.
        - suggestions: List of modification suggestions if evaluation failed.
        - krr_reasoning: {"Day1": "...", ...} for display.
        - day_themes: {"Day1": "Chest", ...} for UI display.
        - day_theme_muscles: {"Day1": ["pectorals", ...], ...} for UI display.
    """
    state = AgentState.get_or_create(user_id)
    if goals is None:
        goals = AgentGoals()

    plan, krr_reasoning, day_themes, day_theme_muscles = generate_weekly_plan(
        user_id, user_profile, exercises_df, state, goals, modifications=None
    )

    evaluation = PlanEvaluation(goals)
    evaluation_ok, suggestions = evaluation.evaluate(plan)

    if not evaluation_ok and suggestions:
        # Optional: re-run with constraints (e.g. cap sets). For simplicity we return
        # the plan and suggestions; caller or next iteration can reduce sets.
        # We do one retry with lower sets for over-targeted muscles.
        for day_exercises in plan.values():
            for ex in day_exercises:
                muscle = ex.get("target")
                if muscle and ex.get("sets", 0) > 2:
                    ex["sets"] = max(2, ex["sets"] - 1)
        evaluation_ok, suggestions = evaluation.evaluate(plan)

    removed_count = _enforce_injury_safety(plan, user_profile.get("injury_muscles") if isinstance(user_profile, dict) else None)
    if removed_count:
        logger.warning("mcp.safety removed_injured_exercises=%s user_id=%s", removed_count, user_id)

    return plan, evaluation_ok, suggestions, krr_reasoning, day_themes, day_theme_muscles
