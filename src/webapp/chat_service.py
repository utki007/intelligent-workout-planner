"""Chat orchestration across LLM parser, rule engine, and SQLite repository."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .database import WorkoutRepository
from .llm import LLMIntentParser
from .rule_engine import WorkoutRuleEngine


@dataclass
class ChatResult:
    """API response payload for one chat turn."""

    session_id: str
    reply: str
    profile: dict[str, Any]
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    plan: list[dict[str, Any]] = field(default_factory=list)
    llm_used: bool = False
    pydatalog_output: str = ""


class WorkoutChatService:
    """Coordinates chat requests with state updates and rule-based filtering."""

    def __init__(
        self,
        repository: WorkoutRepository,
        rule_engine: WorkoutRuleEngine,
        intent_parser: LLMIntentParser,
    ):
        self.repository = repository
        self.rule_engine = rule_engine
        self.intent_parser = intent_parser
        self._history: dict[str, list[dict[str, str]]] = defaultdict(list)

    def _merge_constraints_with_profile(
        self, updates: dict[str, Any], profile: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge list-type constraints from LLM with existing profile so new rules (e.g. injuries) are added to the PyDatalog engine rather than replacing."""
        for list_key in ("injuries", "excluded_equipment", "focus_muscles"):
            new_values = updates.get(list_key)
            if not isinstance(new_values, list) or not new_values:
                continue
            existing = list(profile.get(list_key) or [])
            merged = list(
                dict.fromkeys(existing + [str(v).strip() for v in new_values if str(v).strip()])
            )
            updates[list_key] = merged
        return updates

    def handle_message(self, session_id: str, message: str) -> ChatResult:
        history = self._history[session_id]
        profile = self.repository.get_profile(session_id)

        parsed = self.intent_parser.parse(message=message, profile=profile, history=history)
        updates = self._merge_constraints_with_profile(parsed.updates.copy(), profile)

        if "injuries" in updates:
            updates["injuries"] = self.rule_engine.normalize_injuries(updates.get("injuries", []))

        if getattr(parsed, "remove_injuries", None):
            to_remove = set(
                self.rule_engine.normalize_injuries(parsed.remove_injuries),
            )
            current_injuries = updates.get(
                "injuries",
                list(profile.get("injuries", [])),
            )
            updates["injuries"] = [i for i in current_injuries if i not in to_remove]

        if updates:
            profile = self.repository.update_profile(session_id, updates)

        recommendations: list[dict[str, Any]] = []
        plan: list[dict[str, Any]] = []

        pydatalog_output = ""

        if parsed.intent == "show_profile":
            reply = parsed.assistant_response or self._format_profile(profile)
            pydatalog_output = self._format_pydatalog_profile_only(profile)
        elif parsed.intent == "recommend_exercises":
            recommendations, pydatalog_output = self._recommend_exercises_with_trace(
                profile, parsed.filters
            )
            summary = _format_exercise_cards(recommendations)
            if parsed.assistant_response:
                reply = f"{parsed.assistant_response}\n\n{summary}".strip()
            else:
                reply = summary
        elif parsed.intent == "generate_plan":
            days = parsed.requested_days or profile.get("days_per_week", 3)
            plan, pydatalog_output = self._generate_plan_with_trace(
                profile, parsed.filters, days
            )
            summary = _format_plan(plan)
            if parsed.assistant_response:
                reply = f"{parsed.assistant_response}\n\n{summary}".strip()
            else:
                reply = summary
        elif parsed.intent == "update_profile":
            reply = parsed.assistant_response or self._profile_update_confirmation(profile, updates)
            pydatalog_output = self._format_pydatalog_profile_only(profile)
        else:
            reply = parsed.assistant_response or (
                "Share your goal, training level, available days, and any injuries. "
                "I can update your profile, recommend safe exercises, or build a weekly plan."
            )
            pydatalog_output = self._format_pydatalog_profile_only(profile)

        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": reply})
        self._history[session_id] = history[-14:]

        return ChatResult(
            session_id=session_id,
            reply=reply,
            profile=profile,
            recommendations=recommendations,
            plan=plan,
            llm_used=parsed.llm_used,
            pydatalog_output=pydatalog_output,
        )

    def _format_pydatalog_profile_only(self, profile: dict[str, Any]) -> str:
        injuries = profile.get("injuries") or []
        lines = [
            "Active constraints (used by rules):",
            "  injured_muscle(user) ← " + (", ".join(injuries) if injuries else "none"),
            "",
            "Rules (conceptual):",
            "  blocked_exercise(U,E) ← user_injury(U,I) & injury_blocks(I,M) & (exercise_target(E,M) | exercise_secondary(E,M) | exercise_body_part(E,M))",
            "  safe_exercise(U,E) ← candidate(E) & ¬blocked_exercise(U,E)",
        ]
        return "\n".join(lines)

    def _recommend_exercises_with_trace(
        self, profile: dict[str, Any], filters: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], str]:
        limit = int(filters.get("limit", 8))
        candidate_limit = max(40, limit * 6)
        injuries = profile.get("injuries", [])

        candidates = self.repository.search_exercises(
            body_parts=filters.get("body_parts"),
            targets=filters.get("targets"),
            equipment=filters.get("equipment"),
            difficulty=filters.get("difficulty") or profile.get("experience_level"),
            limit=candidate_limit,
        )
        blocked_ids = self.rule_engine.blocked_exercise_ids(candidates, injuries)
        safe_candidates = self.rule_engine.filter_safe_exercises(candidates, injuries)
        safe_candidates = self._apply_equipment_exclusions(
            safe_candidates, profile.get("excluded_equipment", [])
        )
        prioritized = self._prioritize_focus_muscles(
            safe_candidates, profile.get("focus_muscles", [])
        )
        result = [_compact_exercise(exercise) for exercise in prioritized[:limit]]

        lines = [
            "Query: recommend_exercises",
            "Facts:",
            "  user_injury(active_user, I) ← I ∈ {" + ", ".join(injuries) + "}" if injuries else "  user_injury(active_user, I) ← (none)",
            "  candidates: " + str(len(candidates)) + " exercises",
            "Inference:",
            "  blocked_exercise(active_user, E): " + str(len(blocked_ids)) + " exercises",
            "  safe (after rules): " + str(len(safe_candidates)) + " exercises",
            "Returned: " + str(len(result)) + " exercises",
        ]
        if not injuries:
            lines = [
                "Query: recommend_exercises",
                "Facts: user_injury(active_user) = none",
                "  candidates: " + str(len(candidates)) + " exercises",
                "Inference: no blocking rules applied.",
                "Returned: " + str(len(result)) + " exercises",
            ]
        pydatalog_output = "\n".join(lines)
        return result, pydatalog_output

    def _recommend_exercises(self, profile: dict[str, Any], filters: dict[str, Any]) -> list[dict[str, Any]]:
        recs, _ = self._recommend_exercises_with_trace(profile, filters)
        return recs

    def _generate_plan_with_trace(
        self,
        profile: dict[str, Any],
        filters: dict[str, Any],
        requested_days: int,
    ) -> tuple[list[dict[str, Any]], str]:
        plan = self._generate_plan(profile, filters, requested_days)
        injuries = profile.get("injuries", [])
        lines = [
            "Query: generate_plan (" + str(len(plan)) + " days)",
            "Facts: user_injury(active_user, I) ← I ∈ {" + ", ".join(injuries) + "}" if injuries else "Facts: user_injury(active_user) = none",
            "Rules: blocked_exercise(U,E) as in rule_engine; safe_exercise = candidate ∧ ¬blocked.",
            "Per day: candidates filtered by rules → safe set → up to 5 exercises in plan.",
            "",
            "Plan summary:",
        ]
        for day in plan:
            ex_count = len(day.get("exercises", []))
            lines.append("  Day " + str(day["day"]) + " (" + day["name"] + "): " + str(ex_count) + " exercises")
        pydatalog_output = "\n".join(lines)
        return plan, pydatalog_output

    def _generate_plan(
        self,
        profile: dict[str, Any],
        filters: dict[str, Any],
        requested_days: int,
    ) -> list[dict[str, Any]]:
        days = max(1, min(7, int(requested_days)))
        split = _pick_training_split(days)
        used_ids: set[str] = set()
        plan: list[dict[str, Any]] = []

        for day_index, day_info in enumerate(split, start=1):
            day_candidates = self.repository.search_exercises(
                body_parts=day_info["body_parts"],
                targets=filters.get("targets"),
                equipment=filters.get("equipment"),
                difficulty=filters.get("difficulty") or profile.get("experience_level"),
                limit=160,
            )
            safe_candidates = self.rule_engine.filter_safe_exercises(day_candidates, profile.get("injuries", []))
            safe_candidates = self._apply_equipment_exclusions(
                safe_candidates,
                profile.get("excluded_equipment", []),
            )
            safe_candidates = self._prioritize_focus_muscles(
                safe_candidates,
                profile.get("focus_muscles", []),
            )

            picks = self._pick_unique_exercises(safe_candidates, used_ids, count=5)
            used_ids.update(str(exercise["id"]) for exercise in picks)

            plan.append(
                {
                    "day": day_index,
                    "name": day_info["name"],
                    "focus": ", ".join(day_info["body_parts"]),
                    "exercises": [_compact_exercise(exercise) for exercise in picks],
                }
            )

        return plan

    def _pick_unique_exercises(
        self,
        candidates: list[dict[str, Any]],
        used_ids: set[str],
        count: int,
    ) -> list[dict[str, Any]]:
        picked: list[dict[str, Any]] = []
        for exercise in candidates:
            exercise_id = str(exercise.get("id", ""))
            if not exercise_id or exercise_id in used_ids:
                continue
            picked.append(exercise)
            if len(picked) >= count:
                break

        if len(picked) < count:
            for exercise in candidates:
                if len(picked) >= count:
                    break
                if exercise in picked:
                    continue
                picked.append(exercise)

        return picked

    def _apply_equipment_exclusions(
        self,
        exercises: list[dict[str, Any]],
        excluded_equipment: list[str],
    ) -> list[dict[str, Any]]:
        if not excluded_equipment:
            return exercises
        excluded = {item.strip().lower() for item in excluded_equipment if str(item).strip()}
        if not excluded:
            return exercises
        return [
            exercise
            for exercise in exercises
            if str(exercise.get("equipment", "")).strip().lower() not in excluded
        ]

    def _prioritize_focus_muscles(
        self,
        exercises: list[dict[str, Any]],
        focus_muscles: list[str],
    ) -> list[dict[str, Any]]:
        if not focus_muscles:
            return exercises

        wanted = {muscle.strip().lower() for muscle in focus_muscles if str(muscle).strip()}
        if not wanted:
            return exercises

        prioritized: list[dict[str, Any]] = []
        others: list[dict[str, Any]] = []

        for exercise in exercises:
            target = str(exercise.get("target", "")).strip().lower()
            secondary = {str(item).strip().lower() for item in exercise.get("secondaryMuscles", [])}
            if target in wanted or wanted.intersection(secondary):
                prioritized.append(exercise)
            else:
                others.append(exercise)

        return prioritized + others

    def _profile_update_confirmation(self, profile: dict[str, Any], updates: dict[str, Any]) -> str:
        if not updates:
            return "Profile is unchanged. Share your preferences and I can update them."

        changed_fields = ", ".join(sorted(updates.keys()))
        return (
            f"Updated your profile fields: {changed_fields}.\n"
            f"Current setup: {profile.get('days_per_week')} days/week, "
            f"{profile.get('session_minutes')} min/session, "
            f"level {profile.get('experience_level')}"
        )

    def _format_profile(self, profile: dict[str, Any]) -> str:
        injuries = ", ".join(profile.get("injuries", [])) or "none"
        excluded_equipment = ", ".join(profile.get("excluded_equipment", [])) or "none"
        focus_muscles = ", ".join(profile.get("focus_muscles", [])) or "none"

        return (
            "Profile summary:\n"
            f"- Goal: {profile.get('goal')}\n"
            f"- Experience: {profile.get('experience_level')}\n"
            f"- Days/week: {profile.get('days_per_week')}\n"
            f"- Session length: {profile.get('session_minutes')} minutes\n"
            f"- Injuries: {injuries}\n"
            f"- Excluded equipment: {excluded_equipment}\n"
            f"- Focus muscles: {focus_muscles}"
        )


