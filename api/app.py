"""
FastAPI backend for the Node.js frontend: plan generation and chat with the agent.
Run from project root: uvicorn api.app:app --reload --port 8000
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import date
from typing import Literal

import pandas as pd

# Project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent.agent_state import AgentGoals, AgentState
from agent.decision_policy import PlanEvaluation
from agent.krr import (
    JOINT_ALIASES,
    JOINT_MUSCLE_MAP,
    MUSCLE_ALIASES,
    MUSCLE_GROUP_EXPANSIONS,
    load_kb,
    normalize_equipment_preferences,
    normalize_injury_muscles,
    query_recommended,
)
from agent.llm import LLM
from planner.constraints import (
    DEFAULT_DAYS_PER_WEEK,
    MAX_DAYS_PER_WEEK,
    MIN_DAYS_PER_WEEK,
    clamp_days_per_week,
    normalize_requested_days,
)
from planner.weekly_planner import generate_weekly_plan


_LOG_LEVEL = os.environ.get("WORKOUT_LOG_LEVEL", "INFO").upper()
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=getattr(logging, _LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
logger = logging.getLogger("workout.api")
logger.setLevel(getattr(logging, _LOG_LEVEL, logging.INFO))


app = FastAPI(title="Agentic Workout Planner API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Paths from project root (api/app.py -> parent = project root)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
EXERCISES_PATH = os.path.join(DATA_DIR, "exercises_db.csv")
FRONTEND_DIST_DIR = os.path.join(_PROJECT_ROOT, "web", "dist")
FRONTEND_ASSETS_DIR = os.path.join(FRONTEND_DIST_DIR, "assets")
GENERIC_USER_ID = "default"

if os.path.isdir(FRONTEND_ASSETS_DIR):
    app.mount("/assets", StaticFiles(directory=FRONTEND_ASSETS_DIR), name="assets")


def _clip(value: str | None, limit: int = 160) -> str:
    s = (value or "").strip().replace("\n", " ")
    if len(s) <= limit:
        return s
    return s[:limit] + "..."


def _safe_user_id(user_id: str) -> str:
    return GENERIC_USER_ID


def _plan_store_path(user_id: str) -> str:
    return os.path.join(DATA_DIR, f"plan_{_safe_user_id(user_id)}.json")


def _profile_store_path(user_id: str) -> str:
    return os.path.join(DATA_DIR, f"profile_{_safe_user_id(user_id)}.json")


def load_profile(user_id: str) -> dict:
    """Load saved profile: onboarding, injuries, intensity, and chat history."""
    user_id = _safe_user_id(user_id)
    path = _profile_store_path(user_id)
    default = {
        "injury_muscles": [],
        "equipment_preferences": [],
        "intensity": 3,
        "chat_history": [],
        "onboarding_complete": False,
        "days_per_week": DEFAULT_DAYS_PER_WEEK,
    }
    if not os.path.isfile(path):
        logger.debug("profile.load user_id=%s found=false", user_id)
        return default
    try:
        with open(path) as f:
            data = json.load(f)
        prof = {
            "injury_muscles": data.get("injury_muscles", []),
            "equipment_preferences": sorted(
                normalize_equipment_preferences(data.get("equipment_preferences", []))
            ),
            "intensity": data.get("intensity", 3),
            "chat_history": data.get("chat_history", []),
            "onboarding_complete": data.get("onboarding_complete", False),
            "days_per_week": clamp_days_per_week(data.get("days_per_week", DEFAULT_DAYS_PER_WEEK)),
        }
        logger.debug(
            "profile.load user_id=%s found=true onboarding=%s days_per_week=%s injuries=%s",
            user_id,
            prof["onboarding_complete"],
            prof["days_per_week"],
            len(prof["injury_muscles"]),
        )
        return prof
    except Exception:
        logger.exception("profile.load user_id=%s failed", user_id)
        return default


def save_profile(
    user_id: str,
    injury_muscles: list[str] | None = None,
    equipment_preferences: list[str] | None = None,
    intensity: float | None = None,
    onboarding_complete: bool | None = None,
    days_per_week: int | None = None,
) -> None:
    """Update and persist profile. Merges with existing."""
    user_id = _safe_user_id(user_id)
    prof = load_profile(user_id)
    if injury_muscles is not None:
        prof["injury_muscles"] = sorted(normalize_injury_muscles(injury_muscles))
    if equipment_preferences is not None:
        prof["equipment_preferences"] = sorted(
            normalize_equipment_preferences(equipment_preferences)
        )
    if intensity is not None:
        prof["intensity"] = max(1, min(5, float(intensity)))
    if onboarding_complete is not None:
        prof["onboarding_complete"] = bool(onboarding_complete)
    if days_per_week is not None:
        prof["days_per_week"] = clamp_days_per_week(days_per_week)
    path = _profile_store_path(user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(prof, f, indent=2)
    logger.info(
        "profile.save user_id=%s onboarding=%s days_per_week=%s intensity=%s injuries=%s equipment=%s",
        user_id,
        prof.get("onboarding_complete"),
        prof.get("days_per_week"),
        prof.get("intensity"),
        len(prof.get("injury_muscles", [])),
        len(prof.get("equipment_preferences", [])),
    )


def append_chat_turn_to_profile(user_id: str, user_message: str, assistant_response: str) -> None:
    """Persist recent chat exchanges for better continuity across sessions."""
    user_id = _safe_user_id(user_id)
    prof = load_profile(user_id)
    hist = prof.get("chat_history", [])
    hist.append(
        {
            "ts": int(time.time()),
            "user": str(user_message or "").strip()[:500],
            "assistant": str(assistant_response or "").strip()[:1200],
        }
    )
    prof["chat_history"] = hist[-50:]
    path = _profile_store_path(user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(prof, f, indent=2)
    logger.info("profile.chat.append user_id=%s history_len=%s", user_id, len(prof.get("chat_history", [])))


def save_plan_for_user(user_id: str, plan: dict, krr_reasoning: dict) -> None:
    user_id = _safe_user_id(user_id)
    path = _plan_store_path(user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"plan": plan, "krr_reasoning": krr_reasoning}, f, indent=2)
    total_exercises = sum(len(v or []) for v in plan.values())
    logger.info(
        "plan.save user_id=%s days=%s exercises=%s krr_days=%s",
        user_id,
        len(plan),
        total_exercises,
        len(krr_reasoning),
    )


def load_plan_for_user(user_id: str) -> tuple[dict, dict] | None:
    """Load stored plan and krr_reasoning for user_id. Returns (plan, krr_reasoning) or None if missing."""
    user_id = _safe_user_id(user_id)
    path = _plan_store_path(user_id)
    if not os.path.isfile(path):
        logger.debug("plan.load user_id=%s found=false", user_id)
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        plan = data.get("plan", {})
        krr = data.get("krr_reasoning", {})
        logger.debug("plan.load user_id=%s found=true days=%s krr_days=%s", user_id, len(plan), len(krr))
        return (plan, krr)
    except Exception:
        logger.exception("plan.load user_id=%s failed", user_id)
        return None


def get_exercises_df() -> pd.DataFrame:
    """Load exercise DB from project data/; normalize column names so 'Target'/'Name' work as 'target'/'name'."""
    if not os.path.isfile(EXERCISES_PATH):
        logger.error("db.exercises missing path=%s", EXERCISES_PATH)
        raise HTTPException(status_code=500, detail=f"Missing {EXERCISES_PATH}")
    df = pd.read_csv(EXERCISES_PATH)
    df.columns = [str(c).strip().lower() for c in df.columns]
    logger.debug("db.exercises loaded rows=%s cols=%s", len(df), list(df.columns))
    return df


# Request/response models
class ProfileIn(BaseModel):
    user_id: str
    name: str | None = None
    experience_level: str | None = "beginner"
    injury_muscles: list[str] = []
    equipment_preferences: list[str] | None = None
    days_per_week: int | None = None


class PlanOut(BaseModel):
    plan: dict
    krr_reasoning: dict
    day_themes: dict
    day_theme_muscles: dict
    evaluation_ok: bool
    suggestions: list[str]


class ChatIn(BaseModel):
    user_id: str
    message: str
    plan_summary: str | None = None
    krr_summary: str | None = None
    selected_day: str | None = None


class ChatOut(BaseModel):
    response: str


class IntentIn(BaseModel):
    user_id: str | None = None
    message: str


class IntentInjuryUpdateOut(BaseModel):
    status: Literal["injured", "recovered", "cleared", "unknown"] | None = None
    operation: Literal["add", "remove", "clear", "set", "none"] | None = None
    muscles: list[str] = []


class IntentProfileUpdatesOut(BaseModel):
    injury_muscles: list[str] | None = None
    equipment_preferences: list[str] | None = None
    injury_update: IntentInjuryUpdateOut | None = None
    intensity: float | None = None
    days_per_week: int | None = None


class IntentOut(BaseModel):
    intent: Literal["generate_plan", "profile_update", "chat"]
    days_per_week: int | None = None
    assistant_reply: str | None = None
    profile_updates: IntentProfileUpdatesOut | None = None


REASONING_EXPLAINED = """
The planner uses a Knowledge Base (KRR) with three rules:

