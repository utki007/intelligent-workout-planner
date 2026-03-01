"""FastAPI application exposing chat endpoints and static UI."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .chat_service import WorkoutChatService
from .database import WorkoutRepository
from .llm import LLMIntentParser
from .rule_engine import WorkoutRuleEngine


class ChatRequest(BaseModel):
    """Incoming chat payload."""

    message: str = Field(..., min_length=1)
    session_id: str | None = None


class ChatResponse(BaseModel):
    """Outgoing chat payload."""

    session_id: str
    reply: str
    profile: dict[str, Any]
    recommendations: list[dict[str, Any]]
    plan: list[dict[str, Any]]
    llm_used: bool
    pydatalog_output: str = ""


class ProfileResponse(BaseModel):
    """Current session profile."""

    session_id: str
    profile: dict[str, Any]


def _build_chat_service() -> WorkoutChatService:
    db_path = os.getenv("WORKOUT_DB_PATH", "data/workout_planner.db")
    seed_path = os.getenv("EXERCISE_SEED_PATH", "data/exercises_db.json")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    api_key = os.getenv("OPENAI_API_KEY")

    repository = WorkoutRepository(db_path=db_path, seed_json_path=seed_path)
    repository.initialize()

    rule_engine = WorkoutRuleEngine()
    parser = LLMIntentParser(
        model=model,
        api_key=api_key,
        repository=repository,
        rule_engine=rule_engine,
    )

    return WorkoutChatService(repository=repository, rule_engine=rule_engine, intent_parser=parser)


def create_app() -> FastAPI:
    static_dir = Path(__file__).resolve().parent / "static"
    chat_service = _build_chat_service()

    application = FastAPI(
        title="Intelligent Workout Planner",
        description="Chat-driven workout planner with LLM parsing, pyDatalog rules, and SQLite persistence.",
        version="2.0.0",
    )

    application.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @application.get("/")
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @application.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/profile/{session_id}", response_model=ProfileResponse)
    def get_profile(session_id: str) -> ProfileResponse:
        profile = chat_service.repository.get_profile(session_id)
        return ProfileResponse(session_id=session_id, profile=profile)

    @application.post("/api/chat", response_model=ChatResponse)
    def chat(payload: ChatRequest) -> ChatResponse:
        message = payload.message.strip()
        if not message:
            raise HTTPException(status_code=400, detail="Message cannot be empty")

        session_id = payload.session_id or str(uuid.uuid4())

        try:
            result = chat_service.handle_message(session_id=session_id, message=message)
        except FileNotFoundError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        except Exception as error:  # pragma: no cover - runtime safety
            raise HTTPException(status_code=500, detail=f"Internal error: {error}") from error

        return ChatResponse(
            session_id=result.session_id,
            reply=result.reply,
            profile=result.profile,
            recommendations=result.recommendations,
            plan=result.plan,
            llm_used=result.llm_used,
            pydatalog_output=getattr(result, "pydatalog_output", "") or "",
        )

    return application


app = create_app()
