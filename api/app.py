"""
FastAPI backend for the Node.js frontend: plan generation, feedback, and chat with the agent.
Run from project root: uvicorn api.app:app --reload --port 8000
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from typing import Literal

import pandas as pd

# Project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agent.agent_state import AgentGoals, AgentState
from agent.krr import load_kb, query_recommended
from agent.llm import LLM
from agent.mcp import run_plan_cycle


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
EXERCISES_PATH = os.path.join(DATA_DIR, "exercises_db.xlsx")


def _clip(value: str | None, limit: int = 160) -> str:
    s = (value or "").strip().replace("\n", " ")
    if len(s) <= limit:
        return s
    return s[:limit] + "..."


def _safe_user_id(user_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in (user_id or "").strip()) or "default"


def _plan_store_path(user_id: str) -> str:
    return os.path.join(DATA_DIR, f"plan_{_safe_user_id(user_id)}.json")


def _profile_store_path(user_id: str) -> str:
    return os.path.join(DATA_DIR, f"profile_{_safe_user_id(user_id)}.json")


def load_profile(user_id: str) -> dict:
    """Load saved profile: onboarding, injuries, intensity, feedback_history."""
    path = _profile_store_path(user_id)
    default = {
        "injury_muscles": [],
        "intensity": 3,
        "feedback_history": [],
        "onboarding_complete": False,
        "fitness_goals": "",
        "days_per_week": 5,
    }
    if not os.path.isfile(path):
        logger.debug("profile.load user_id=%s found=false", user_id)
        return default
    try:
        with open(path) as f:
            data = json.load(f)
        prof = {
            "injury_muscles": data.get("injury_muscles", []),
            "intensity": data.get("intensity", 3),
            "feedback_history": data.get("feedback_history", []),
            "onboarding_complete": data.get("onboarding_complete", False),
            "fitness_goals": data.get("fitness_goals", ""),
            "days_per_week": data.get("days_per_week", 5),
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
    intensity: float | None = None,
    onboarding_complete: bool | None = None,
    fitness_goals: str | None = None,
    days_per_week: int | None = None,
) -> None:
    """Update and persist profile. Merges with existing."""
    prof = load_profile(user_id)
    if injury_muscles is not None:
        prof["injury_muscles"] = injury_muscles
    if intensity is not None:
        prof["intensity"] = max(1, min(5, float(intensity)))
    if onboarding_complete is not None:
        prof["onboarding_complete"] = bool(onboarding_complete)
    if fitness_goals is not None:
        prof["fitness_goals"] = str(fitness_goals).strip()
    if days_per_week is not None:
        prof["days_per_week"] = max(3, min(7, int(days_per_week)))
    path = _profile_store_path(user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(prof, f, indent=2)
    logger.info(
        "profile.save user_id=%s onboarding=%s days_per_week=%s intensity=%s injuries=%s goals_set=%s",
        user_id,
        prof.get("onboarding_complete"),
        prof.get("days_per_week"),
        prof.get("intensity"),
        len(prof.get("injury_muscles", [])),
        bool(prof.get("fitness_goals")),
    )


def append_feedback_to_profile(user_id: str, soreness: float) -> None:
    """Append feedback to profile history."""
    prof = load_profile(user_id)
    hist = prof.get("feedback_history", [])
    hist.append({"soreness": soreness})
    prof["feedback_history"] = hist[-20:]  # keep last 20
    path = _profile_store_path(user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(prof, f, indent=2)
    logger.info(
        "profile.feedback.append user_id=%s soreness=%s history_len=%s",
        user_id,
        soreness,
        len(prof.get("feedback_history", [])),
    )


def save_plan_for_user(user_id: str, plan: dict, krr_reasoning: dict) -> None:
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
    df = pd.read_excel(EXERCISES_PATH)
    df.columns = [str(c).strip().lower() for c in df.columns]
    logger.debug("db.exercises loaded rows=%s cols=%s", len(df), list(df.columns))
    return df


# Request/response models
class ProfileIn(BaseModel):
    user_id: str
    name: str | None = None
    experience_level: str | None = "beginner"
    injury_muscles: list[str] = []
    days_per_week: int | None = None


class PlanOut(BaseModel):
    plan: dict
    krr_reasoning: dict
    day_themes: dict
    day_theme_muscles: dict
    evaluation_ok: bool
    suggestions: list[str]


class FeedbackIn(BaseModel):
    user_id: str
    soreness: float  # 1-5


class ChatIn(BaseModel):
    user_id: str
    message: str
    plan_summary: str | None = None
    krr_summary: str | None = None


class ChatOut(BaseModel):
    response: str


class IntentIn(BaseModel):
    user_id: str | None = None
    message: str


class IntentOut(BaseModel):
    intent: Literal["generate_plan", "profile_update", "chat"]
    days_per_week: int | None = None
    assistant_reply: str | None = None


REASONING_EXPLAINED = """
The planner uses a Knowledge Base (KRR) with two rules:

