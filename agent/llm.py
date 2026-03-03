"""
Azure OpenAI LLM wrapper for natural-language explanations of the workout plan.

Reads config from the environment (or from a .env file in the project root).
Set these to connect the LLM:
  - AZURE_OPENAI_KEY or AZURE_OPENAI_API_KEY
  - AZURE_OPENAI_ENDPOINT
  - AZURE_OPENAI_MODEL (your deployment name)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

# Load .env from project root if present (so you can add endpoints in one place)
def _load_dotenv():
    try:
        from dotenv import load_dotenv
        root = Path(__file__).resolve().parent.parent
        load_dotenv(root / ".env")
    except ImportError:
        pass

_load_dotenv()

# Lazy import so the app runs without openai installed or configured
_client: Any = None
logger = logging.getLogger("workout.llm")


def _clip(value: str | None, limit: int = 160) -> str:
    s = (value or "").strip().replace("\n", " ")
    if len(s) <= limit:
        return s
    return s[:limit] + "..."


def _get_client():
    global _client
    if _client is not None:
        return _client
    try:
        from openai import AzureOpenAI
    except ImportError:
        logger.warning("llm.client unavailable reason=openai_sdk_missing")
        return None
    key = os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY")
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    if not key or not endpoint:
        logger.warning(
            "llm.client unavailable reason=missing_config has_key=%s has_endpoint=%s",
            bool(key),
            bool(endpoint),
        )
        return None
    requested_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
    # This project uses the Chat Completions API shape (`messages`, `response_format`).
    # Some newer API versions route through Responses and will reject those params.
    # Prefer a known-good version unless explicitly compatible.
    api_version = requested_version
    if str(requested_version).startswith("2025-"):
        logger.warning("llm.client overriding_unsupported_api_version requested=%s using=%s", requested_version, "2024-02-15-preview")
        api_version = "2024-02-15-preview"
    _client = AzureOpenAI(
        api_key=key,
        azure_endpoint=endpoint.rstrip("/"),
        api_version=api_version,
    )
    return _client


def _response_text(response: Any) -> str:
    """
    Extract plain text from a Responses API response.

    The OpenAI SDK typically exposes `response.output_text`. If it doesn't,
    fall back to scanning output items.
    """
    if response is None:
        return ""
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str):
        return output_text.strip()
    output = getattr(response, "output", None)
    if isinstance(output, list):
        chunks: list[str] = []
        for item in output:
            content = getattr(item, "content", None) if item is not None else None
            if isinstance(content, list):
                for c in content:
                    text = getattr(c, "text", None)
                    if isinstance(text, str) and text.strip():
                        chunks.append(text.strip())
        return "\n".join(chunks).strip()
    return ""


class LLM:
    """
    Wrapper for Azure OpenAI Chat Completions.
    explain_plan() returns a natural-language explanation for one day's plan.
    """

    def __init__(self) -> None:
        self._client = _get_client()
        self._model = os.environ.get("AZURE_OPENAI_MODEL", "").strip() or None

    def is_available(self) -> bool:
        """True if Azure OpenAI is configured (key, endpoint, model)."""
        return (
            self._client is not None
            and bool(self._model)
        )

    def explain_plan(
        self,
        user_id: str,
        day: str,
        exercises: list[dict],
        skipped_reason: str | None = None,
        krr_reasoning: str | None = None,
    ) -> str:
        """
        Generate a short natural-language explanation for why this day's
        exercises were recommended or skipped.

        Args:
            user_id: User identifier.
            day: Day label (e.g. "Day1").
            exercises: List of {"name", "sets", "reps", "target"} (may be empty for rest day).
            skipped_reason: Optional text (e.g. "All exercises for this day target injured muscles.").
            krr_reasoning: Optional KRR reasoning text (facts, rules, inference) for context.

        Returns:
            Explanation string, or a fallback message if Azure is unavailable or the call fails.
        """
        if not self._client or not self._model:
            logger.warning("llm.explain_plan unavailable model_configured=%s", bool(self._model))
            return "Explanation unavailable (Azure OpenAI not configured). Set AZURE_OPENAI_KEY, AZURE_OPENAI_ENDPOINT, and AZURE_OPENAI_MODEL."

        exercise_list = ", ".join(e.get("name", "?") for e in exercises) if exercises else "Rest day."
        user_content = (
            f"User: {user_id}. Day: {day}. "
            f"Exercises for this day: {exercise_list}. "
        )
        if skipped_reason:
            user_content += f"Note: {skipped_reason} "
        if krr_reasoning:
            user_content += f"KRR reasoning (for context): {krr_reasoning[:800]} "
        user_content += (
            "In 2–3 sentences, explain why these exercises were chosen for this day "
            "or why it's a rest day. Be concise and friendly."
        )

        try:
            logger.info(
                "llm.explain_plan request user_id=%s day=%s exercises=%s has_krr=%s skipped=%s",
                user_id,
                day,
                len(exercises),
                bool(krr_reasoning),
                bool(skipped_reason),
            )
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a fitness coach. Explain workout plan choices briefly.",
                    },
                    {"role": "user", "content": user_content},
                ],
                temperature=0.3,
                max_tokens=200,
            )
            choice = response.choices[0] if response.choices else None
            if choice and getattr(choice, "message", None):
                text = (choice.message.content or "").strip()
                logger.info("llm.explain_plan success user_id=%s day=%s chars=%s", user_id, day, len(text))
                return text
        except Exception:
            logger.exception("llm.explain_plan error user_id=%s day=%s", user_id, day)
        return "Explanation unavailable (API error)."

    def chat(
        self,
        user_message: str,
        plan_summary: str | None = None,
        krr_summary: str | None = None,
        user_id: str | None = None,
        profile_context: str | None = None,
    ) -> str:
        """
        Respond as the workout agent to a free-form user message (e.g. chat).
        Optionally include current plan, KRR context, and profile (goals, injuries) for informed answers.
        """
        if not self._client or not self._model:
            logger.warning("llm.chat unavailable model_configured=%s", bool(self._model))
            return "I can't chat right now — Azure OpenAI isn't configured. Set AZURE_OPENAI_KEY, AZURE_OPENAI_ENDPOINT, and AZURE_OPENAI_MODEL in your .env."

        context = []
        if user_id:
            context.append(f"User: {user_id}.")
        if profile_context:
            context.append(profile_context)
        if plan_summary:
            context.append(f"Current plan summary: {plan_summary[:600]}.")
        if krr_summary:
            context.append(f"Reasoning context: {krr_summary[:400]}.")

        system = (
            "You are a friendly workout planner agent. You help users understand their weekly plan, "
            "why certain exercises were chosen or skipped (e.g. injuries), and how to adjust. "
            "Answer briefly and helpfully. If the user has already completed their profile (goals, days, injuries), "
            "do NOT ask them to provide it again — they've already set it up. If they don't have a plan yet, "
            "offer to generate one. If they mention recovery or changes, suggest updating their profile. "
            "If context is missing or ambiguous, ask one concise clarifying question instead of guessing. "
            "When recommending exercises, stay grounded in the provided exercise DB context and do not invent exercise names."
        )
        user_content = "\n".join(context) + "\n\nUser message: " + (user_message or "").strip()

        try:
            logger.info(
                "llm.chat request user_id=%s message=\"%s\" has_plan_summary=%s has_krr_summary=%s has_profile_context=%s",
                user_id,
                _clip(user_message, 120),
                bool(plan_summary),
                bool(krr_summary),
                bool(profile_context),
            )
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.4,
                max_tokens=300,
            )
            choice = response.choices[0] if response.choices else None
            if choice and getattr(choice, "message", None):
                text = (choice.message.content or "").strip()
                logger.info("llm.chat success user_id=%s chars=%s", user_id, len(text))
                return text
        except Exception:
            logger.exception("llm.chat error user_id=%s", user_id)
        return "Sorry, I couldn't process that. Please try again."

    def resolve_intent(self, user_message: str) -> dict[str, Any]:
        """
        Classify the user's message intent and return structured routing hints:
        {
          "intent": "generate_plan" | "profile_update" | "chat",
                    "days_per_week": int(2-7) | null,
          "assistant_reply": str | null,
          "profile_updates": {
            "days_per_week": int(2-7) | null,
            "intensity": float(1-5) | null,
            "fitness_goals": str | null,
            "injury_update": {
              "status": "injured" | "recovered" | "cleared" | "unknown",
              "operation": "add" | "remove" | "clear" | "none",
              "muscles": [str]
            } | null
          } | null
        }
        """
        out = {
            "intent": "chat",
            "days_per_week": None,
            "assistant_reply": None,
            "profile_updates": None,
        }
        if not self._client or not self._model:
            logger.warning("llm.intent unavailable model_configured=%s", bool(self._model))
            out["assistant_reply"] = (
                "Intent routing unavailable because Azure OpenAI is not configured. "
                "Set AZURE_OPENAI_KEY, AZURE_OPENAI_ENDPOINT, and AZURE_OPENAI_MODEL."
            )
            return out

        system = (
            "You classify intent for a workout-planner app.\n"
            "Return strict JSON only with keys: intent, days_per_week, assistant_reply, profile_updates.\n"
            "intent must be one of: generate_plan, profile_update, chat.\n"
            "days_per_week must be an integer 2-7 when explicitly requested, otherwise null.\n"
            "assistant_reply should be a short sentence for the UI, or null.\n"
            "profile_updates must be null or an object with optional keys:\n"
            "- days_per_week (2-7)\n"
            "- intensity (1-5)\n"
            "- fitness_goals (string)\n"
            "- injury_update (object with status, operation, muscles)\n"
            "injury_update.status must be one of: injured, recovered, cleared, unknown.\n"
            "injury_update.operation must be one of: add, remove, clear, none.\n"
            "injury_update.muscles must be lowercase muscle names.\n"
            "Rules:\n"
            "- If the user asks for a workout split/plan with a specific day count (e.g. '2 day split'), "
            "intent=generate_plan and days_per_week=<that number>.\n"
            "- If the user asks to change goals, injuries, intensity, or profile settings without asking to generate now, "
            "intent=profile_update.\n"
            "- Injury semantics:\n"
            "  - new pain/injury -> status=injured, operation=add.\n"
            "  - recovery/better/healed/no longer hurt for specific muscles -> status=recovered, operation=remove.\n"
            "  - no injuries / fully recovered overall -> status=cleared, operation=clear.\n"
            "- Otherwise intent=chat.\n"
            "Examples:\n"
            "- 'I want a two day workout split for the weekend' -> {\"intent\":\"generate_plan\",\"days_per_week\":2,\"assistant_reply\":\"I'll build a 2-day plan.\",\"profile_updates\":null}\n"
            "- 'Update my injuries, shoulder is better' -> {\"intent\":\"profile_update\",\"days_per_week\":null,\"assistant_reply\":\"I detected an injury recovery update.\",\"profile_updates\":{\"injury_update\":{\"status\":\"recovered\",\"operation\":\"remove\",\"muscles\":[\"delts\"]}}}\n"
            "- 'I hurt my knee and shoulder' -> {\"intent\":\"profile_update\",\"days_per_week\":null,\"assistant_reply\":\"I detected new injury updates.\",\"profile_updates\":{\"injury_update\":{\"status\":\"injured\",\"operation\":\"add\",\"muscles\":[\"quads\",\"delts\"]}}}\n"
            "- 'No injuries now' -> {\"intent\":\"profile_update\",\"days_per_week\":null,\"assistant_reply\":\"I detected that injuries are cleared.\",\"profile_updates\":{\"injury_update\":{\"status\":\"cleared\",\"operation\":\"clear\",\"muscles\":[]}}}\n"
            "- 'Why this exercise?' -> {\"intent\":\"chat\",\"days_per_week\":null,\"assistant_reply\":null,\"profile_updates\":null}"
        )
        user_content = f"User message: {(user_message or '').strip()}"

        try:
            logger.info("llm.intent request message=\"%s\"", _clip(user_message, 120))
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.0,
                max_tokens=120,
                response_format={"type": "json_object"},
            )
            choice = response.choices[0] if response.choices else None
            if choice and getattr(choice, "message", None):
                raw = (choice.message.content or "").strip()
                logger.info("llm.intent raw_response=%s", _clip(raw, 200))
                parsed = json.loads(raw)
                intent = str(parsed.get("intent", "chat")).strip().lower()
                if intent in {"generate_plan", "profile_update", "chat"}:
                    out["intent"] = intent
                days = parsed.get("days_per_week")
                if isinstance(days, (int, float)):
                    d = int(days)
                    if 2 <= d <= 7:
                        out["days_per_week"] = d
                reply = parsed.get("assistant_reply")
                if isinstance(reply, str) and reply.strip():
                    out["assistant_reply"] = reply.strip()
                updates_raw = parsed.get("profile_updates")
                if isinstance(updates_raw, dict):
                    updates_out: dict[str, Any] = {}
                    upd_days = updates_raw.get("days_per_week")
                    if isinstance(upd_days, (int, float)):
                        upd_days = int(upd_days)
                        if 2 <= upd_days <= 7:
                            updates_out["days_per_week"] = upd_days
                    upd_intensity = updates_raw.get("intensity")
                    if isinstance(upd_intensity, (int, float)):
                        intensity = float(upd_intensity)
                        if 1.0 <= intensity <= 5.0:
                            updates_out["intensity"] = intensity
                    upd_goals = updates_raw.get("fitness_goals")
                    if isinstance(upd_goals, str) and upd_goals.strip():
                        updates_out["fitness_goals"] = upd_goals.strip()

                    injury_raw = updates_raw.get("injury_update")
                    if isinstance(injury_raw, dict):
                        injury_out: dict[str, Any] = {}
                        status = str(injury_raw.get("status", "")).strip().lower()
                        if status in {"injured", "recovered", "cleared", "unknown"}:
                            injury_out["status"] = status
                        operation = str(injury_raw.get("operation", "")).strip().lower()
                        if operation in {"add", "remove", "clear", "none"}:
                            injury_out["operation"] = operation
                        muscles = injury_raw.get("muscles")
                        if isinstance(muscles, list):
                            cleaned = []
                            seen: set[str] = set()
                            for muscle in muscles:
                                m = str(muscle).strip().lower()
                                if m and m not in seen:
                                    seen.add(m)
                                    cleaned.append(m)
                            injury_out["muscles"] = cleaned
                        if injury_out:
                            updates_out["injury_update"] = injury_out

                    if updates_out:
                        out["profile_updates"] = updates_out
        except Exception:
            logger.exception("llm.intent error")
        logger.info(
            "llm.intent result intent=%s days_per_week=%s has_reply=%s has_profile_updates=%s",
            out.get("intent"),
            out.get("days_per_week"),
            bool(out.get("assistant_reply")),
            bool(out.get("profile_updates")),
        )
        return out

    def build_weekly_plan(
        self,
        user_id: str,
        profile: dict[str, Any],
        kb_evidence: dict[str, Any],
        constraints: dict[str, Any],
        repair_feedback: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """
        Build a KB-grounded weekly plan as strict JSON.
        Returns parsed JSON or None on failure/unavailability.
        """
        if not self._client or not self._model:
            logger.warning("llm.plan unavailable model_configured=%s", bool(self._model))
            return None

        payload = {
            "user_id": user_id,
            "profile": profile,
            "kb_evidence": kb_evidence,
            "constraints": constraints,
        }
        if repair_feedback:
            payload["repair_feedback"] = repair_feedback
        system = (
            "You are a workout planning model that must produce a weekly plan grounded in provided KB evidence.\n"
            "Return strict JSON only with this schema:\n"
            "{\n"
            '  "days": [\n'
            "    {\n"
            '      "day": "Day1",\n'
            '      "theme": "short day theme",\n'
            '      "muscles": ["muscle1", "muscle2"],\n'
            '      "exercises": [\n'
            '        {"name": "exercise name from kb", "target": "muscle", "sets": 2-4, "reps": 6-20}\n'
            "      ]\n"
            "    }\n"
            "  ],\n"
            '  "summary": "1-2 sentence rationale"\n'
            "}\n"
            "Rules:\n"
            "- Use ONLY exercises present in kb_evidence.recommended_by_muscle.\n"
            "- Never include exercises targeting injured muscles.\n"
            "- Number of day objects must exactly equal constraints.days_per_week.\n"
            "- Use 3-6 exercises per training day when possible.\n"
            "- Respect constraints.max_exercises_per_day and rep/set ranges.\n"
            "- Keep plan coherent with profile goals and days_per_week.\n"
            "- If repair_feedback exists, fix those exact issues in this output.\n"
            "- Do not output markdown. Output JSON only."
        )

        try:
            logger.info(
                "llm.plan request user_id=%s days_per_week=%s goals=%s muscles=%s repair_feedback=%s",
                user_id,
                constraints.get("days_per_week"),
                _clip(str(profile.get("fitness_goals", "")), 80),
                len((kb_evidence.get("recommended_by_muscle") or {})),
                bool(repair_feedback),
            )
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload)},
                ],
                temperature=0.2,
                max_tokens=1200,
                response_format={"type": "json_object"},
            )
            choice = response.choices[0] if response.choices else None
            if choice and getattr(choice, "message", None):
                raw = (choice.message.content or "").strip()
                logger.info("llm.plan raw_response=%s", _clip(raw, 240))
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    logger.info("llm.plan success user_id=%s days=%s", user_id, len(parsed.get("days") or []))
                    return parsed
        except Exception:
            logger.exception("llm.plan error user_id=%s", user_id)
        return None
