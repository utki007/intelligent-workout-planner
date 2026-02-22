"""LLM message parser used by the chat API."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - handled at runtime
    OpenAI = None  # type: ignore[assignment]


_ALLOWED_INTENTS = {
    "general_chat",
    "update_profile",
    "recommend_exercises",
    "generate_plan",
    "show_profile",
}


@dataclass
class ParsedIntent:
    """Structured intent returned by the LLM parser."""

    intent: str = "general_chat"
    assistant_response: str = ""
    updates: dict[str, Any] = field(default_factory=dict)
    filters: dict[str, Any] = field(default_factory=dict)
    requested_days: int | None = None
    remove_injuries: list[str] = field(default_factory=list)
    llm_used: bool = False


class LLMIntentParser:
    """Converts natural language into planner intents and parameters."""

    def __init__(self, model: str = "gpt-4o-mini", api_key: str | None = None):
        self.model = model
        self._client = OpenAI(api_key=api_key) if (OpenAI is not None and api_key) else None

    @property
    def llm_available(self) -> bool:
        return self._client is not None

    def parse(
        self,
        *,
        message: str,
        profile: dict[str, Any],
        history: list[dict[str, str]],
    ) -> ParsedIntent:
        if self._client is None:
            return self._fallback_parse(message, llm_used=False)

        user_payload = {
            "message": message,
            "profile": profile,
            "recent_history": history[-6:],
        }

        try:
            completion = self._client.chat.completions.create(
                model=self.model,
                temperature=0.1,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
                ],
            )
            content = completion.choices[0].message.content or "{}"
            parsed = json.loads(content)
            return self._normalize_result(parsed, llm_used=True)
        except Exception:
            return self._fallback_parse(message, llm_used=False)

    def _normalize_result(self, payload: dict[str, Any], llm_used: bool) -> ParsedIntent:
        intent = str(payload.get("intent", "general_chat")).strip().lower()
        if intent not in _ALLOWED_INTENTS:
            intent = "general_chat"

        assistant_response = str(payload.get("assistant_response", "")).strip()
        updates = _sanitize_updates(payload.get("updates", {}))
        filters = _sanitize_filters(payload.get("filters", {}))

        requested_days = None
        raw_days = payload.get("requested_days")
        if raw_days is not None:
            try:
                requested_days = max(1, min(7, int(raw_days)))
            except (TypeError, ValueError):
                requested_days = None

        if updates and intent == "general_chat":
            intent = "update_profile"

        remove_injuries = _sanitize_remove_injuries(payload.get("remove_injuries"))

        return ParsedIntent(
            intent=intent,
            assistant_response=assistant_response,
            updates=updates,
            filters=filters,
            requested_days=requested_days,
            remove_injuries=remove_injuries,
            llm_used=llm_used,
        )

    def _fallback_parse(self, message: str, llm_used: bool) -> ParsedIntent:
        text = message.strip()
        lowered = text.lower()

        updates: dict[str, Any] = {}
        filters: dict[str, Any] = {}

        days_match = re.search(r"(\d{1,2})\s*(day|days)", lowered)
        if days_match:
            updates["days_per_week"] = int(days_match.group(1))

        minutes_match = re.search(r"(\d{2,3})\s*(min|mins|minute|minutes)", lowered)
        if minutes_match:
            updates["session_minutes"] = int(minutes_match.group(1))

        for level in ("beginner", "intermediate", "advanced"):
            if level in lowered:
                updates["experience_level"] = level
                break

        goal_keywords = {
            "fat loss": ["fat loss", "lose weight", "cut"],
            "muscle gain": ["build muscle", "muscle gain", "bulk"],
            "strength": ["strength", "stronger"],
            "general fitness": ["fitness", "health"],
        }
        for goal, patterns in goal_keywords.items():
            if any(pattern in lowered for pattern in patterns):
                updates["goal"] = goal
                break

        detected_injuries: list[str] = []
        injury_aliases = {
            "shoulder": "shoulder_injury",
            "knee": "knee_injury",
            "elbow": "elbow_injury",
            "wrist": "wrist_injury",
            "lower back": "lower_back_injury",
            "ankle": "ankle_injury",
            "neck": "neck_injury",
        }
        for token, canonical in injury_aliases.items():
            if token in lowered:
                detected_injuries.append(canonical)

        if detected_injuries:
            updates["injuries"] = list(dict.fromkeys(detected_injuries))

        if "without" in lowered or "no equipment" in lowered:
            if "dumbbell" in lowered:
                updates["excluded_equipment"] = ["dumbbell"]
            elif "barbell" in lowered:
                updates["excluded_equipment"] = ["barbell"]

        body_part_tokens = [
            "chest",
            "back",
            "shoulders",
            "upper arms",
            "lower arms",
            "upper legs",
            "lower legs",
            "waist",
            "cardio",
            "neck",
        ]
        detected_body_parts = [token for token in body_part_tokens if token in lowered]
        if detected_body_parts:
            filters["body_parts"] = detected_body_parts

        target_tokens = [
            "abs",
            "lats",
            "biceps",
            "triceps",
            "glutes",
            "hamstrings",
            "quads",
            "calves",
            "pectorals",
            "delts",
        ]
        detected_targets = [token for token in target_tokens if token in lowered]
        if detected_targets:
            filters["targets"] = detected_targets

        equipment_tokens = [
            "body weight",
            "dumbbell",
            "barbell",
            "cable",
            "kettlebell",
            "band",
            "machine",
        ]
        detected_equipment = [token for token in equipment_tokens if token in lowered]
        if detected_equipment:
            filters["equipment"] = detected_equipment

        limit_match = re.search(r"(\d{1,2})\s*(exercise|exercises|workouts)", lowered)
        if limit_match:
            filters["limit"] = int(limit_match.group(1))

        intent = "general_chat"
        requested_days = None

        if "profile" in lowered:
            intent = "show_profile"
        if any(keyword in lowered for keyword in ["plan", "schedule", "weekly"]):
            intent = "generate_plan"
            if days_match:
                requested_days = int(days_match.group(1))
        elif any(keyword in lowered for keyword in ["recommend", "exercise", "workout"]):
            intent = "recommend_exercises"

        if updates and intent == "general_chat":
            intent = "update_profile"

        if "beginner" in lowered or "intermediate" in lowered or "advanced" in lowered:
            filters["difficulty"] = updates.get("experience_level")

        remove_injuries: list[str] = []
        injury_gone_patterns = ("gone", "healed", "better", "no longer", "recovered", "fixed", "cleared")
        if any(p in lowered for p in injury_gone_patterns):
            for token, canonical in injury_aliases.items():
                if token in lowered:
                    remove_injuries.append(canonical)
        remove_injuries = list(dict.fromkeys(remove_injuries))

        return ParsedIntent(
            intent=intent,
            assistant_response="",
            updates=_sanitize_updates(updates),
            filters=_sanitize_filters(filters),
            requested_days=requested_days,
            remove_injuries=remove_injuries,
            llm_used=llm_used,
        )


def _sanitize_remove_injuries(raw: Any) -> list[str]:
    """Extract and normalize injury names to remove (canonical form)."""
    if not isinstance(raw, list):
        return []
    canonical = {
        "shoulder": "shoulder_injury",
        "knee": "knee_injury",
        "elbow": "elbow_injury",
        "wrist": "wrist_injury",
        "lower back": "lower_back_injury",
        "ankle": "ankle_injury",
        "neck": "neck_injury",
    }
    result: list[str] = []
    for item in raw:
        s = str(item).strip().lower()
        if not s:
            continue
        if s.endswith("_injury"):
            result.append(s)
        else:
            result.append(canonical.get(s, f"{s.replace(' ', '_')}_injury"))
    return list(dict.fromkeys(result))


def _sanitize_updates(raw_updates: Any) -> dict[str, Any]:
    if not isinstance(raw_updates, dict):
        return {}

    cleaned: dict[str, Any] = {}

    if isinstance(raw_updates.get("goal"), str):
        goal = raw_updates["goal"].strip()
        if goal:
            cleaned["goal"] = goal

    if isinstance(raw_updates.get("experience_level"), str):
        level = raw_updates["experience_level"].strip().lower()
        if level in {"beginner", "intermediate", "advanced"}:
            cleaned["experience_level"] = level

    for numeric_key, minimum, maximum in [
        ("days_per_week", 1, 7),
        ("session_minutes", 15, 180),
    ]:
        if numeric_key in raw_updates:
            try:
                value = int(raw_updates[numeric_key])
            except (TypeError, ValueError):
                continue
            cleaned[numeric_key] = max(minimum, min(maximum, value))

    for list_key in ["injuries", "excluded_equipment", "focus_muscles"]:
        values = raw_updates.get(list_key)
        if isinstance(values, list):
            normalized = [str(item).strip() for item in values if str(item).strip()]
            if normalized:
                cleaned[list_key] = normalized

    return cleaned


def _sanitize_filters(raw_filters: Any) -> dict[str, Any]:
    if not isinstance(raw_filters, dict):
        return {}

    cleaned: dict[str, Any] = {}

    for list_key in ["body_parts", "targets", "equipment"]:
        values = raw_filters.get(list_key)
        if isinstance(values, list):
            normalized = [str(item).strip() for item in values if str(item).strip()]
            if normalized:
                cleaned[list_key] = normalized

    if isinstance(raw_filters.get("difficulty"), str):
        difficulty = raw_filters["difficulty"].strip().lower()
        if difficulty in {"beginner", "intermediate", "advanced"}:
            cleaned["difficulty"] = difficulty

    if "limit" in raw_filters:
        try:
            limit = int(raw_filters["limit"])
            cleaned["limit"] = max(1, min(24, limit))
        except (TypeError, ValueError):
            pass

    return cleaned


_SYSTEM_PROMPT = """
You are a personal trainer for a workout-planning app. You reason about the user's message and output structured data that drives a PyDatalog rule engine: the engine uses the user's profile (injuries, equipment, level) as rules to forbid unsafe exercises and generate safe plans. Your job is to keep those rules in sync with what the user says and to answer in a supportive, clear, personal-trainer tone.

