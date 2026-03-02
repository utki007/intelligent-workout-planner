"""
Azure OpenAI LLM wrapper for natural-language explanations of the workout plan.

Reads config from the environment (or from a .env file in the project root).
Set these to connect the LLM:
  - AZURE_OPENAI_KEY or AZURE_OPENAI_API_KEY
  - AZURE_OPENAI_ENDPOINT
  - AZURE_OPENAI_MODEL (your deployment name)
"""

from __future__ import annotations

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


def _get_client():
    global _client
    if _client is not None:
        return _client
    try:
        from openai import AzureOpenAI
    except ImportError:
        return None
    key = os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY")
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    if not key or not endpoint:
        return None
    _client = AzureOpenAI(
        api_key=key,
        azure_endpoint=endpoint.rstrip("/"),
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-15-preview"),
    )
    return _client


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
                return (choice.message.content or "").strip()
        except Exception:
            pass
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
            "offer to generate one. If they mention recovery or changes, suggest updating their profile."
        )
        user_content = "\n".join(context) + "\n\nUser message: " + (user_message or "").strip()

        try:
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
                return (choice.message.content or "").strip()
        except Exception:
            pass
        return "Sorry, I couldn't process that. Please try again."