def _compact_exercise(exercise: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": exercise.get("id"),
        "name": exercise.get("name"),
        "bodyPart": exercise.get("bodyPart"),
        "target": exercise.get("target"),
        "equipment": exercise.get("equipment"),
        "difficulty": exercise.get("difficulty"),
    }


def _pick_training_split(days: int) -> list[dict[str, Any]]:
    if days <= 1:
        return [{"name": "Full Body", "body_parts": ["chest", "back", "upper legs", "waist", "shoulders"]}]

    if days == 2:
        return [
            {"name": "Upper Body", "body_parts": ["chest", "back", "shoulders", "upper arms"]},
            {"name": "Lower Body + Core", "body_parts": ["upper legs", "lower legs", "waist"]},
        ]

    if days == 3:
        return [
            {"name": "Push", "body_parts": ["chest", "shoulders", "upper arms"]},
            {"name": "Pull", "body_parts": ["back", "upper arms", "lower arms"]},
            {"name": "Legs + Core", "body_parts": ["upper legs", "lower legs", "waist"]},
        ]

    if days == 4:
        return [
            {"name": "Upper A", "body_parts": ["chest", "back", "upper arms"]},
            {"name": "Lower A", "body_parts": ["upper legs", "lower legs", "waist"]},
            {"name": "Upper B", "body_parts": ["shoulders", "chest", "back", "lower arms"]},
            {"name": "Lower B", "body_parts": ["upper legs", "lower legs", "waist"]},
        ]

    if days == 5:
        return [
            {"name": "Push", "body_parts": ["chest", "shoulders", "upper arms"]},
            {"name": "Pull", "body_parts": ["back", "upper arms", "lower arms"]},
            {"name": "Legs", "body_parts": ["upper legs", "lower legs"]},
            {"name": "Core + Conditioning", "body_parts": ["waist", "cardio"]},
            {"name": "Upper Mix", "body_parts": ["chest", "back", "shoulders"]},
        ]

    if days == 6:
        return [
            {"name": "Push A", "body_parts": ["chest", "shoulders", "upper arms"]},
            {"name": "Pull A", "body_parts": ["back", "upper arms", "lower arms"]},
            {"name": "Legs A", "body_parts": ["upper legs", "lower legs", "waist"]},
            {"name": "Push B", "body_parts": ["chest", "shoulders", "upper arms"]},
            {"name": "Pull B", "body_parts": ["back", "upper arms", "lower arms"]},
            {"name": "Legs B", "body_parts": ["upper legs", "lower legs", "waist"]},
        ]

    # days == 7
    return [
        {"name": "Push", "body_parts": ["chest", "shoulders", "upper arms"]},
        {"name": "Pull", "body_parts": ["back", "upper arms", "lower arms"]},
        {"name": "Legs", "body_parts": ["upper legs", "lower legs"]},
        {"name": "Core", "body_parts": ["waist"]},
        {"name": "Cardio", "body_parts": ["cardio"]},
        {"name": "Upper Mix", "body_parts": ["chest", "back", "shoulders"]},
        {"name": "Recovery Technique", "body_parts": ["waist", "neck"]},
    ]


def _format_exercise_cards(exercises: list[dict[str, Any]]) -> str:
    if not exercises:
        return "I could not find safe exercises for that request. Try fewer constraints or update your profile."

    lines = ["Recommended exercises:"]
    for exercise in exercises:
        lines.append(
            f"- {exercise['name']} ({exercise['bodyPart']} | target: {exercise['target']} | {exercise['difficulty']})"
        )
    return "\n".join(lines)


def _format_plan(plan: list[dict[str, Any]]) -> str:
    if not plan:
        return "I could not generate a plan with the current constraints."

    lines = ["Weekly plan:"]
    for day in plan:
        lines.append(f"Day {day['day']} - {day['name']} ({day['focus']})")
        if not day["exercises"]:
            lines.append("  No safe exercises found for this day.")
            continue
        for exercise in day["exercises"]:
            lines.append(
                f"  - {exercise['name']} [{exercise['target']}, {exercise['difficulty']}, {exercise['equipment']}]"
            )
    return "\n".join(lines)
