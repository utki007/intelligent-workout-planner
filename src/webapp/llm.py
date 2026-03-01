"""LLM message parser with OpenAI tool-calling for the chat API.

The LLM can call backend tools (exercise search, safety check, injury
lookup) to gather real data from the database and pyDatalog rule engine
before producing its structured JSON response.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .database import WorkoutRepository
    from .rule_engine import WorkoutRuleEngine

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - handled at runtime
    OpenAI = None  # type: ignore[assignment]

from .rule_engine import INJURY_TO_RESTRICTED_MUSCLES

logger = logging.getLogger(__name__)

_ALLOWED_INTENTS = {
    "general_chat",
    "update_profile",
    "recommend_exercises",
    "generate_plan",
    "show_profile",
}

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# OpenAI tool (function-calling) definitions
# ---------------------------------------------------------------------------

_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_exercises",
            "description": (
                "Search the exercise database with optional filters. "
                "Returns exercises with name, bodyPart, target, equipment, difficulty."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "body_parts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Body-part filter, e.g. ['chest','back','shoulders',"
                            "'upper legs','lower legs','waist','upper arms',"
                            "'lower arms','cardio','neck']"
                        ),
                    },
                    "targets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Target-muscle filter, e.g. ['pectorals','lats',"
                            "'biceps','triceps','glutes','quads','hamstrings',"
                            "'delts','abs','calves']"
                        ),
                    },
                    "equipment": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Equipment filter, e.g. ['barbell','dumbbell',"
                            "'body weight','cable','kettlebell','band','machine']"
                        ),
                    },
                    "difficulty": {
                        "type": "string",
                        "enum": ["beginner", "intermediate", "advanced"],
                        "description": "Difficulty level filter",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 10, max 20)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_safe_exercises",
            "description": (
                "Search exercises AND filter out any blocked by the user's "
                "injuries using the pyDatalog rule engine. Returns only safe "
                "exercises together with counts of candidates vs blocked."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "injuries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "User injuries in canonical form, e.g. "
                            "['shoulder_injury','knee_injury']"
                        ),
                    },
                    "body_parts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Body-part filter",
                    },
                    "targets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Target-muscle filter",
                    },
                    "equipment": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Equipment filter",
                    },
                    "difficulty": {
                        "type": "string",
                        "enum": ["beginner", "intermediate", "advanced"],
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max safe exercises to return (default 10)",
                    },
                },
                "required": ["injuries"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_injury_restrictions",
            "description": (
                "Look up which muscles and body parts are restricted/blocked "
                "by a given injury. Useful for understanding what to avoid."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "injury_name": {
                        "type": "string",
                        "description": (
                            "Canonical injury name: shoulder_injury, "
                            "knee_injury, elbow_injury, wrist_injury, "
                            "lower_back_injury, ankle_injury, or neck_injury"
                        ),
                    },
                },
                "required": ["injury_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_exercise_details",
            "description": (
                "Get full details about a specific exercise by name, "
                "including instructions, secondary muscles, and whether "
                "it is safe for the user's current injuries."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "exercise_name": {
                        "type": "string",
                        "description": "The exercise name to look up",
                    },
                    "injuries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional user injuries to check safety",
                    },
                },
                "required": ["exercise_name"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Parser class
# ---------------------------------------------------------------------------

_MAX_TOOL_ROUNDS = 6


class LLMIntentParser:
    """Converts natural language into planner intents and parameters.

    When *repository* and *rule_engine* are supplied the parser exposes
    backend tools to the LLM via OpenAI function-calling so the model can
    query exercises, check safety rules, and look up injury restrictions
    before producing its structured JSON response.
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
        repository: WorkoutRepository | None = None,
        rule_engine: WorkoutRuleEngine | None = None,
    ):
        self.model = model
        self._client = (
            OpenAI(api_key=api_key)
            if (OpenAI is not None and api_key)
            else None
        )
        self._repository = repository
        self._rule_engine = rule_engine

    @property
    def llm_available(self) -> bool:
        return self._client is not None

    @property
    def _tools_available(self) -> bool:
        return self._repository is not None and self._rule_engine is not None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(user_payload, ensure_ascii=True),
            },
        ]

        try:
            use_tools = self._tools_available

            for _ in range(_MAX_TOOL_ROUNDS):
                kwargs: dict[str, Any] = {
                    "model": self.model,
                    "temperature": 0.1,
                    "messages": messages,
                }
                if use_tools:
                    kwargs["tools"] = _TOOL_DEFINITIONS
                else:
                    kwargs["response_format"] = {"type": "json_object"}

                completion = self._client.chat.completions.create(**kwargs)
                assistant_msg = completion.choices[0].message

                if use_tools and assistant_msg.tool_calls:
                    messages.append(assistant_msg)  # type: ignore[arg-type]
                    for tool_call in assistant_msg.tool_calls:
                        fn_name = tool_call.function.name
                        fn_args = json.loads(tool_call.function.arguments)
                        result = self._execute_tool(fn_name, fn_args)
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "content": json.dumps(
                                    result,
                                    ensure_ascii=True,
                                    default=str,
                                ),
                            }
                        )
                    continue

                content = assistant_msg.content or "{}"
                return self._parse_llm_json(content)

            # Exhausted tool-call rounds — force a final JSON response.
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Provide your final JSON response now, using the "
                        "schema from the system prompt."
                    ),
                }
            )
            completion = self._client.chat.completions.create(
                model=self.model,
                temperature=0.1,
                response_format={"type": "json_object"},
                messages=messages,
            )
            content = completion.choices[0].message.content or "{}"
            return self._parse_llm_json(content)

        except Exception:
            logger.exception("LLM tool-calling parse failed; falling back to regex")
            return self._fallback_parse(message, llm_used=False)

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    def _execute_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        handlers: dict[str, Any] = {
            "search_exercises": self._tool_search_exercises,
            "get_safe_exercises": self._tool_get_safe_exercises,
            "get_injury_restrictions": self._tool_get_injury_restrictions,
            "get_exercise_details": self._tool_get_exercise_details,
        }
        handler = handlers.get(name)
        if handler is None:
            return {"error": f"Unknown tool: {name}"}
        try:
            return handler(arguments)
        except Exception as exc:
            logger.warning("Tool %s raised: %s", name, exc)
            return {"error": str(exc)}

    def _tool_search_exercises(self, args: dict[str, Any]) -> dict[str, Any]:
        assert self._repository is not None
        limit = min(int(args.get("limit", 10)), 20)
        results = self._repository.search_exercises(
            body_parts=args.get("body_parts"),
            targets=args.get("targets"),
            equipment=args.get("equipment"),
            difficulty=args.get("difficulty"),
            limit=limit,
        )
        return {
            "count": len(results),
            "exercises": [_compact(ex) for ex in results],
        }

    def _tool_get_safe_exercises(self, args: dict[str, Any]) -> dict[str, Any]:
        assert self._repository is not None and self._rule_engine is not None
        injuries = args.get("injuries", [])
        limit = min(int(args.get("limit", 10)), 20)
        candidate_limit = max(60, limit * 6)

        candidates = self._repository.search_exercises(
            body_parts=args.get("body_parts"),
            targets=args.get("targets"),
            equipment=args.get("equipment"),
            difficulty=args.get("difficulty"),
            limit=candidate_limit,
        )
        safe = self._rule_engine.filter_safe_exercises(candidates, injuries)
        blocked_count = len(candidates) - len(safe)

        return {
            "total_candidates": len(candidates),
            "blocked_by_injuries": blocked_count,
            "safe_count": len(safe),
            "exercises": [_compact(ex) for ex in safe[:limit]],
        }

    def _tool_get_injury_restrictions(self, args: dict[str, Any]) -> dict[str, Any]:
        injury = args.get("injury_name", "").strip().lower()
        restricted = INJURY_TO_RESTRICTED_MUSCLES.get(injury)
        if restricted is None:
            return {
                "injury": injury,
                "known": False,
                "message": (
                    f"Unknown injury '{injury}'. Known injuries: "
                    f"{list(INJURY_TO_RESTRICTED_MUSCLES.keys())}"
                ),
            }
        return {
            "injury": injury,
            "known": True,
            "restricted_muscles": restricted,
            "description": (
                f"{injury} restricts exercises targeting: "
                f"{', '.join(restricted)}"
            ),
        }

    def _tool_get_exercise_details(self, args: dict[str, Any]) -> dict[str, Any]:
        assert self._repository is not None
        name = args.get("exercise_name", "").strip()
        injuries: list[str] = args.get("injuries", [])

        matches = self._repository.find_exercise_by_name(name)
        if not matches:
            return {"found": False, "message": f"No exercise found matching '{name}'"}

        match = matches[0]
        result: dict[str, Any] = {
            "found": True,
            "name": match["name"],
            "bodyPart": match["bodyPart"],
            "target": match["target"],
            "secondaryMuscles": match.get("secondaryMuscles", []),
            "equipment": match["equipment"],
            "difficulty": match["difficulty"],
            "instructions": match.get("instructions", []),
        }

        if injuries and self._rule_engine is not None:
            safe = self._rule_engine.filter_safe_exercises([match], injuries)
            result["safe_for_user"] = len(safe) > 0
            if not safe:
                restricted: set[str] = set()
                for inj in injuries:
                    restricted.update(
                        INJURY_TO_RESTRICTED_MUSCLES.get(inj, [])
                    )
                target_muscles = {match["target"].lower()} | {
                    m.lower() for m in match.get("secondaryMuscles", [])
                }
                overlap = restricted & target_muscles
                if overlap:
                    result["blocked_reason"] = (
                        f"Targets {', '.join(sorted(overlap))} which is "
                        f"restricted by your injuries"
                    )

        return result

    # ------------------------------------------------------------------
    # Response normalisation helpers
    # ------------------------------------------------------------------

    def _parse_llm_json(self, content: str) -> ParsedIntent:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
            else:
                return ParsedIntent(
                    intent="general_chat",
                    assistant_response=content.strip(),
                    llm_used=True,
                )
        return self._normalize_result(parsed, llm_used=True)

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

    # ------------------------------------------------------------------
    # Regex fallback (no LLM available)
    # ------------------------------------------------------------------

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
            "chest", "back", "shoulders", "upper arms", "lower arms",
            "upper legs", "lower legs", "waist", "cardio", "neck",
        ]
        detected_body_parts = [token for token in body_part_tokens if token in lowered]
        if detected_body_parts:
            filters["body_parts"] = detected_body_parts

        target_tokens = [
            "abs", "lats", "biceps", "triceps", "glutes",
            "hamstrings", "quads", "calves", "pectorals", "delts",
        ]
        detected_targets = [token for token in target_tokens if token in lowered]
        if detected_targets:
            filters["targets"] = detected_targets

        equipment_tokens = [
            "body weight", "dumbbell", "barbell", "cable",
            "kettlebell", "band", "machine",
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
        injury_gone_patterns = (
            "gone", "healed", "better", "no longer",
            "recovered", "fixed", "cleared",
        )
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


# ---------------------------------------------------------------------------
# Compact exercise dict for tool responses (keep token count small)
# ---------------------------------------------------------------------------

def _compact(exercise: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": exercise.get("name"),
        "bodyPart": exercise.get("bodyPart"),
        "target": exercise.get("target"),
        "equipment": exercise.get("equipment"),
        "difficulty": exercise.get("difficulty"),
    }


# ---------------------------------------------------------------------------
# Sanitisation helpers (unchanged from original)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """
You are a personal trainer for a workout-planning app backed by an exercise database and a PyDatalog rule engine.

YOU HAVE TOOLS — use them:
• search_exercises  — query the exercise DB by body part, target muscle, equipment, or difficulty.
• get_safe_exercises — search AND filter out exercises blocked by the user's injuries (pyDatalog rules). Use this whenever the user has injuries.
• get_injury_restrictions — look up which muscles an injury restricts.
• get_exercise_details — full details for a single exercise (instructions, secondary muscles, safety check).

WHEN TO CALL TOOLS (important):
1. User asks for exercise recommendations → call get_safe_exercises (or search_exercises if no injuries) so you can reference real exercises.
2. User mentions an injury → call get_injury_restrictions to understand what's affected.
3. User asks about a specific exercise → call get_exercise_details.
4. User asks for a plan → call get_safe_exercises for each relevant body-part split to see what's available.
5. You may call MULTIPLE tools in one turn if needed.

Call tools BEFORE writing your final JSON response so your answer is grounded in real data.

FINAL RESPONSE FORMAT:
After gathering information, return ONLY valid JSON with this schema (no markdown fences):
{
  "intent": "general_chat | update_profile | recommend_exercises | generate_plan | show_profile",
  "assistant_response": "short, encouraging response in a personal-trainer voice — you MAY now mention specific exercises you found via tools",
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
- assistant_response should be brief, encouraging, and actionable.
- Because you have tool access, you CAN reference specific exercises you found.
- Still speak as if the backend planner makes the final selections; the tool results inform your suggestions.

Intent and filters:
- "Recommend exercises" / "what X exercises?" → intent: "recommend_exercises", set filters from request.
- "Generate a plan" / "weekly plan" → intent: "generate_plan"; use requested_days if mentioned.
- "Update my profile" / "I train 4 days" / "no barbell" → intent: "update_profile".
- "Show my profile" → intent: "show_profile".
- Use empty [] or {} where no values apply.
""".strip()