1. FORBIDDEN(User, Exercise): An exercise is forbidden for you if it trains any muscle you listed as injured.
   → We never recommend exercises that target injured muscles.

2. EQUIPMENT_ALLOWED(User, Exercise): If you set preferred equipment, an exercise must use one of those equipment types.
   → Bodyweight exercises are always allowed alongside your selected equipment.

3. RECOMMENDED(User, Exercise): An exercise is recommended if it's in the DB, marked "beginner", NOT forbidden,
   and (when preferences are set) equipment-allowed.
   → The weekly plan is built by picking from recommended exercises for each day (Chest, Back, Legs, Arms, Shoulders).

Facts come from: (a) the exercise DB (name, target muscle, difficulty, equipment), (b) your profile (injured muscles, equipment preferences).
The agent also tracks your state (soreness, weekly volume) to adjust sets and prioritize muscles over time.
"""


class ProfileUpdateIn(BaseModel):
    user_id: str
    injury_muscles: list[str] | None = None
    equipment_preferences: list[str] | None = None
    intensity: float | None = None
    onboarding_complete: bool | None = None
    days_per_week: int | None = None
    selected_day: str | None = None
    auto_update_plan: bool | None = True


class DayActionIn(BaseModel):
    user_id: str
    day: str


class DayReduceIn(DayActionIn):
    delta_sets: int | None = 1


class DaySwapIn(DayActionIn):
    index_a: int | None = 0
    index_b: int | None = 1


class DayActionOut(BaseModel):
    ok: bool
    user_id: str
    day: str
    message: str
    plan: dict
    krr_reasoning: dict
    day_theme: str | None = None
    day_theme_muscles: list[str] | None = None


def _build_user_profile_for_planner(user_id: str) -> tuple[dict, AgentGoals]:
    user_id = _safe_user_id(user_id)
    stored_profile = load_profile(user_id)
    goals = AgentGoals()
    try:
        intensity = float(stored_profile.get("intensity", goals.target_soreness_max))
    except (TypeError, ValueError):
        intensity = goals.target_soreness_max
    intensity = max(1.0, min(5.0, intensity))
    goals.max_sets_per_muscle_per_week = int(4 * intensity + 8)

    user_profile = {
        "user_id": user_id,
        "name": user_id,
        "experience_level": "beginner",
        "injury_muscles": stored_profile.get("injury_muscles", []),
        "equipment_preferences": stored_profile.get("equipment_preferences", []),
        "days_per_week": clamp_days_per_week(stored_profile.get("days_per_week", DEFAULT_DAYS_PER_WEEK)),
        "intensity": intensity,
    }
    return user_profile, goals


def _require_stored_plan(user_id: str) -> tuple[dict, dict]:
    user_id = _safe_user_id(user_id)
    stored = load_plan_for_user(user_id)
    if not stored:
        raise HTTPException(status_code=404, detail="No plan found for this user. Generate a plan first.")
    return stored


def _enforce_injury_safety(plan: dict[str, list[dict]], injury_muscles: list[str] | None) -> int:
    forbidden = set(normalize_injury_muscles(injury_muscles or []))
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


def _run_stateless_plan_cycle(
    user_id: str,
    exercises_df: pd.DataFrame,
    user_profile: dict,
    goals: AgentGoals,
) -> tuple[dict, bool, list[str], dict, dict, dict]:
    transient_state = AgentState(user_id=user_id)
    plan, krr_reasoning, day_themes, day_theme_muscles = generate_weekly_plan(
        user_id=user_id,
        user_profile=user_profile,
        exercises_df=exercises_df,
        state=transient_state,
        goals=goals,
    )

    evaluation = PlanEvaluation(goals)
    evaluation_ok, suggestions = evaluation.evaluate(plan)

    if not evaluation_ok and suggestions:
        for day_exercises in plan.values():
            for ex in day_exercises:
                target = str(ex.get("target", "")).strip().lower()
                if target and int(ex.get("sets", 0)) > 2:
                    ex["sets"] = max(2, int(ex["sets"]) - 1)
        evaluation_ok, suggestions = evaluation.evaluate(plan)

    removed_count = _enforce_injury_safety(plan, user_profile.get("injury_muscles") if isinstance(user_profile, dict) else None)
    if removed_count:
        logger.warning("plan.safety removed_injured_exercises=%s user_id=%s", removed_count, user_id)

    return plan, evaluation_ok, suggestions, krr_reasoning, day_themes, day_theme_muscles


def resolve_intent(message: str) -> dict:
    """Classify chat intent using the configured LLM."""
    llm = LLM()
    out = llm.resolve_intent(message)
    logger.info(
        "intent.resolve message=\"%s\" intent=%s days_per_week=%s has_reply=%s",
        _clip(message, 120),
        out.get("intent"),
        out.get("days_per_week"),
        bool(out.get("assistant_reply")),
    )
    return out


def _extract_days_hint(message: str) -> int | None:
    text = (message or "").lower()
    if not text:
        return None

    num_match = re.search(r"\b([2-7])\s*[- ]?day\b", text)
    if num_match:
        return int(num_match.group(1))

    word_match = re.search(r"\b(two|three|four|five|six|seven)\s*[- ]?day\b", text)
    if word_match:
        mapping = {
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
        }
        return mapping.get(word_match.group(1))

    return None


def _heuristic_intent(message: str) -> tuple[str, int | None]:
    text = (message or "").strip().lower()
    if not text:
        return "chat", None

    days_hint = _extract_days_hint(text)
    plan_terms = {
        "plan", "split", "workout", "routine", "program",
    }
    plan_verbs = {
        "make", "build", "create", "generate", "start", "give", "prepare",
    }
    profile_terms = {
        "injury", "injuries", "intensity", "profile", "soreness", "recover",
        "equipment", "dumbbell", "barbell", "kettlebell", "cable", "band", "machine", "bodyweight",
    }
    profile_verbs = {
        "update", "change", "edit", "set", "adjust",
    }

    has_plan_term = any(token in text for token in plan_terms)
    has_plan_verb = any(token in text for token in plan_verbs)
    has_profile_term = any(token in text for token in profile_terms)
    has_profile_verb = any(token in text for token in profile_verbs)

    if has_plan_term and (has_plan_verb or "me a" in text or "for me" in text):
        return "generate_plan", days_hint
    if has_plan_term and ("day" in text or "days" in text):
        return "generate_plan", days_hint
    if has_profile_term and has_profile_verb and not has_plan_term:
        return "profile_update", None

    return "chat", days_hint


def _extract_muscles_from_text(message: str) -> list[str]:
    text = (message or "").strip().lower()
    if not text:
        return []
    known_terms = {
        "abs",
        "abductors",
        "adductors",
        "ankle",
        "ankles",
        "biceps",
        "calves",
        "chest",
        "delts",
        "elbow",
        "elbows",
        "forearms",
        "glutes",
        "hamstrings",
        "hip",
        "hips",
        "knee",
        "knees",
        "lats",
        "legs",
        "pectorals",
        "quads",
        "shoulder",
        "shoulders",
        "spine",
        "traps",
        "triceps",
        "upper back",
        "wrist",
        "wrists",
    }
    known_terms.update(str(k).strip().lower() for k in JOINT_MUSCLE_MAP.keys())
    known_terms.update(str(k).strip().lower() for k in JOINT_ALIASES.keys())
    known_terms.update(str(k).strip().lower() for k in MUSCLE_ALIASES.keys())
    known_terms.update(str(v).strip().lower() for v in MUSCLE_ALIASES.values())
    known_terms.update(str(k).strip().lower() for k in MUSCLE_GROUP_EXPANSIONS.keys())
    for expanded in MUSCLE_GROUP_EXPANSIONS.values():
        for term in expanded:
            known_terms.add(str(term).strip().lower())

    matched_raw: list[str] = []
    for term in sorted(known_terms, key=len, reverse=True):
        if term and re.search(rf"\b{re.escape(term)}\b", text):
            matched_raw.append(term)
    return sorted(normalize_injury_muscles(matched_raw))


_EQUIPMENT_TERM_MAP = {
    "bodyweight": "body weight",
    "body weight": "body weight",
    "dumbbell": "dumbbell",
    "dumbbells": "dumbbell",
    "barbell": "barbell",
    "barbells": "barbell",
    "kettlebell": "kettlebell",
    "kettlebells": "kettlebell",
    "cable": "cable",
    "cables": "cable",
    "band": "band",
    "bands": "band",
    "resistance band": "resistance band",
    "resistance bands": "resistance band",
    "smith machine": "smith machine",
    "machine": "leverage machine",
    "machines": "leverage machine",
    "lever machine": "leverage machine",
    "leverage machine": "leverage machine",
    "stability ball": "stability ball",
    "medicine ball": "medicine ball",
    "ez bar": "ez barbell",
    "ez barbell": "ez barbell",
    "trap bar": "trap bar",
    "rope": "rope",
    "sled": "sled machine",
}


def _extract_equipment_from_text(message: str) -> list[str]:
    text = (message or "").strip().lower()
    if not text:
        return []
    matched_raw: list[str] = []
    for term in sorted(_EQUIPMENT_TERM_MAP.keys(), key=len, reverse=True):
        if term and re.search(rf"\b{re.escape(term)}\b", text):
            matched_raw.append(_EQUIPMENT_TERM_MAP[term])
    return sorted(normalize_equipment_preferences(matched_raw))


def _apply_injury_operation(current_injuries: list[str], operation: str, muscles: list[str]) -> list[str]:
    current = set(normalize_injury_muscles(current_injuries))
    parsed = set(normalize_injury_muscles(muscles))
    op = str(operation or "").strip().lower()

    if op == "clear":
        return []
    if op == "set":
        return sorted(parsed)
    if op == "remove":
        return sorted(current - parsed)
    if op == "add":
        return sorted(current | parsed)
    return sorted(current)


def _coerce_profile_updates_from_intent_payload(
    payload_updates: dict | None,
    current_profile: dict,
    message: str,
) -> dict:
    if not isinstance(payload_updates, dict):
        return {}

    updates: dict = {}

    # Structured scalar profile fields
    days = payload_updates.get("days_per_week")
    if isinstance(days, (int, float)):
        day_int = int(days)
        if MIN_DAYS_PER_WEEK <= day_int <= MAX_DAYS_PER_WEEK:
            updates["days_per_week"] = day_int

    intensity = payload_updates.get("intensity")
    if isinstance(intensity, (int, float)):
        i = float(intensity)
        if 1.0 <= i <= 5.0:
            updates["intensity"] = i

    equipment_raw = payload_updates.get("equipment_preferences")
    parsed_equipment: list[str] = []
    if isinstance(equipment_raw, list):
        parsed_equipment = sorted(
            normalize_equipment_preferences([str(eq).strip().lower() for eq in equipment_raw])
        )
    if not parsed_equipment:
        text_equipment = _extract_equipment_from_text(message)
        equipment_signal = bool(
            re.search(
                r"\b(equipment|gear|using|use|with|only|available|have|access)\b",
                (message or "").lower(),
            )
        )
        if text_equipment and equipment_signal:
            parsed_equipment = text_equipment
    if parsed_equipment:
        updates["equipment_preferences"] = parsed_equipment

    # Injury update semantics
    current_injuries = current_profile.get("injury_muscles", []) if isinstance(current_profile, dict) else []
    injury_raw = payload_updates.get("injury_update")
    injury_payload = injury_raw if isinstance(injury_raw, dict) else {}
    status = str(injury_payload.get("status", "")).strip().lower()
    if status not in {"injured", "recovered", "cleared", "unknown"}:
        status = ""
    operation = str(injury_payload.get("operation", "")).strip().lower()
    if operation not in {"add", "remove", "clear", "set", "none"}:
        operation = ""
    muscles_raw = injury_payload.get("muscles")
    parsed_muscles: list[str] = []
    if isinstance(muscles_raw, list):
        parsed_muscles = sorted(normalize_injury_muscles([str(m).strip().lower() for m in muscles_raw]))

    if not parsed_muscles:
        parsed_muscles = _extract_muscles_from_text(message)

    if not operation and status == "injured":
        operation = "add"
    elif not operation and status == "recovered":
        operation = "remove"
    elif not operation and status == "cleared":
        operation = "clear"

    direct_injury_list = payload_updates.get("injury_muscles")
    if isinstance(direct_injury_list, list):
        canonical = sorted(normalize_injury_muscles([str(m).strip().lower() for m in direct_injury_list]))
        updates["injury_muscles"] = canonical
        if operation in {"", "none"}:
            operation = "set"
            parsed_muscles = canonical

    if operation and operation != "none":
        updates["injury_muscles"] = _apply_injury_operation(current_injuries, operation, parsed_muscles)
        updates["injury_update"] = {
            "status": status or "unknown",
            "operation": operation,
            "muscles": parsed_muscles,
        }

    return updates


def _heuristic_profile_updates(message: str, current_profile: dict) -> dict:
    text = (message or "").strip().lower()
    if not text:
        return {}

    updates: dict = {}

    days_hint = _extract_days_hint(text)
    if days_hint is not None:
        updates["days_per_week"] = days_hint

    intensity_match = re.search(r"\bintensity\b[^\d]*([1-5](?:\.\d)?)\b", text)
    if intensity_match:
        try:
            intensity = float(intensity_match.group(1))
            updates["intensity"] = max(1.0, min(5.0, intensity))
        except (TypeError, ValueError):
            pass

    equipment = _extract_equipment_from_text(text)
    equipment_signal = bool(
        re.search(r"\b(equipment|gear|using|use|with|only|available|have|access)\b", text)
    )
    if equipment and equipment_signal:
        updates["equipment_preferences"] = equipment

    current_injuries = current_profile.get("injury_muscles", []) if isinstance(current_profile, dict) else []
    muscles = _extract_muscles_from_text(text)

    clear_all = bool(
        re.search(
            r"\b(no injuries|not injured|fully recovered|all recovered|injury[\s-]?free|clear all injuries)\b",
            text,
        )
    )
    recovery_signal = bool(
        re.search(r"\b(recovered|healed|better|pain[\s-]?free|no pain|no longer hurt|fixed)\b", text)
    )
    injury_signal = bool(
        re.search(r"\b(injured|injury|hurt|pain|strained|strain|pulled|tear|tore|sprain|sore)\b", text)
    )
    remove_signal = bool(re.search(r"\b(remove|delete|drop|clear)\b", text))
    add_signal = bool(re.search(r"\b(add|include|mark)\b", text))

    operation = ""
    status = ""
    if clear_all:
        operation = "clear"
        status = "cleared"
        muscles = []
    elif muscles and (remove_signal or recovery_signal):
        operation = "remove"
        status = "recovered"
    elif muscles and (add_signal or injury_signal):
        operation = "add"
        status = "injured"

    if operation:
        updates["injury_muscles"] = _apply_injury_operation(current_injuries, operation, muscles)
        updates["injury_update"] = {
            "status": status,
            "operation": operation,
            "muscles": muscles,
        }

    return updates


def _summarize_saved_plan(plan: dict) -> str | None:
    if not plan:
        return None
    day_keys = sorted(plan.keys(), key=lambda d: int(re.search(r"\d+", str(d)).group(0)) if re.search(r"\d+", str(d)) else 999)
    if not day_keys:
        return None
    parts = []
    for day in day_keys[:5]:
        names = [str(ex.get("name", "")).strip() for ex in (plan.get(day) or []) if str(ex.get("name", "")).strip()]
        parts.append(f"{day}: {', '.join(names[:6]) or 'Rest'}")
    return "; ".join(parts)


def _summarize_saved_krr(krr_reasoning: dict) -> str | None:
    if not isinstance(krr_reasoning, dict) or not krr_reasoning:
        return None
    chunks: list[str] = []
    for _, value in list(krr_reasoning.items())[:2]:
        text = str(value or "").strip()
        if text:
            chunks.append(_clip(text, 240))
    return " ".join(chunks) if chunks else None


def _is_progress_query(message: str) -> bool:
    text = (message or "").lower()
    if not text:
        return False
    return any(
        token in text
        for token in [
            "progress",
            "how am i doing",
            "how i'm doing",
            "stats",
            "status",
            "track",
            "summary",
        ]
    )


def _sorted_day_keys(plan: dict) -> list[str]:
    if not isinstance(plan, dict):
        return []
    return sorted(
        plan.keys(),
        key=lambda d: int(re.search(r"\d+", str(d)).group(0)) if re.search(r"\d+", str(d)) else 999,
    )


def _normalize_day_label(label: str | None) -> str:
    text = str(label or "").strip().lower()
    if not text:
        return ""
    return re.sub(r"\s+", "", text)


def _is_schedule_query(message: str) -> bool:
    text = (message or "").lower()
    if not text:
        return False
    schedule_signals = [
        "what am i doing",
        "what's my workout",
        "whats my workout",
        "today",
        "yesterday",
        "tomorrow",
        "weekend",
        "saturday",
        "sunday",
        "next workout",
        "next session",
        "today's plan",
        "todays plan",
        "today workout",
        "my plan for",
        "which exercises",
    ]
    return any(token in text for token in schedule_signals)


def _resolve_day_for_schedule_query(plan: dict, message: str, selected_day: str | None = None) -> str | None:
    day_keys = _sorted_day_keys(plan)
    if not day_keys:
        return None

    normalized_map = {_normalize_day_label(k): k for k in day_keys}
    selected = normalized_map.get(_normalize_day_label(selected_day))

    msg = message or ""
    m = re.search(r"\bday\s*(\d{1,2})\b", msg, flags=re.IGNORECASE)
    if m:
        requested = f"Day{int(m.group(1))}"
        if requested in plan:
            return requested

    lowered = msg.lower()
    if "next workout" in lowered or "next session" in lowered:
        if selected and selected in day_keys:
            curr_idx = day_keys.index(selected)
            return day_keys[(curr_idx + 1) % len(day_keys)]
        idx = date.today().weekday() % len(day_keys)
        return day_keys[(idx + 1) % len(day_keys)]

    if "tomorrow" in lowered:
        if selected and selected in day_keys:
            curr_idx = day_keys.index(selected)
            return day_keys[(curr_idx + 1) % len(day_keys)]
        idx = (date.today().weekday() + 1) % len(day_keys)
        return day_keys[idx]

    if "yesterday" in lowered:
        if selected and selected in day_keys:
            curr_idx = day_keys.index(selected)
            return day_keys[(curr_idx - 1) % len(day_keys)]
        idx = (date.today().weekday() - 1) % len(day_keys)
        return day_keys[idx]

    day_number_to_key: dict[int, str] = {}
    for key in day_keys:
        match = re.search(r"\d+", key)
        if match:
            day_number_to_key[int(match.group(0))] = key

    if "sunday" in lowered:
        return day_number_to_key.get(7) or day_number_to_key.get(6) or day_keys[-1]

    if "saturday" in lowered:
        return day_number_to_key.get(6) or day_number_to_key.get(5) or day_keys[-1]

    if "weekend" in lowered:
        return day_number_to_key.get(6) or day_number_to_key.get(7) or day_keys[-1]

    if "today" in lowered or "tonight" in lowered:
        if selected and selected in day_keys:
            return selected
        idx = date.today().weekday() % len(day_keys)
        return day_keys[idx]

    if selected and selected in day_keys:
        return selected

    return day_keys[0]


def _structured_schedule_facts(plan: dict, day: str) -> str:
    exercises = list(plan.get(day, []) or [])
    compact = []
    for ex in exercises:
        compact.append(
            {
                "name": str(ex.get("name", "")).strip(),
                "sets": ex.get("sets"),
                "reps": ex.get("reps"),
                "target": str(ex.get("target", "")).strip(),
            }
        )
    facts = {
        "query_type": "schedule_lookup",
        "resolved_day": day,
        "exercise_count": len(compact),
        "exercises": compact,
    }
    return json.dumps(facts, ensure_ascii=False)


def _structured_progress_facts(user_id: str, profile: dict) -> str:
    user_id = _safe_user_id(user_id)
    stored = load_plan_for_user(user_id)
    plan = stored[0] if stored else {}
    total_days = len(plan)
    total_exercises = sum(len(day or []) for day in plan.values()) if plan else 0

    facts = {
        "query_type": "progress_lookup",
        "days_per_week": profile.get("days_per_week", DEFAULT_DAYS_PER_WEEK),
        "injuries": profile.get("injury_muscles", []) or [],
        "equipment_preferences": profile.get("equipment_preferences", []) or [],
        "latest_plan_days": total_days,
        "latest_plan_exercises": total_exercises,
    }
    return json.dumps(facts, ensure_ascii=False)


def _coerce_intensity_value(profile: dict | None) -> float:
    source = profile if isinstance(profile, dict) else {}
    try:
        value = float(source.get("intensity", 3))
    except (TypeError, ValueError):
        value = 3.0
    return max(1.0, min(5.0, value))


def _dynamic_profile_fields_changed(before: dict | None, after: dict | None) -> list[str]:
    before_profile = before if isinstance(before, dict) else {}
    after_profile = after if isinstance(after, dict) else {}
    changed: list[str] = []

    before_days = clamp_days_per_week(before_profile.get("days_per_week", DEFAULT_DAYS_PER_WEEK))
    after_days = clamp_days_per_week(after_profile.get("days_per_week", DEFAULT_DAYS_PER_WEEK))
    if before_days != after_days:
        changed.append("days_per_week")

    before_intensity = _coerce_intensity_value(before_profile)
    after_intensity = _coerce_intensity_value(after_profile)
    if abs(before_intensity - after_intensity) > 1e-9:
        changed.append("intensity")

    before_injuries = sorted(normalize_injury_muscles(before_profile.get("injury_muscles", [])))
    after_injuries = sorted(normalize_injury_muscles(after_profile.get("injury_muscles", [])))
    if before_injuries != after_injuries:
        changed.append("injury_muscles")

    before_equipment = sorted(normalize_equipment_preferences(before_profile.get("equipment_preferences", [])))
    after_equipment = sorted(normalize_equipment_preferences(after_profile.get("equipment_preferences", [])))
    if before_equipment != after_equipment:
        changed.append("equipment_preferences")

    return changed


def _derive_day_metadata_from_plan(plan: dict[str, list[dict]]) -> tuple[dict[str, str], dict[str, list[str]]]:
    day_themes: dict[str, str] = {}
    day_theme_muscles: dict[str, list[str]] = {}
    for day in _sorted_day_keys(plan):
        exercises = plan.get(day, []) or []
        muscles: list[str] = []
        for ex in exercises:
            target = str(ex.get("target", "")).strip().lower()
            if target and target not in muscles:
                muscles.append(target)
        day_theme_muscles[day] = muscles[:4]
        if not muscles:
            day_themes[day] = "Rest"
        elif len(muscles) == 1:
            day_themes[day] = f"{muscles[0].title()} Focus"
        else:
            day_themes[day] = " / ".join(m.title() for m in muscles[:2])
    return day_themes, day_theme_muscles


def _merge_regenerated_remaining_days(
    existing_plan: dict[str, list[dict]],
    existing_krr: dict[str, str],
    generated_plan: dict[str, list[dict]],
    generated_krr: dict[str, str],
    generated_day_themes: dict[str, str],
    generated_day_theme_muscles: dict[str, list[str]],
    selected_day: str | None,
) -> tuple[dict[str, list[dict]], dict[str, str], dict[str, str], dict[str, list[str]], str]:
    existing_days = _sorted_day_keys(existing_plan)
    generated_days = _sorted_day_keys(generated_plan)

    normalized_selected = _normalize_day_label(selected_day)
    selected_index = -1
    if normalized_selected:
        for idx, day in enumerate(existing_days):
            if _normalize_day_label(day) == normalized_selected:
                selected_index = idx
                break

    if selected_index < 0 or len(existing_days) != len(generated_days):
        return (
            generated_plan,
            generated_krr,
            generated_day_themes,
            generated_day_theme_muscles,
            "full",
        )

    merged_plan: dict[str, list[dict]] = {}
    merged_krr: dict[str, str] = {}
    merged_day_themes: dict[str, str] = {}
    merged_day_theme_muscles: dict[str, list[str]] = {}

    for idx, existing_day in enumerate(existing_days):
        generated_day = generated_days[idx]
        if idx < selected_index:
            merged_plan[existing_day] = existing_plan.get(existing_day, []) or []
            merged_krr[existing_day] = str(existing_krr.get(existing_day, "") or "")
        else:
            merged_plan[existing_day] = generated_plan.get(generated_day, []) or []
            merged_krr[existing_day] = str(generated_krr.get(generated_day, "") or "")

        merged_day_themes[existing_day] = str(generated_day_themes.get(generated_day, existing_day) or existing_day)
        merged_day_theme_muscles[existing_day] = list(generated_day_theme_muscles.get(generated_day, []) or [])

    inferred_themes, inferred_muscles = _derive_day_metadata_from_plan(merged_plan)
    for idx, existing_day in enumerate(existing_days):
        if idx < selected_index:
            merged_day_themes[existing_day] = inferred_themes.get(existing_day, merged_day_themes.get(existing_day, existing_day))
            merged_day_theme_muscles[existing_day] = inferred_muscles.get(
                existing_day, merged_day_theme_muscles.get(existing_day, [])
            )

    return merged_plan, merged_krr, merged_day_themes, merged_day_theme_muscles, "remaining"


def _auto_refresh_stored_plan_for_profile(
    user_id: str,
    selected_day: str | None = None,
    force_full_regen: bool = False,
) -> dict | None:
    user_id = _safe_user_id(user_id)
    stored = load_plan_for_user(user_id)
    if not stored:
        return None

    existing_plan, existing_krr = stored
    if not existing_plan:
        return None

    df = get_exercises_df()
    user_profile, goals = _build_user_profile_for_planner(user_id)
    generated_plan, evaluation_ok, suggestions, generated_krr, generated_day_themes, generated_day_theme_muscles = (
        _run_stateless_plan_cycle(user_id, df, user_profile, goals)
    )

    update_mode = "full"
    if force_full_regen:
        merged_plan = generated_plan
        merged_krr = generated_krr
        merged_day_themes = generated_day_themes
        merged_day_theme_muscles = generated_day_theme_muscles
    else:
        merged_plan, merged_krr, merged_day_themes, merged_day_theme_muscles, update_mode = (
            _merge_regenerated_remaining_days(
                existing_plan=existing_plan,
                existing_krr=existing_krr,
                generated_plan=generated_plan,
                generated_krr=generated_krr,
                generated_day_themes=generated_day_themes,
                generated_day_theme_muscles=generated_day_theme_muscles,
                selected_day=selected_day,
            )
        )

    if not merged_day_themes or not merged_day_theme_muscles:
        merged_day_themes, merged_day_theme_muscles = _derive_day_metadata_from_plan(merged_plan)

    save_plan_for_user(user_id, merged_plan, merged_krr)
    return {
        "plan": merged_plan,
        "krr_reasoning": merged_krr,
        "day_themes": merged_day_themes,
        "day_theme_muscles": merged_day_theme_muscles,
        "evaluation_ok": evaluation_ok,
        "suggestions": suggestions or [],
        "update_mode": update_mode,
    }


@app.middleware("http")
async def request_logger(request: Request, call_next):
    request_id = uuid.uuid4().hex[:10]
    start = time.perf_counter()
    logger.info(
        "[%s] request.start method=%s path=%s query=%s",
        request_id,
        request.method,
        request.url.path,
        dict(request.query_params),
    )
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("[%s] request.error method=%s path=%s", request_id, request.method, request.url.path)
        raise
    elapsed_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "[%s] request.end method=%s path=%s status=%s duration_ms=%.1f",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/profile")
def get_profile(user_id: str = Query(..., description="User ID")):
    """Return saved profile (onboarding + constraints)."""
    user_id = _safe_user_id(user_id)
    prof = load_profile(user_id)
    logger.info(
        "profile.get user_id=%s onboarding=%s days_per_week=%s injuries=%s equipment=%s",
        user_id,
        prof.get("onboarding_complete"),
        prof.get("days_per_week"),
        len(prof.get("injury_muscles", [])),
        len(prof.get("equipment_preferences", [])),
    )
    return prof


@app.get("/api/injury-map")
def get_injury_map():
    """Return KB-backed injury filter mapping for frontend joint/muscle selectors."""
    joints = {joint: list(muscles) for joint, muscles in JOINT_MUSCLE_MAP.items()}
    muscles: list[str] = []
    seen: set[str] = set()
    for values in joints.values():
        for muscle in values:
            m = str(muscle).strip().lower()
            if m and m not in seen:
                seen.add(m)
                muscles.append(m)
    return {"joints": joints, "muscles": muscles}


@app.get("/api/equipment-options")
def get_equipment_options():
    """Return canonical equipment options present in the exercise DB."""
    fallback = [
        "body weight",
        "dumbbell",
        "barbell",
        "kettlebell",
        "cable",
        "band",
        "resistance band",
        "smith machine",
        "leverage machine",
        "stability ball",
        "medicine ball",
        "ez barbell",
    ]
    try:
        df = get_exercises_df()
        if "equipment" not in df.columns:
            return {"equipment": fallback}
        options: set[str] = set()
        for raw in df["equipment"].fillna("").astype(str).tolist():
            options.update(normalize_equipment_preferences([raw]))
        normalized = sorted(options)
        return {"equipment": normalized or fallback}
    except Exception:
        logger.exception("equipment.options failed")
        return {"equipment": fallback}


@app.put("/api/profile")
def update_profile(body: ProfileUpdateIn):
    """Save profile fields: injuries, intensity, onboarding data."""
    user_id = _safe_user_id(body.user_id)
    profile_before = load_profile(user_id)

    save_profile(
        user_id,
        injury_muscles=body.injury_muscles,
        equipment_preferences=body.equipment_preferences,
        intensity=body.intensity,
        onboarding_complete=body.onboarding_complete,
        days_per_week=body.days_per_week,
    )
    updated_profile = load_profile(user_id)

    plan_update_payload = None
    plan_update_error = None
    should_attempt_plan_update = bool(body.auto_update_plan)
    changed_dynamic_fields = _dynamic_profile_fields_changed(profile_before, updated_profile)
    if should_attempt_plan_update:
        if changed_dynamic_fields:
            days_changed = "days_per_week" in changed_dynamic_fields
            try:
                plan_update_payload = _auto_refresh_stored_plan_for_profile(
                    user_id=user_id,
                    selected_day=body.selected_day,
                    force_full_regen=days_changed,
                )
            except Exception as exc:
                logger.exception(
                    "profile.update.auto_plan_refresh_failed user_id=%s selected_day=%s",
                    user_id,
                    body.selected_day,
                )
                plan_update_error = str(exc)
        else:
            logger.info("profile.update.auto_plan_refresh_skipped user_id=%s reason=no_dynamic_field_change", user_id)

    logger.info(
        "profile.update user_id=%s onboarding=%s days_per_week=%s intensity=%s injuries=%s equipment=%s auto_plan_refresh=%s update_mode=%s changed_dynamic_fields=%s",
        user_id,
        body.onboarding_complete,
        body.days_per_week,
        body.intensity,
        None if body.injury_muscles is None else len(body.injury_muscles),
        None if body.equipment_preferences is None else len(body.equipment_preferences),
        should_attempt_plan_update,
        None if not plan_update_payload else plan_update_payload.get("update_mode"),
        changed_dynamic_fields,
    )
    response_payload = {
        "ok": True,
        "profile": updated_profile,
        "plan_updated": bool(plan_update_payload),
        "plan_update_error": plan_update_error,
        "selected_day": body.selected_day,
        "changed_dynamic_fields": changed_dynamic_fields,
    }
    if plan_update_payload:
        response_payload.update(plan_update_payload)
    return response_payload


@app.post("/api/intent", response_model=IntentOut)
def detect_intent(body: IntentIn):
    """Resolve chat intent and structured profile updates from one backend source of truth."""
    user_id = _safe_user_id(body.user_id or GENERIC_USER_ID)
    current_profile = load_profile(user_id)
    payload = resolve_intent(body.message) or {}
    intent = payload.get("intent", "chat")
    if intent not in {"generate_plan", "profile_update", "chat"}:
        intent = "chat"
    days = payload.get("days_per_week")
    if isinstance(days, (int, float)):
        days = int(days)
        if days < MIN_DAYS_PER_WEEK or days > MAX_DAYS_PER_WEEK:
            days = None
    elif isinstance(days, str):
        normalized = days.strip().lower()
        word_to_num = {
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
        }
        if normalized.isdigit():
            days = int(normalized)
        elif normalized in word_to_num:
            days = word_to_num[normalized]
        else:
            days = None
        if isinstance(days, int) and (days < MIN_DAYS_PER_WEEK or days > MAX_DAYS_PER_WEEK):
            days = None
    else:
        days = None

    heuristic_intent, heuristic_days = _heuristic_intent(body.message)
    if intent == "chat" and heuristic_intent in {"generate_plan", "profile_update"}:
        intent = heuristic_intent
    if intent == "generate_plan" and days is None and heuristic_days is not None:
        days = heuristic_days

    profile_updates = _coerce_profile_updates_from_intent_payload(
        payload.get("profile_updates"),
        current_profile=current_profile,
        message=body.message,
    )
    heuristic_updates = _heuristic_profile_updates(body.message, current_profile)
    if not profile_updates:
        profile_updates = heuristic_updates
    elif heuristic_updates:
        for key, value in heuristic_updates.items():
            if key not in profile_updates:
                profile_updates[key] = value

    if intent == "chat" and profile_updates and heuristic_intent != "generate_plan":
        intent = "profile_update"
    if intent == "generate_plan" and days is None and isinstance(profile_updates.get("days_per_week"), int):
        days = int(profile_updates["days_per_week"])

    reply = payload.get("assistant_reply")
    if not isinstance(reply, str) or not reply.strip():
        reply = None
    if reply is None:
        llm = LLM()
        generated_reply = llm.activity_hint(body.message, intent=intent)
        if isinstance(generated_reply, str) and generated_reply.strip():
            reply = generated_reply.strip()

    logger.info(
        "intent.detect user_id=%s message=\"%s\" intent=%s days_per_week=%s has_reply=%s update_keys=%s",
        user_id,
        _clip(body.message, 120),
        intent,
        days,
        bool(reply),
        sorted(profile_updates.keys()) if profile_updates else [],
    )
    return IntentOut(
        intent=intent,
        days_per_week=days,
        assistant_reply=reply,
        profile_updates=profile_updates or None,
    )


@app.get("/api/db")
def get_db():
    """Return what's stored: exercises from exercises_db.csv."""
    exercises = []
    try:
        df = get_exercises_df()
        exercises = df[["name", "target", "difficulty", "equipment"]].fillna("").astype(str).to_dict("records")
    except Exception:
        pass
    return {
        "exercises": exercises,
        "source": "data/exercises_db.csv",
    }


