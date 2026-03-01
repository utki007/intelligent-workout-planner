"""LLM message parser with OpenAI tool-calling for the chat API.

The LLM can call backend tools (exercise search, safety check, injury
lookup, KRR reasoning) to gather real data from the database, pyDatalog
rule engine, and KRR knowledge base before producing its structured JSON
response.
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

from .rule_engine import INJURY_ALIASES, INJURY_TO_RESTRICTED_MUSCLES

try:
    from krr.knowledge_base import DEFAULT_RECOVERY_DAYS
except ImportError:
    DEFAULT_RECOVERY_DAYS = {
        "glutes": 2, "quadriceps": 2, "hamstrings": 2,
        "pectorals": 1, "delts": 1, "traps": 1, "lats": 1,
        "biceps": 1, "triceps": 1, "calves": 1, "forearms": 0, "abs": 0,
    }

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
# Helpers: resolve injury aliases everywhere
# ---------------------------------------------------------------------------


def _resolve_injury_alias(raw: str) -> str | None:
    """Map a free-text injury mention to its canonical form using INJURY_ALIASES."""
    key = raw.strip().lower()
    if not key:
        return None
    canonical = INJURY_ALIASES.get(key)
    if canonical:
        return canonical
    if key.endswith("_injury") and key in INJURY_TO_RESTRICTED_MUSCLES:
        return key
    return None


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
                "exercises together with counts of candidates vs blocked, plus "
                "a KRR reasoning trace."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "injuries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "User injuries in canonical form, e.g. "
                            "['shoulder_injury','knee_injury']. Also accepts "
                            "aliases like 'shoulder', 'knee pain', etc."
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
                "by a given injury. Accepts aliases like 'shoulder', "
                "'knee pain', 'back pain' as well as canonical names."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "injury_name": {
                        "type": "string",
                        "description": (
                            "Injury name — canonical (shoulder_injury, "
                            "knee_injury, …) or alias (shoulder, knee pain, "
                            "back pain, …)"
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
                "including instructions, secondary muscles, safety check, "
                "and a KRR explanation of why it is safe or forbidden."
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
                        "description": (
                            "Optional user injuries to check safety "
                            "(canonical or alias form)"
                        ),
                    },
                },
                "required": ["exercise_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_muscle_recovery_info",
            "description": (
                "Return the KRR knowledge base's muscle recovery data — "
                "how many rest days each muscle group needs between sessions. "
                "Optionally filter to specific muscles."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "muscles": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional list of muscles to look up, e.g. "
                            "['glutes','pectorals']. Omit for all muscles."
                        ),
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_krr_reasoning",
            "description": (
                "Generate a Knowledge Representation & Reasoning (KRR) "
                "trace showing pyDatalog-style facts, rules, and inference "
                "for a set of exercises and injuries. Shows WHY each "
                "exercise is recommended or forbidden."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "injuries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "User injuries (canonical or alias)",
                    },
                    "exercise_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Exercises to reason about. If omitted, searches "
                            "by body_parts instead."
                        ),
                    },
                    "body_parts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Body parts to search exercises for (used when "
                            "exercise_names is omitted)"
                        ),
                    },
                },
                "required": ["injuries"],
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
    query exercises, check safety rules, look up injury restrictions, and
    retrieve KRR reasoning traces before producing its structured JSON
    response.
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
        repository: WorkoutRepository | None = None,
        rule_engine: WorkoutRuleEngine | None = None,
    ):
        if OpenAI is None:
            raise RuntimeError(
                "The 'openai' package is required. Install it with: pip install openai"
            )
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is required. Set it in your .env file or environment."
            )

        self.model = model
        self._client: OpenAI = OpenAI(api_key=api_key)
        self._repository = repository
        self._rule_engine = rule_engine

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

        use_tools = self._tools_available
        logger.info(
            "[LLM] Calling %s (tools_available=%s) for: %s",
            self.model, use_tools, message[:120],
        )

        for round_num in range(_MAX_TOOL_ROUNDS):
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
                tool_names = [tc.function.name for tc in assistant_msg.tool_calls]
                logger.info(
                    "[LLM] Round %d — model requested %d tool call(s): %s",
                    round_num + 1, len(assistant_msg.tool_calls),
                    ", ".join(tool_names),
                )
                messages.append(assistant_msg)  # type: ignore[arg-type]
                for tool_call in assistant_msg.tool_calls:
                    fn_name = tool_call.function.name
                    fn_args = json.loads(tool_call.function.arguments)
                    logger.info(
                        "[LLM]   → %s(%s)",
                        fn_name,
                        json.dumps(fn_args, ensure_ascii=True, default=str),
                    )
                    result = self._execute_tool(fn_name, fn_args)
                    result_summary = json.dumps(result, ensure_ascii=True, default=str)
                    if len(result_summary) > 300:
                        result_summary = result_summary[:300] + "…"
                    logger.info("[LLM]   ← %s", result_summary)
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
            logger.info(
                "[LLM] Final response received (round %d, %d chars)",
                round_num + 1, len(content),
            )
            parsed = self._parse_llm_json(content)
            logger.info(
                "[LLM] Parsed intent=%s llm_used=%s",
                parsed.intent, parsed.llm_used,
            )
            return parsed

        logger.info("[LLM] Exhausted %d tool-call rounds — forcing final JSON", _MAX_TOOL_ROUNDS)
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
        parsed = self._parse_llm_json(content)
        logger.info("[LLM] Forced-final intent=%s", parsed.intent)
        return parsed

    # ------------------------------------------------------------------
    # Tool execution dispatch
    # ------------------------------------------------------------------

    def _execute_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        handlers: dict[str, Any] = {
            "search_exercises": self._tool_search_exercises,
            "get_safe_exercises": self._tool_get_safe_exercises,
            "get_injury_restrictions": self._tool_get_injury_restrictions,
            "get_exercise_details": self._tool_get_exercise_details,
            "get_muscle_recovery_info": self._tool_get_muscle_recovery_info,
            "get_krr_reasoning": self._tool_get_krr_reasoning,
        }
        handler = handlers.get(name)
        if handler is None:
            return {"error": f"Unknown tool: {name}"}
        try:
            return handler(arguments)
        except Exception as exc:
            logger.warning("Tool %s raised: %s", name, exc)
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

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
        raw_injuries = args.get("injuries", [])
        injuries = _normalize_injury_list(raw_injuries)
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

        reasoning = _build_krr_trace(
            injuries=injuries,
            exercises=safe[:limit],
            total_candidates=len(candidates),
            blocked_count=blocked_count,
        )

        return {
            "total_candidates": len(candidates),
            "blocked_by_injuries": blocked_count,
            "safe_count": len(safe),
            "exercises": [_compact(ex) for ex in safe[:limit]],
            "krr_reasoning": reasoning,
        }

    def _tool_get_injury_restrictions(self, args: dict[str, Any]) -> dict[str, Any]:
        raw = args.get("injury_name", "").strip().lower()
        canonical = _resolve_injury_alias(raw)
        if canonical is None:
            canonical = raw.replace(" ", "_")
            if not canonical.endswith("_injury"):
                canonical = f"{canonical}_injury"

        restricted = INJURY_TO_RESTRICTED_MUSCLES.get(canonical)
        if restricted is None:
            return {
                "injury": raw,
                "canonical": canonical,
                "known": False,
                "known_aliases": list(INJURY_ALIASES.keys()),
                "known_injuries": list(INJURY_TO_RESTRICTED_MUSCLES.keys()),
            }
        recovery_info = {
            m: DEFAULT_RECOVERY_DAYS.get(m)
            for m in restricted
            if DEFAULT_RECOVERY_DAYS.get(m) is not None
        }
        return {
            "injury": raw,
            "canonical": canonical,
            "known": True,
            "restricted_muscles": restricted,
            "muscle_recovery_days": recovery_info,
            "krr_rule": (
                f"forbidden_exercise(User, E) ← "
                f"trains_muscle(E, M) ∧ injured_muscle(User, M) "
                f"where M ∈ {{{', '.join(restricted)}}}"
            ),
        }

    def _tool_get_exercise_details(self, args: dict[str, Any]) -> dict[str, Any]:
        assert self._repository is not None
        name = args.get("exercise_name", "").strip()
        raw_injuries: list[str] = args.get("injuries", [])
        injuries = _normalize_injury_list(raw_injuries)

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

        target_muscle = match["target"].strip().lower()
        recovery = DEFAULT_RECOVERY_DAYS.get(target_muscle)
        if recovery is not None:
            result["target_recovery_days"] = recovery

        if injuries and self._rule_engine is not None:
            safe = self._rule_engine.filter_safe_exercises([match], injuries)
            result["safe_for_user"] = len(safe) > 0
            result["krr_explanation"] = _explain_exercise_krr(
                match, injuries, is_safe=len(safe) > 0,
            )

        return result

    def _tool_get_muscle_recovery_info(self, args: dict[str, Any]) -> dict[str, Any]:
        muscles = args.get("muscles")
        if muscles:
            filtered = {
                m.strip().lower(): DEFAULT_RECOVERY_DAYS.get(m.strip().lower())
                for m in muscles
            }
            found = {k: v for k, v in filtered.items() if v is not None}
            unknown = [k for k, v in filtered.items() if v is None]
            result: dict[str, Any] = {"recovery_days": found}
            if unknown:
                result["unknown_muscles"] = unknown
                result["available_muscles"] = list(DEFAULT_RECOVERY_DAYS.keys())
            return result
        return {"recovery_days": DEFAULT_RECOVERY_DAYS}

    def _tool_get_krr_reasoning(self, args: dict[str, Any]) -> dict[str, Any]:
        assert self._repository is not None and self._rule_engine is not None
        raw_injuries = args.get("injuries", [])
        injuries = _normalize_injury_list(raw_injuries)
        exercise_names: list[str] | None = args.get("exercise_names")
        body_parts: list[str] | None = args.get("body_parts")

        exercises: list[dict[str, Any]] = []
        if exercise_names:
            for en in exercise_names:
                found = self._repository.find_exercise_by_name(en)
                if found:
                    exercises.append(found[0])
        elif body_parts:
            exercises = self._repository.search_exercises(
                body_parts=body_parts, limit=20,
            )

        safe = self._rule_engine.filter_safe_exercises(exercises, injuries)
        safe_names = {ex["name"] for ex in safe}
        blocked_count = len(exercises) - len(safe)

        per_exercise: list[dict[str, Any]] = []
        for ex in exercises:
            is_safe = ex["name"] in safe_names
            per_exercise.append({
                "name": ex["name"],
                "target": ex.get("target", ""),
                "safe": is_safe,
                "explanation": _explain_exercise_krr(ex, injuries, is_safe),
            })

        trace = _build_krr_trace(
            injuries=injuries,
            exercises=exercises,
            total_candidates=len(exercises),
            blocked_count=blocked_count,
        )

        return {
            "krr_trace": trace,
            "exercises": per_exercise,
            "summary": {
                "total": len(exercises),
                "safe": len(safe),
                "blocked": blocked_count,
            },
        }

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
# Injury normalisation helpers (use INJURY_ALIASES everywhere)
# ---------------------------------------------------------------------------


def _normalize_injury_list(raw: list[str]) -> list[str]:
    """Resolve a list of raw injury strings to canonical names via INJURY_ALIASES."""
    result: list[str] = []
    for item in raw:
        s = item.strip().lower()
        if not s:
            continue
        resolved = _resolve_injury_alias(s)
        if resolved:
            result.append(resolved)
        elif s.endswith("_injury"):
            result.append(s)
    return list(dict.fromkeys(result))


def _sanitize_remove_injuries(raw: Any) -> list[str]:
    """Extract and normalize injury names to remove (canonical form)."""
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw:
        s = str(item).strip().lower()
        if not s:
            continue
        resolved = _resolve_injury_alias(s)
        if resolved:
            result.append(resolved)
        elif s.endswith("_injury"):
            result.append(s)
        else:
            result.append(f"{s.replace(' ', '_')}_injury")
    return list(dict.fromkeys(result))


# ---------------------------------------------------------------------------
# KRR reasoning helpers (mirrors krr/knowledge_base.py logic without
# touching global pyDatalog state so it doesn't conflict with rule_engine)
# ---------------------------------------------------------------------------


def _restricted_muscles_for(injuries: list[str]) -> set[str]:
    """Collect all muscles restricted by a list of canonical injuries."""
    restricted: set[str] = set()
    for inj in injuries:
        restricted.update(
            m.lower() for m in INJURY_TO_RESTRICTED_MUSCLES.get(inj, [])
        )
    return restricted


def _exercise_muscles(exercise: dict[str, Any]) -> set[str]:
    """Return the set of muscles an exercise trains (target + secondary)."""
    muscles: set[str] = set()
    target = exercise.get("target", "")
    if target:
        muscles.add(target.strip().lower())
    body_part = exercise.get("bodyPart", "")
    if body_part:
        muscles.add(body_part.strip().lower())
    for m in exercise.get("secondaryMuscles", []) or []:
        if m and str(m).strip():
            muscles.add(str(m).strip().lower())
    return muscles


def _explain_exercise_krr(
    exercise: dict[str, Any],
    injuries: list[str],
    is_safe: bool,
) -> str:
    """Build a KRR-style explanation for a single exercise (mirrors
    KnowledgeBase.explain_exercise logic)."""
    name = exercise.get("name", "?")
    target = exercise.get("target", "?").strip().lower()
    difficulty = exercise.get("difficulty", "?").strip().lower()
    ex_muscles = _exercise_muscles(exercise)
    restricted = _restricted_muscles_for(injuries)
    overlap = ex_muscles & restricted

    if not is_safe and overlap:
        offending = ", ".join(sorted(overlap))
        involved_injuries = [
            inj for inj in injuries
            if set(m.lower() for m in INJURY_TO_RESTRICTED_MUSCLES.get(inj, [])) & overlap
        ]
        return (
            f"forbidden({name}) ← "
            f"trains_muscle({name}, {{{offending}}}) "
            f"∧ injured_muscle(user, {{{', '.join(involved_injuries)}}}) "
            f"⇒ blocked because {offending} is restricted"
        )
    return (
        f"recommended({name}) ← "
        f"is_exercise({name}) ∧ difficulty({name}, {difficulty}) "
        f"∧ trains_muscle({name}, {target}) "
        f"∧ ¬forbidden(user, {name})"
    )


def _build_krr_trace(
    injuries: list[str],
    exercises: list[dict[str, Any]],
    total_candidates: int,
    blocked_count: int,
) -> str:
    """Build a multi-line KRR reasoning trace (mirrors
    KnowledgeBase.get_krr_reasoning_for_day)."""
    restricted = _restricted_muscles_for(injuries)
    lines: list[str] = []

    lines.append("Facts:")
    if injuries:
        for inj in injuries:
            muscles = INJURY_TO_RESTRICTED_MUSCLES.get(inj, [])
            lines.append(f"  injured_muscle(user, {inj})  → restricts {{{', '.join(muscles)}}}")
    else:
        lines.append("  (no injured muscles)")
    for muscle, days in DEFAULT_RECOVERY_DAYS.items():
        if muscle in restricted:
            lines.append(f"  muscle_recovery({muscle}, {days} days)")

    lines.append("")
    lines.append("Rules:")
    lines.append("  forbidden(U, E) ← trains_muscle(E, M) ∧ injured_muscle(U, M)")
    lines.append("  recommended(U, E) ← is_exercise(E) ∧ has_difficulty(E, D) ∧ ¬forbidden(U, E)")

    lines.append("")
    lines.append("Inference:")
    lines.append(f"  Candidates: {total_candidates}")
    lines.append(f"  Blocked:    {blocked_count}")
    lines.append(f"  Safe:       {total_candidates - blocked_count}")

    if exercises:
        lines.append("")
        shown = exercises[:5]
        for ex in shown:
            name = ex.get("name", "?")
            target = ex.get("target", "?")
            diff = ex.get("difficulty", "?")
            lines.append(
                f"  ✓ recommended(user, \"{name}\") ← "
                f"is_exercise ∧ difficulty={diff} ∧ target={target} "
                f"∧ ¬forbidden"
            )
        if len(exercises) > 5:
            lines.append(f"  … +{len(exercises) - 5} more")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sanitisation helpers
# ---------------------------------------------------------------------------


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
You are a personal trainer for a workout-planning app backed by an exercise database, a PyDatalog rule engine, and a Knowledge Representation & Reasoning (KRR) knowledge base.

YOU HAVE TOOLS — use them to ground your response in real data:

• search_exercises — query the exercise DB by body part, target muscle, equipment, or difficulty.
• get_safe_exercises — search AND filter out exercises blocked by the user's injuries (pyDatalog rules). Returns a KRR reasoning trace.
• get_injury_restrictions — look up which muscles an injury restricts, including recovery days. Accepts aliases like "shoulder", "knee pain", "back pain".
• get_exercise_details — full details for a single exercise (instructions, secondary muscles, safety check, KRR explanation).
• get_muscle_recovery_info — muscle recovery days from the KRR knowledge base (e.g. glutes need 2 days, pectorals need 1).
• get_krr_reasoning — generate a full KRR reasoning trace (facts → rules → inference) for a set of exercises and injuries. Use this to explain WHY exercises are recommended or forbidden.

WHEN TO CALL TOOLS (important):
1. User asks for exercise recommendations → call get_safe_exercises (or search_exercises if no injuries).
2. User mentions an injury → call get_injury_restrictions to understand what's affected, then get_safe_exercises.
3. User asks about a specific exercise → call get_exercise_details.
4. User asks for a plan → call get_safe_exercises for each relevant body-part split.
5. User asks "why?" about a recommendation or restriction → call get_krr_reasoning for the full symbolic trace.
6. User asks about rest days or recovery → call get_muscle_recovery_info.
7. You may call MULTIPLE tools in one turn if needed.

Call tools BEFORE writing your final JSON response so your answer is grounded in real data.

KRR CONCEPTS (built into the tools):
The knowledge base encodes:
  Facts: is_exercise(E), trains_muscle(E, M), has_difficulty(E, D), injured_muscle(User, M), muscle_recovery(M, Days)
  Rules: forbidden(U, E) ← trains_muscle(E, M) ∧ injured_muscle(U, M)
         recommended(U, E) ← is_exercise(E) ∧ has_difficulty(E, D) ∧ ¬forbidden(U, E)
When you describe reasoning to the user, reference these facts and rules naturally.

FINAL RESPONSE FORMAT:
After gathering information, return ONLY valid JSON with this schema (no markdown fences):
{
  "intent": "general_chat | update_profile | recommend_exercises | generate_plan | show_profile",
  "assistant_response": "short, encouraging response in a personal-trainer voice — reference specific exercises and KRR reasoning from tools",
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
- User says an injury EXISTS → add to "updates.injuries" using canonical names: shoulder_injury, knee_injury, elbow_injury, wrist_injury, lower_back_injury, ankle_injury, neck_injury.
- User says an injury is GONE / BETTER → add to "remove_injuries" only. Do NOT put it in "updates.injuries".
- If both adding and removing in one message, put new in "updates.injuries" and healed in "remove_injuries".
- Any constraint in the same message as a question must be in "updates" so the engine uses it for this turn.

Personal trainer behavior:
- assistant_response should be brief, encouraging, and actionable.
- Because you have tool access, reference specific exercises and KRR reasoning you found.
- When the user asks "why?", include the symbolic reasoning (e.g. "Your shoulder injury restricts delts and pectorals, so exercises targeting those are blocked by the rule: forbidden(U,E) ← trains_muscle(E,M) ∧ injured_muscle(U,M)").

Intent and filters:
- "Recommend exercises" / "what X exercises?" → intent: "recommend_exercises", set filters from request.
- "Generate a plan" / "weekly plan" → intent: "generate_plan"; use requested_days if mentioned.
- "Update my profile" / "I train 4 days" / "no barbell" → intent: "update_profile".
- "Show my profile" → intent: "show_profile".
- Use empty [] or {} where no values apply.
""".strip()
