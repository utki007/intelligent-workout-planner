"""
Excel storage for users, weekly_plans, and feedback.
Creates data/users.xlsx, data/weekly_plans.xlsx, data/feedback.xlsx if missing.
"""

import os
import pandas as pd
from datetime import datetime
from typing import List, Optional
from models.user import User
from models.weekly_plan import WeeklyPlanEntry, day_to_focus

DATA_DIR = os.path.join(os.path.dirname(__file__))

USERS_PATH = os.path.join(DATA_DIR, "users.xlsx")
WEEKLY_PLANS_PATH = os.path.join(DATA_DIR, "weekly_plans.xlsx")
FEEDBACK_PATH = os.path.join(DATA_DIR, "feedback.xlsx")

USERS_COLUMNS = ["user_id", "name", "age", "weight", "experience_level", "injury_muscles", "current_intensity_level"]
PLANS_COLUMNS = ["user_id", "week_number", "day", "exercise_name", "sets", "reps", "weight", "timestamp"]
FEEDBACK_COLUMNS = ["user_id", "week_number", "feedback_score", "soreness_level", "timestamp"]


def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def init_users_excel():
    if not os.path.exists(USERS_PATH):
        _ensure_data_dir()
        pd.DataFrame(columns=USERS_COLUMNS).to_excel(USERS_PATH, index=False, engine="openpyxl")


def init_weekly_plans_excel():
    if not os.path.exists(WEEKLY_PLANS_PATH):
        _ensure_data_dir()
        pd.DataFrame(columns=PLANS_COLUMNS).to_excel(WEEKLY_PLANS_PATH, index=False, engine="openpyxl")


def init_feedback_excel():
    if not os.path.exists(FEEDBACK_PATH):
        _ensure_data_dir()
        pd.DataFrame(columns=FEEDBACK_COLUMNS).to_excel(FEEDBACK_PATH, index=False, engine="openpyxl")


def get_user(user_id: str) -> Optional[User]:
    init_users_excel()
    df = pd.read_excel(USERS_PATH, engine="openpyxl")
    row = df[df["user_id"].astype(str) == str(user_id)]
    if row.empty:
        return None
    return User.from_row(row.iloc[0].to_dict())


def save_user(user: User) -> None:
    init_users_excel()
    df = pd.read_excel(USERS_PATH, engine="openpyxl")
    df = df[df["user_id"].astype(str) != str(user.user_id)]
    new_row = pd.DataFrame([user.to_row()])
    df = pd.concat([df, new_row], ignore_index=True)
    df.to_excel(USERS_PATH, index=False, engine="openpyxl")


def get_or_create_user(user_id: str, **kwargs) -> User:
    u = get_user(user_id)
    if u is not None:
        return u
    # Create new user with required fields
    default = {
        "name": "User",
        "age": 25,
        "weight": 70.0,
        "experience_level": "beginner",
        "injury_muscles": [],
        "current_intensity_level": 1,
    }
    default.update(kwargs)
    user = User(user_id=user_id, **default)
    save_user(user)
    return user


def append_weekly_plan_entries(entries: List[WeeklyPlanEntry]) -> None:
    init_weekly_plans_excel()
    df = pd.read_excel(WEEKLY_PLANS_PATH, engine="openpyxl")
    for e in entries:
        df = pd.concat([df, pd.DataFrame([e.to_row()])], ignore_index=True)
    df.to_excel(WEEKLY_PLANS_PATH, index=False, engine="openpyxl")


def get_last_week_number(user_id: str) -> int:
    init_weekly_plans_excel()
    df = pd.read_excel(WEEKLY_PLANS_PATH, engine="openpyxl")
    user_plans = df[df["user_id"].astype(str) == str(user_id)]
    if user_plans.empty:
        return 0
    return int(user_plans["week_number"].max())


def load_weekly_plan(user_id: str, week_number: int) -> Optional[dict]:
    """Load existing plan for user/week from Excel. Returns None if none."""
    init_weekly_plans_excel()
    df = pd.read_excel(WEEKLY_PLANS_PATH, engine="openpyxl")
    subset = df[(df["user_id"].astype(str) == str(user_id)) & (df["week_number"] == week_number)]
    if subset.empty:
        return None
    subset = subset.sort_values("day")
    first_ts = subset.iloc[0].get("timestamp")
    plan = {"user_id": user_id, "week_number": week_number, "days": {}, "created_at": str(first_ts) if first_ts else None}
    for day in range(1, 6):
        day_rows = subset[subset["day"] == day]
        if day_rows.empty:
            continue
        focus = day_to_focus(day)
        warmup = "5 min light cardio; dynamic stretches for target muscle."
        cooldown = "5 min light stretch; focus on worked muscle groups."
        exercises = []
        for _, row in day_rows.iterrows():
            name = str(row.get("exercise_name", "")).strip()
            if name == "Warmup" or name == "Cooldown":
                continue
            exercises.append({
                "name": name,
                "sets": int(row.get("sets", 3)),
                "reps": int(row.get("reps", 12)),
                "weight": float(row.get("weight", 0) or 0),
            })
        plan["days"][day] = {
            "focus": focus,
            "warmup": warmup,
            "exercises": exercises,
            "cooldown": cooldown,
        }
    return plan


def delete_weekly_plan(user_id: str, week_number: int) -> None:
    """Remove all rows for this user/week so a new plan can be saved."""
    init_weekly_plans_excel()
    df = pd.read_excel(WEEKLY_PLANS_PATH, engine="openpyxl")
    df = df[~((df["user_id"].astype(str) == str(user_id)) & (df["week_number"] == week_number))]
    df.to_excel(WEEKLY_PLANS_PATH, index=False, engine="openpyxl")


def save_feedback(user_id: str, week_number: int, feedback_score: int, soreness_level: int) -> None:
    init_feedback_excel()
    df = pd.read_excel(FEEDBACK_PATH, engine="openpyxl")
    row = {
        "user_id": user_id,
        "week_number": week_number,
        "feedback_score": feedback_score,
        "soreness_level": soreness_level,
        "timestamp": datetime.now().isoformat(),
    }
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df.to_excel(FEEDBACK_PATH, index=False, engine="openpyxl")


def update_intensity_from_feedback(user_id: str, soreness_level: int) -> None:
    """Use soreness to adjust intensity: high soreness (4-5) → decrease; low (1-2) → increase. Bounds 1-5."""
    user = get_user(user_id)
    if not user:
        return
    level = user.current_intensity_level
    if soreness_level >= 4:
        level = max(1, level - 1)
    elif soreness_level <= 2:
        level = min(5, level + 1)
    user.current_intensity_level = level
    save_user(user)
