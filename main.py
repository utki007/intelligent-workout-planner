"""
Entry point for the Agentic Workout Planner.

Creates/loads a user profile, generates a 5-day split (10 exercises total),
displays KRR reasoning per day, calls Azure OpenAI for explanations,
and collects end-of-week feedback to update AgentState for next week.
"""

from __future__ import annotations

import os
import sys

import pandas as pd

# Ensure repo root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent.agent_state import AgentGoals, AgentState
from agent.llm import LLM
from agent.mcp import run_plan_cycle


# Default user profile with at least one injury so KRR reasoning is visible
DEFAULT_USER_ID = "default_user"
DEFAULT_USER_PROFILE = {
    "user_id": DEFAULT_USER_ID,
    "name": "Default User",
    "experience_level": "beginner",
    "injury_muscles": ["shoulders"],
}


def _data_dir():
    return os.path.join(os.path.dirname(__file__), "data")


def _load_exercises():
    path = os.path.join(_data_dir(), "exercises_db.csv")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Missing {path}. Create data/exercises_db.csv.")
    return pd.read_csv(path)


def main():
    print("Agentic Workout Planner")
    print("=" * 60)

    # 1. Create/load user profile (default with one injury)
    user_id = os.environ.get("WORKOUT_USER_ID", DEFAULT_USER_ID)
    profile = {
        "user_id": user_id,
        "name": os.environ.get("WORKOUT_USER_NAME", DEFAULT_USER_PROFILE["name"]),
        "experience_level": os.environ.get("WORKOUT_EXPERIENCE", "beginner"),
        "injury_muscles": DEFAULT_USER_PROFILE["injury_muscles"],
    }
    print(f"User: {profile['name']} ({user_id})")
    print(f"Injuries (avoided in plan): {profile['injury_muscles']}")
    print()

    # 2. Load exercises and state
    exercises_df = _load_exercises()
    state = AgentState.get_or_create(user_id)
    goals = AgentGoals()

    # 3. Generate plan via MCP
    plan, evaluation_ok, suggestions, krr_reasoning, day_themes, day_theme_muscles = run_plan_cycle(
        user_id, exercises_df, profile, goals
    )
    if suggestions:
        print("Plan suggestions:", suggestions)
    print()

    # 4. Display plan with KRR reasoning per day
    llm = LLM()
    day_order = sorted(plan.keys())
    for day in day_order:
        exercises = plan.get(day, [])
        theme = day_themes.get(day, day)
        targets = ", ".join(day_theme_muscles.get(day, [])) or "-"
        print(f"  {day}  |  {theme}  | targets: {targets}")
        print("-" * 50)
        if exercises:
            for ex in exercises:
                print(f"  {ex['name']:<30}  {ex['sets']} x {ex['reps']}  (target: {ex.get('target', '-')})")
        else:
            print("  Rest day (no recommended exercises for this muscle group).")
        print("  KRR Reasoning:")
        for line in (krr_reasoning.get(day, "") or "").split("\n"):
            print(f"    | {line}")
        # 5. LLM explanation per day (with KRR context for reasoning)
        skipped = "All exercises for this day target injured muscles." if not exercises and day == "Day5" else None
        day_krr = krr_reasoning.get(day, "")
        explanation = llm.explain_plan(user_id, day, exercises, skipped_reason=skipped, krr_reasoning=day_krr)
        print(f"  LLM: {explanation}")
        print()

    # 6. Record this week's volume in state (for next cycle)
    state.record_week_volume(plan)
    state.save()

    # 7. Feedback: end-of-week soreness
    print("=" * 60)
    try:
        raw = input("End-of-week feedback: soreness 1-5 (Enter to skip): ").strip()
        if raw:
            soreness = float(raw)
            if 1 <= soreness <= 5:
                state.update_from_feedback(overall_soreness=soreness)
                state.save()
                print("State updated. Next week's plan will adapt.")
            else:
                print("Soreness must be 1-5; skipped.")
    except EOFError:
        pass

    print("Done.")


if __name__ == "__main__":
    main()