@app.get("/api/reasoning")
def get_reasoning():
    """Return how KRR reasoning works: rules and short explanation."""
    return {
        "how_it_works": REASONING_EXPLAINED.strip(),
        "rules": [
            "forbidden(User, Exercise) ← exercise trains a muscle M AND user has M injured",
            "equipment_allowed(User, Exercise) ← user prefers equipment Eq AND exercise uses Eq",
            "equipment_allowed(User, Exercise) ← exercise uses body weight",
            "recommended(User, Exercise) ← exercise in DB AND difficulty=beginner AND NOT forbidden(User, Exercise)"
            " AND (no equipment preference OR equipment_allowed(User, Exercise))",
        ],
        "facts_from": [
            "exercises_db.csv (name, target, difficulty, equipment)",
            "your injuries list",
            "your equipment preferences",
        ],
    }


@app.post("/api/plan", response_model=PlanOut)
def generate_plan(profile: ProfileIn):
    """Generate a weekly plan. Uses saved injuries if profile.injury_muscles is empty."""
    user_id = _safe_user_id(profile.user_id)
    try:
        df = get_exercises_df()
    except HTTPException:
        raise
    # Load full profile so we can respect stored limitations (injuries, intensity, days/week)
    stored_profile = load_profile(user_id)
    injuries = profile.injury_muscles if profile.injury_muscles else stored_profile["injury_muscles"]
    equipment_preferences = (
        sorted(normalize_equipment_preferences(profile.equipment_preferences))
        if profile.equipment_preferences is not None
        else stored_profile.get("equipment_preferences", [])
    )
    logger.info(
        "plan.generate.start user_id=%s onboarding=%s days_per_week=%s injuries_in_request=%s injuries_effective=%s equipment_effective=%s intensity=%s",
        user_id,
        stored_profile.get("onboarding_complete"),
        stored_profile.get("days_per_week"),
        len(profile.injury_muscles or []),
        len(injuries or []),
        len(equipment_preferences or []),
        stored_profile.get("intensity"),
    )
    if injuries:
        save_profile(user_id, injury_muscles=injuries)
        stored_profile["injury_muscles"] = injuries
    if profile.equipment_preferences is not None:
        save_profile(user_id, equipment_preferences=equipment_preferences)
        stored_profile["equipment_preferences"] = equipment_preferences

    # Derive agent goals from profile intensity (1–5 slider).
    # Lower intensity → lower weekly volume cap; higher intensity → higher cap.
    goals = AgentGoals()
    try:
        intensity = float(stored_profile.get("intensity", goals.target_soreness_max))
    except (TypeError, ValueError):
        intensity = goals.target_soreness_max
    # Map intensity ~1–5 to a reasonable max_sets range (e.g. 12–28 sets/week).
    intensity = max(1.0, min(5.0, intensity))
    goals.max_sets_per_muscle_per_week = int(4 * intensity + 8)

    requested_days = profile.days_per_week
    requested_days = normalize_requested_days(requested_days)

    user_profile = {
        "user_id": user_id,
        "name": profile.name or "Generic User",
        "experience_level": profile.experience_level or "beginner",
        "injury_muscles": injuries,
        "equipment_preferences": equipment_preferences,
        # Respect preferred training frequency unless an override was explicitly requested
        "days_per_week": clamp_days_per_week(requested_days or stored_profile.get("days_per_week", DEFAULT_DAYS_PER_WEEK)),
        "intensity": intensity,
    }
    plan, evaluation_ok, suggestions, krr_reasoning, day_themes, day_theme_muscles = _run_stateless_plan_cycle(
        user_id, df, user_profile, goals
    )
    total_exercises = sum(len(v or []) for v in plan.values())
    logger.info(
        "plan.generate.done user_id=%s days=%s exercises=%s evaluation_ok=%s suggestions=%s max_sets=%s",
        user_id,
        len(plan),
        total_exercises,
        evaluation_ok,
        len(suggestions or []),
        goals.max_sets_per_muscle_per_week,
    )
    save_plan_for_user(user_id, plan, krr_reasoning)
    return PlanOut(
        plan=plan,
        krr_reasoning=krr_reasoning,
        day_themes=day_themes,
        day_theme_muscles=day_theme_muscles,
        evaluation_ok=evaluation_ok,
        suggestions=suggestions or [],
    )