Return only valid JSON with this exact schema:
{
  "intent": "general_chat | update_profile | recommend_exercises | generate_plan | show_profile",
  "assistant_response": "short, encouraging response in a personal-trainer voice",
  "updates": {
    "goal": "string",
    "experience_level": "beginner|intermediate|advanced",
    "days_per_week": 1,
    "session_minutes": 45,
    "injuries": ["shoulder_injury|knee_injury|elbow_injury|wrist_injury|lower_back_injury|ankle_injury|neck_injury"],
    "excluded_equipment": ["string"],
    "focus_muscles": ["string"]
  },
  "remove_injuries": ["canonical injury names to remove from the user's list"],
  "filters": {
    "body_parts": ["string"],
    "targets": ["string"],
    "equipment": ["string"],
    "difficulty": "beginner|intermediate|advanced",
    "limit": 8
  },
  "requested_days": 3
}

Injury rules (critical for PyDatalog):
- User says an injury EXISTS (e.g. "I have a shoulder injury", "with a bad knee", "my wrist hurts") → add that injury to "updates.injuries" so the rule engine forbids exercises that stress it. Use canonical names: shoulder_injury, knee_injury, elbow_injury, wrist_injury, lower_back_injury, ankle_injury, neck_injury.
- User says an injury is GONE or BETTER (e.g. "my shoulder is better", "knee is healed", "no longer have wrist pain", "recovered from ankle injury") → add that injury to "remove_injuries" only. Do NOT put it in "updates.injuries". Example: "My shoulder is all good now" → "remove_injuries": ["shoulder_injury"], "updates": {}.
- If the user both adds and removes injuries in one message, put new injuries in "updates.injuries" and healed ones in "remove_injuries".
- Any constraint mentioned in the same message as a question (e.g. "With a shoulder injury, what chest exercises can I do?") must be in "updates" so the engine uses it for this turn.

Personal trainer behavior:
- assistant_response should be brief, encouraging, and actionable (e.g. "Got it—I've noted your shoulder. Here are chest options that keep the load off it." or "Good to hear your knee is better. I've removed it from your constraints.").
- When recommending or planning, acknowledge their constraints so they feel heard.

Intent and filters:
- "Recommend exercises" / "what X exercises?" → intent: "recommend_exercises", set filters (body_parts, targets, equipment, difficulty, limit) from the request.
- "Generate a plan" / "weekly plan" → intent: "generate_plan"; use requested_days if mentioned.
- "Update my profile" / "I train 4 days" / "no barbell" → intent: "update_profile" or keep other intent and put constraints in updates.
- "Show my profile" → intent: "show_profile".
- Use empty [] or {} where no values apply. Omit "remove_injuries" or use [] when nothing is being removed.
""".strip()