1. FORBIDDEN(User, Exercise): An exercise is forbidden for you if it trains any muscle you listed as injured.
   → We never recommend exercises that target injured muscles.

2. RECOMMENDED(User, Exercise): An exercise is recommended if it's in the DB, marked "beginner", and NOT forbidden.
   → The weekly plan is built by picking from recommended exercises for each day (Chest, Back, Legs, Arms, Shoulders).

Facts come from: (a) the exercise DB (name, target muscle, difficulty), (b) your profile (injured muscles).
The agent also tracks your state (soreness, weekly volume) to adjust sets and prioritize muscles over time.
"""


class ProfileUpdateIn(BaseModel):
    user_id: str
    injury_muscles: list[str] | None = None
    intensity: float | None = None
    onboarding_complete: bool | None = None
    fitness_goals: str | None = None
    days_per_week: int | None = None


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
    """Return saved profile: injuries, intensity, feedback history. Merged with agent state for soreness."""
    prof = load_profile(user_id)
    # Also include agent state (soreness, volume) if exists
    state = AgentState.load(user_id)
    if state:
        prof["soreness_per_muscle"] = state.soreness_per_muscle
        prof["weekly_volume_per_muscle"] = state.weekly_volume_per_muscle
    logger.info(
        "profile.get user_id=%s onboarding=%s days_per_week=%s injuries=%s feedback=%s has_state=%s",
        user_id,
        prof.get("onboarding_complete"),
        prof.get("days_per_week"),
        len(prof.get("injury_muscles", [])),
        len(prof.get("feedback_history", [])),
        state is not None,
    )
    return prof


@app.put("/api/profile")
def update_profile(body: ProfileUpdateIn):
    """Save profile fields: injuries, intensity, onboarding data."""
    save_profile(
        body.user_id,
        injury_muscles=body.injury_muscles,
        intensity=body.intensity,
        onboarding_complete=body.onboarding_complete,
        fitness_goals=body.fitness_goals,
        days_per_week=body.days_per_week,
    )
    logger.info(
        "profile.update user_id=%s onboarding=%s days_per_week=%s intensity=%s injuries=%s goals_set=%s",
        body.user_id,
        body.onboarding_complete,
        body.days_per_week,
        body.intensity,
        None if body.injury_muscles is None else len(body.injury_muscles),
        bool((body.fitness_goals or "").strip()) if body.fitness_goals is not None else None,
    )
    return {"ok": True}


@app.post("/api/intent", response_model=IntentOut)
def detect_intent(body: IntentIn):
    """Resolve user message intent so frontend behavior doesn't rely on local regex logic."""
    payload = resolve_intent(body.message) or {}
    intent = payload.get("intent", "chat")
    if intent not in {"generate_plan", "profile_update", "chat"}:
        intent = "chat"
    days = payload.get("days_per_week")
    if isinstance(days, (int, float)):
        days = int(days)
        if days < 2 or days > 7:
            days = None
    elif isinstance(days, str):
        normalized = days.strip().lower()
        word_to_num = {
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
        if isinstance(days, int) and (days < 2 or days > 7):
            days = None
    else:
        days = None
    reply = payload.get("assistant_reply")
    if not isinstance(reply, str) or not reply.strip():
        reply = None
    logger.info(
        "intent.detect user_id=%s message=\"%s\" intent=%s days_per_week=%s has_reply=%s",
        body.user_id,
        _clip(body.message, 120),
        intent,
        days,
        bool(reply),
    )
    return IntentOut(intent=intent, days_per_week=days, assistant_reply=reply)


@app.get("/api/db")
def get_db():
    """Return what's stored: exercises from exercises_db.xlsx and saved user states from data/."""
    import glob
    exercises = []
    try:
        df = get_exercises_df()
        exercises = df[["name", "target", "difficulty"]].fillna("").astype(str).to_dict("records")
    except Exception:
        pass
    user_states = []
    try:
        for path in glob.glob(os.path.join(DATA_DIR, "agent_state_*.json")):
            name = os.path.basename(path)
            user_id = name.replace("agent_state_", "").replace(".json", "")
            with open(path) as f:
                data = json.load(f)
            user_states.append({
                "user_id": user_id,
                "soreness_per_muscle": data.get("soreness_per_muscle", {}),
                "weekly_volume_per_muscle": data.get("weekly_volume_per_muscle", {}),
                "missed_sessions": data.get("missed_sessions", []),
            })
    except Exception:
        pass
    return {
        "exercises": exercises,
        "user_states": user_states,
        "source": "data/exercises_db.xlsx and data/agent_state_*.json",
    }


@app.get("/api/reasoning")
def get_reasoning():
    """Return how KRR reasoning works: rules and short explanation."""
    return {
        "how_it_works": REASONING_EXPLAINED.strip(),
        "rules": [
            "forbidden(User, Exercise) ← exercise trains a muscle M AND user has M injured",
            "recommended(User, Exercise) ← exercise in DB AND difficulty=beginner AND NOT forbidden(User, Exercise)",
        ],
        "facts_from": ["exercises_db.xlsx (name, target, difficulty)", "your injuries list"],
    }


@app.post("/api/plan", response_model=PlanOut)
def generate_plan(profile: ProfileIn):
    """Generate a weekly plan. Uses saved injuries if profile.injury_muscles is empty."""
    try:
        df = get_exercises_df()
    except HTTPException:
        raise
    # Load full profile so we can respect stored limitations (injuries, intensity, days/week)
    stored_profile = load_profile(profile.user_id)
    injuries = profile.injury_muscles if profile.injury_muscles else stored_profile["injury_muscles"]
    logger.info(
        "plan.generate.start user_id=%s onboarding=%s days_per_week=%s injuries_in_request=%s injuries_effective=%s intensity=%s",
        profile.user_id,
        stored_profile.get("onboarding_complete"),
        stored_profile.get("days_per_week"),
        len(profile.injury_muscles or []),
        len(injuries or []),
        stored_profile.get("intensity"),
    )
    if injuries:
        save_profile(profile.user_id, injury_muscles=injuries)
        stored_profile["injury_muscles"] = injuries

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
    if requested_days is not None:
        try:
            requested_days = int(requested_days)
        except (TypeError, ValueError):
            requested_days = None
        if requested_days is not None and (requested_days < 2 or requested_days > 7):
            requested_days = None

    user_profile = {
        "user_id": profile.user_id,
        "name": profile.name or profile.user_id,
        "experience_level": profile.experience_level or "beginner",
        "injury_muscles": injuries,
        # Respect preferred training frequency unless an override was explicitly requested
        "days_per_week": requested_days or stored_profile.get("days_per_week", 5),
        "intensity": intensity,
    }
    plan, evaluation_ok, suggestions, krr_reasoning, day_themes, day_theme_muscles = run_plan_cycle(
        profile.user_id, df, user_profile, goals=goals
    )
    total_exercises = sum(len(v or []) for v in plan.values())
    logger.info(
        "plan.generate.done user_id=%s days=%s exercises=%s evaluation_ok=%s suggestions=%s max_sets=%s",
        profile.user_id,
        len(plan),
        total_exercises,
        evaluation_ok,
        len(suggestions or []),
        goals.max_sets_per_muscle_per_week,
    )
    save_plan_for_user(profile.user_id, plan, krr_reasoning)
    return PlanOut(
        plan=plan,
        krr_reasoning=krr_reasoning,
        day_themes=day_themes,
        day_theme_muscles=day_theme_muscles,
        evaluation_ok=evaluation_ok,
        suggestions=suggestions or [],
    )


@app.post("/api/feedback")
def submit_feedback(body: FeedbackIn):
    """Submit end-of-week soreness; updates AgentState and appends to profile feedback history."""
    state = AgentState.get_or_create(body.user_id)
    state.update_from_feedback(overall_soreness=body.soreness)
    state.save()
    append_feedback_to_profile(body.user_id, body.soreness)
    logger.info("feedback.submit user_id=%s soreness=%s", body.user_id, body.soreness)
    return {"ok": True}


@app.get("/api/explanation")
def get_explanation(
    day: str = Query(..., description="Day label, e.g. Day1"),
    user_id: str = Query(..., description="User ID who owns the plan"),
):
    """Return KRR reasoning and LLM explanation for a given day. Requires a stored plan for user_id."""
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
    prof = load_profile(body.user_id)
    profile_context = None
    if prof.get("onboarding_complete"):
        profile_context = f"User profile: Goals={prof.get('fitness_goals', '')}, Days/week={prof.get('days_per_week', 5)}, Injuries={', '.join(prof.get('injury_muscles', [])) or 'none'}."
    kb_context = None
    try:
        # Ground chat in the exercise KB: beginner exercises filtered by injuries.
        df = get_exercises_df()
        injuries = prof.get("injury_muscles", []) or []
        load_kb(df, body.user_id, injuries)
        recommended = query_recommended(body.user_id)
        sample = recommended[:40]
        kb_context = (
            "Grounding (exercise knowledge base): Only suggest exercises that appear in the DB, "
            "are beginner-friendly, and do not target injured muscles.\n"
            f"Available recommended exercises (sample): {', '.join(sample) if sample else '(none)'}"
        )
    except Exception:
        kb_context = None
    llm = LLM()
    response = llm.chat(
        user_message=body.message,
        plan_summary=body.plan_summary,
        krr_summary=body.krr_summary,
        user_id=body.user_id,
        profile_context=(profile_context + ("\n" + kb_context if kb_context else "")) if profile_context else kb_context,
    )
    logger.info(
        "chat.reply user_id=%s message=\"%s\" onboarding=%s plan_summary=%s krr_summary=%s response_chars=%s",
        body.user_id,
        _clip(body.message, 120),
        prof.get("onboarding_complete"),
        bool(body.plan_summary),
        bool(body.krr_summary),
        len(response or ""),
    )
    return ChatOut(response=response)
