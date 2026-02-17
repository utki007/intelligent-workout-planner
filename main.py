"""
KRR-Based Workout Planning System — entry point and example usage.

Run: python main.py

Example: create or retrieve a user, generate next week's plan, optionally record feedback.
"""

import os
import sys

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models.user import User
from models.weekly_plan import day_to_focus
from data.excel_storage import (
    get_or_create_user,
    get_user,
    save_feedback,
    update_intensity_from_feedback,
)
from planner.weekly_planner import WeeklyPlanner, generate_next_week


def print_plan(plan: dict) -> None:
    """Print weekly plan with clear, intuitive layout and KRR reasoning."""
    WIDTH = 62
    thin = "─" * WIDTH
    sep = "═" * WIDTH

    sep = "═" * WIDTH
    print()
    print(sep)
    print("  WEEK {}  ·  WORKOUT PLAN".format(plan["week_number"]))
    print(sep)

    for day in range(1, 6):
        d = plan["days"][day]
        focus = d["focus"]
        print()
        print("  Day {}  │  {}".format(day, focus))
        print(thin)
        print("  Warmup   {}".format(d["warmup"]))

        if d["exercises"]:
            print("  Exercises:")
            for idx, ex in enumerate(d["exercises"], 1):
                w = ex.get("weight", 0) or 0
                if w > 0:
                    detail = "{} sets of {} reps at {} kg".format(ex["sets"], ex["reps"], w)
                else:
                    detail = "{} sets of {} reps (bodyweight)".format(ex["sets"], ex["reps"])
                print("    {}. {} — {}".format(idx, ex["name"], detail))
        else:
            print("  — Rest (no exercises; theme conflicts with your profile)")

        print("  Cooldown {}".format(d["cooldown"]))

        # KRR reasoning block
        if d.get("krr"):
            _print_krr_block(d["krr"])
        else:
            _print_krr_fallback(d)
        print()

    print(sep)
    print()


def _truncate(s: str, max_len: int) -> str:
    s = (s or "").strip()
    return (s[: max_len - 3] + "...") if len(s) > max_len else s


def _print_krr_block(krr_lines: list) -> None:
    """Print full KRR reasoning (Facts, Rules, Inference) with clear formatting."""
    WIDTH = 62
    print("  ┌─ KRR Reasoning " + "─" * (WIDTH - 18))
    for line in krr_lines:
        print("  │  {}".format(line))
    print("  └" + "─" * (WIDTH - 3))


def _print_krr_fallback(day_plan: dict) -> None:
    """KRR-style fallback when plan has no krr list."""
    exercises = day_plan.get("exercises") or []
    WIDTH = 62
    n = len(exercises)
    print("  ┌─ KRR Reasoning " + "─" * (WIDTH - 18))
    print("  │  Inference: Result = {} exercise(s) (plan loaded without full derivation)".format(n))
    print("  └" + "─" * (WIDTH - 3))


def collect_and_apply_feedback(user_id: str, week_number: int) -> None:
    """
    Prompt for feedback and soreness, save to Excel, and update intensity from soreness.
    High soreness (4-5) → lower intensity next week; low soreness (1-2) → higher intensity.
    """
    while True:
        try:
            raw = input("  How was this week? (1-5): ").strip()
            feedback_score = int(raw)
            if 1 <= feedback_score <= 5:
                break
        except ValueError:
            pass
        print("  Please enter a number between 1 and 5.")

    while True:
        try:
            raw = input("  Soreness level (1-5): ").strip()
            soreness_level = int(raw)
            if 1 <= soreness_level <= 5:
                break
        except ValueError:
            pass
        print("  Please enter a number between 1 and 5.")

    save_feedback(user_id, week_number, feedback_score, soreness_level)
    update_intensity_from_feedback(user_id, soreness_level)
    user = get_user(user_id)
    level = user.current_intensity_level if user else 1
    print("  Saved. Next week intensity: {}/5".format(level))


def example_usage():
    """Example: one user, generate plan, show output."""
    user_id = "Utkarsh"

    # Retrieve existing user or create new one
    user = get_or_create_user(
        user_id,
        name="Utkarsh",
        age=28,
        weight=75.0,
        experience_level="beginner",
        injury_muscles=["delts"],  # avoid shoulder-heavy exercises
        current_intensity_level=1,
    )
    injuries = ", ".join(user.injury_muscles) if user.injury_muscles else "none"
    print(
        f"User: {user.name} (id={user.user_id}), "
        f"age {user.age}, weight {user.weight} kg, level={user.experience_level}, "
        f"injuries={injuries}, intensity={user.current_intensity_level}/5"
    )

    plan = generate_next_week(user_id)
    print_plan(plan)

    try:
        if sys.stdin.isatty():
            reply = input("  Submit feedback for this week? (y/n): ").strip().lower()
            if reply in ("y", "yes"):
                collect_and_apply_feedback(user_id, plan["week_number"])
        else:
            print("  (Run in terminal to submit feedback.)")
    except (EOFError, OSError):
        print("  (Skipping input — run in terminal to use it.)")

    return plan


if __name__ == "__main__":
    example_usage()