@app.post("/api/plan/day/regenerate", response_model=DayActionOut)
def regenerate_plan_day(body: DayActionIn):
    user_id = _safe_user_id(body.user_id)
    plan, krr_reasoning = _require_stored_plan(user_id)
    if body.day not in plan:
        raise HTTPException(status_code=404, detail=f"Unknown day: {body.day}")

    try:
        df = get_exercises_df()
        user_profile, goals = _build_user_profile_for_planner(user_id)
        full_plan, _, _, full_krr, day_themes, day_theme_muscles = _run_stateless_plan_cycle(
            user_id,
            df,
            user_profile,
            goals,
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("plan.day.regenerate failed user_id=%s day=%s", user_id, body.day)
        raise HTTPException(status_code=500, detail="Failed to regenerate day")

    plan[body.day] = full_plan.get(body.day, [])
    krr_reasoning[body.day] = full_krr.get(body.day, "")
    save_plan_for_user(user_id, plan, krr_reasoning)

    logger.info(
        "plan.day.regenerate user_id=%s day=%s exercises=%s",
        user_id,
        body.day,
        len(plan.get(body.day, [])),
    )
    return DayActionOut(
        ok=True,
        user_id=user_id,
        day=body.day,
        message=f"{body.day} regenerated and validated.",
        plan=plan,
        krr_reasoning=krr_reasoning,
        day_theme=day_themes.get(body.day),
        day_theme_muscles=day_theme_muscles.get(body.day, []),
    )


@app.post("/api/plan/day/reduce", response_model=DayActionOut)
def reduce_plan_day_volume(body: DayReduceIn):
    user_id = _safe_user_id(body.user_id)
    plan, krr_reasoning = _require_stored_plan(user_id)
    if body.day not in plan:
        raise HTTPException(status_code=404, detail=f"Unknown day: {body.day}")

    delta = body.delta_sets if isinstance(body.delta_sets, int) and body.delta_sets > 0 else 1
    day_exercises = plan.get(body.day, []) or []
    updated = []
    for ex in day_exercises:
        ex_next = dict(ex)
        sets = int(ex_next.get("sets", 3))
        ex_next["sets"] = max(2, sets - delta)
        updated.append(ex_next)
    plan[body.day] = updated
    save_plan_for_user(user_id, plan, krr_reasoning)

    logger.info(
        "plan.day.reduce user_id=%s day=%s delta=%s exercises=%s",
        user_id,
        body.day,
        delta,
        len(updated),
    )
    return DayActionOut(
        ok=True,
        user_id=user_id,
        day=body.day,
        message=f"Reduced sets for {body.day} by {delta} (min set safety preserved).",
        plan=plan,
        krr_reasoning=krr_reasoning,
    )


@app.post("/api/plan/day/swap", response_model=DayActionOut)
def swap_plan_day_exercises(body: DaySwapIn):
    user_id = _safe_user_id(body.user_id)
    plan, krr_reasoning = _require_stored_plan(user_id)
    if body.day not in plan:
        raise HTTPException(status_code=404, detail=f"Unknown day: {body.day}")

    exercises = list(plan.get(body.day, []) or [])
    if len(exercises) < 2:
        raise HTTPException(status_code=400, detail="Not enough exercises to swap")

    idx_a = body.index_a if isinstance(body.index_a, int) else 0
    idx_b = body.index_b if isinstance(body.index_b, int) else 1
    if idx_a < 0 or idx_b < 0 or idx_a >= len(exercises) or idx_b >= len(exercises):
        raise HTTPException(status_code=400, detail="Swap indices out of range")

    exercises[idx_a], exercises[idx_b] = exercises[idx_b], exercises[idx_a]
    plan[body.day] = exercises
    save_plan_for_user(user_id, plan, krr_reasoning)

    logger.info(
        "plan.day.swap user_id=%s day=%s index_a=%s index_b=%s",
        user_id,
        body.day,
        idx_a,
        idx_b,
    )
    return DayActionOut(
        ok=True,
        user_id=user_id,
        day=body.day,
        message=f"Swapped exercises at positions {idx_a + 1} and {idx_b + 1} for {body.day}.",
        plan=plan,
        krr_reasoning=krr_reasoning,
    )


@app.get("/api/explanation")
def get_explanation(
    day: str = Query(..., description="Day label, e.g. Day1"),
    user_id: str = Query(..., description="User ID who owns the plan"),
):
    """Return KRR reasoning and LLM explanation for a given day. Requires a stored plan for user_id."""
    user_id = _safe_user_id(user_id)
    stored = load_plan_for_user(user_id)
    if not stored:
        logger.warning("explanation.get user_id=%s day=%s missing_plan=true", user_id, day)
        raise HTTPException(
            status_code=404,
            detail="No plan found for this user. Generate a plan first (POST /api/plan or 'Generate my plan' in chat).",
        )
    plan, krr_reasoning = stored
    if day not in plan:
        logger.warning("explanation.get user_id=%s day=%s unknown_day=true", user_id, day)
        raise HTTPException(status_code=404, detail=f"Unknown day: {day}. Use a day label from your generated plan.")
    exercises = plan.get(day, [])
    day_krr = krr_reasoning.get(day, "")
    skipped = "All exercises for this day target injured muscles." if not exercises else None
    llm = LLM()
    llm_explanation = llm.explain_plan(
        user_id=user_id,
        day=day,
        exercises=exercises,
        skipped_reason=skipped,
        krr_reasoning=day_krr or None,
    )
    logger.info(
        "explanation.get user_id=%s day=%s exercises=%s has_krr=%s llm_chars=%s",
        user_id,
        day,
        len(exercises),
        bool(day_krr),
        len(llm_explanation or ""),
    )
    return {"krr_reasoning": day_krr, "llm_explanation": llm_explanation}


@app.post("/api/chat", response_model=ChatOut)
def chat(body: ChatIn):
    """Chat with the workout planner agent. Includes profile context if available."""
    user_id = _safe_user_id(body.user_id)
    prof = load_profile(user_id)
    stored = load_plan_for_user(user_id)
    has_saved_plan = stored is not None

    plan_summary = body.plan_summary
    krr_summary = body.krr_summary
    if stored:
        saved_plan, saved_krr = stored
        if not plan_summary:
            plan_summary = _summarize_saved_plan(saved_plan)
        if not krr_summary:
            krr_summary = _summarize_saved_krr(saved_krr)
    else:
        # Do not trust client-supplied plan/krr summaries when no server-side plan exists.
        plan_summary = None
        krr_summary = None

    profile_context = None
    if prof.get("onboarding_complete"):
        profile_context = (
            f"User profile: Days/week={prof.get('days_per_week', 5)}, "
            f"Injuries={', '.join(prof.get('injury_muscles', [])) or 'none'}, "
            f"Equipment preferences={', '.join(prof.get('equipment_preferences', [])) or 'none'}."
        )

    kb_context = None
    try:
        # Ground chat in the exercise KB: beginner exercises filtered by injuries.
        df = get_exercises_df()
        injuries = prof.get("injury_muscles", []) or []
        equipment = prof.get("equipment_preferences", []) or []
        load_kb(df, user_id, injuries, equipment_preferences=equipment)
        recommended = query_recommended(user_id)
        sample = recommended[:40]
        kb_context = (
            "Grounding (exercise knowledge base): Only suggest exercises that appear in the DB, "
            "are beginner-friendly, do not target injured muscles, and respect preferred equipment when set.\n"
            f"Available recommended exercises (sample): {', '.join(sample) if sample else '(none)'}"
        )
    except Exception:
        kb_context = None

    combined_context = (profile_context + ("\n" + kb_context if kb_context else "")) if profile_context else kb_context
    llm = LLM()

    if _is_progress_query(body.message):
        structured = _structured_progress_facts(user_id, prof)
        progress_context = (
            (combined_context + "\n\n" if combined_context else "")
            + "Structured facts (must be followed exactly; do not invent values): "
            + structured
        )
        progress_reply = llm.chat(
            user_message=body.message,
            plan_summary=plan_summary,
            krr_summary=krr_summary,
            user_id=user_id,
            profile_context=progress_context,
            has_saved_plan=has_saved_plan,
        )
        append_chat_turn_to_profile(user_id, body.message, progress_reply)
        logger.info(
            "chat.progress user_id=%s message=\"%s\" has_saved_plan=%s llm_chars=%s",
            user_id,
            _clip(body.message, 120),
            stored is not None,
            len(progress_reply or ""),
        )
        return ChatOut(response=progress_reply)

    if stored and _is_schedule_query(body.message):
        saved_plan, _ = stored
        resolved_day = _resolve_day_for_schedule_query(saved_plan, body.message, body.selected_day)
        if resolved_day and resolved_day in saved_plan:
            structured = _structured_schedule_facts(saved_plan, resolved_day)
            schedule_context = (
                (combined_context + "\n\n" if combined_context else "")
                + "Structured facts (must be followed exactly; do not invent exercises or prescriptions): "
                + structured
            )
            schedule_reply = llm.chat(
                user_message=body.message,
                plan_summary=plan_summary,
                krr_summary=krr_summary,
                user_id=user_id,
                profile_context=schedule_context,
                has_saved_plan=has_saved_plan,
            )
            append_chat_turn_to_profile(user_id, body.message, schedule_reply)
            logger.info(
                "chat.schedule user_id=%s message=\"%s\" day=%s exercises=%s selected_day=%s llm_chars=%s",
                user_id,
                _clip(body.message, 120),
                resolved_day,
                len(saved_plan.get(resolved_day, []) or []),
                body.selected_day,
                len(schedule_reply or ""),
            )
            return ChatOut(response=schedule_reply)

    response = llm.chat(
        user_message=body.message,
        plan_summary=plan_summary,
        krr_summary=krr_summary,
        user_id=user_id,
        profile_context=combined_context,
        has_saved_plan=has_saved_plan,
    )
    append_chat_turn_to_profile(user_id, body.message, response)
    logger.info(
        "chat.reply user_id=%s message=\"%s\" onboarding=%s plan_summary=%s krr_summary=%s response_chars=%s",
        user_id,
        _clip(body.message, 120),
        prof.get("onboarding_complete"),
        bool(plan_summary),
        bool(krr_summary),
        len(response or ""),
    )
    return ChatOut(response=response)


def _frontend_index_path() -> str | None:
    index_path = os.path.join(FRONTEND_DIST_DIR, "index.html")
    if os.path.isfile(index_path):
        return index_path
    return None


@app.get("/", include_in_schema=False)
def serve_frontend_root():
    index_path = _frontend_index_path()
    if index_path:
        return FileResponse(index_path)
    return {
        "message": "API running. Frontend build not found.",
        "hint": "Build web app with `npm run build` inside `web/`.",
    }


@app.get("/{full_path:path}", include_in_schema=False)
def serve_frontend_spa(full_path: str):
    if full_path.startswith("api"):
        raise HTTPException(status_code=404, detail="Not found")

    if full_path:
        requested_path = os.path.join(FRONTEND_DIST_DIR, full_path)
        if os.path.isfile(requested_path):
            return FileResponse(requested_path)

    index_path = _frontend_index_path()
    if index_path:
        return FileResponse(index_path)

    raise HTTPException(status_code=404, detail="Frontend build not found")
