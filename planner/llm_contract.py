"""
Strict contract for planner LLM outputs.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError


class LLMExercise(BaseModel):
    name: str = Field(min_length=1)
    target: str = Field(min_length=1)
    sets: int = Field(ge=1, le=10)
    reps: int = Field(ge=1, le=50)
    hint: str | None = Field(default=None)


class LLMDay(BaseModel):
    day: str = Field(min_length=1)
    theme: str = Field(min_length=1)
    muscles: list[str] = Field(default_factory=list)
    exercises: list[LLMExercise] = Field(default_factory=list)


class LLMPlanPayload(BaseModel):
    days: list[LLMDay] = Field(default_factory=list)
    summary: str | None = None


def parse_llm_plan_payload(payload: Any) -> tuple[dict[str, Any] | None, list[str]]:
    """
    Parse raw LLM payload into a validated dict.
    Returns (payload_dict_or_none, errors).
    """
    if not isinstance(payload, dict):
        return None, ["payload_not_object"]

    try:
        parsed = LLMPlanPayload.model_validate(payload)
        return parsed.model_dump(), []
    except ValidationError as exc:
        errors: list[str] = []
        for item in exc.errors():
            loc = ".".join(str(part) for part in item.get("loc", []))
            msg = item.get("msg", "validation_error")
            errors.append(f"{loc}:{msg}")
        return None, errors or ["schema_validation_failed"]
