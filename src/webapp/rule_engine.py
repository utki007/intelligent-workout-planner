"""pyDatalog rule engine for constraint filtering."""

from __future__ import annotations

import re
from typing import Any

from pyDatalog import pyDatalog


INJURY_TO_RESTRICTED_MUSCLES: dict[str, list[str]] = {
    "shoulder_injury": ["shoulders", "delts", "triceps", "pectorals", "upper_arms"],
    "knee_injury": ["upper_legs", "lower_legs", "quads", "hamstrings", "glutes", "calves"],
    "elbow_injury": ["biceps", "triceps", "forearms", "lower_arms", "upper_arms"],
    "wrist_injury": ["forearms", "lower_arms", "upper_arms"],
    "lower_back_injury": ["lower_back", "waist", "abs", "hamstrings", "glutes"],
    "ankle_injury": ["lower_legs", "calves", "ankles", "upper_legs"],
    "neck_injury": ["neck", "upper_back", "shoulders"],
}

INJURY_ALIASES: dict[str, str] = {
    "shoulder": "shoulder_injury",
    "shoulder pain": "shoulder_injury",
    "shoulder injury": "shoulder_injury",
    "knee": "knee_injury",
    "knee pain": "knee_injury",
    "knee injury": "knee_injury",
    "elbow": "elbow_injury",
    "elbow pain": "elbow_injury",
    "elbow injury": "elbow_injury",
    "wrist": "wrist_injury",
    "wrist pain": "wrist_injury",
    "wrist injury": "wrist_injury",
    "lower back": "lower_back_injury",
    "back pain": "lower_back_injury",
    "lower back pain": "lower_back_injury",
    "ankle": "ankle_injury",
    "ankle pain": "ankle_injury",
    "neck": "neck_injury",
    "neck pain": "neck_injury",
}


class WorkoutRuleEngine:
    """Constraint engine that maps injuries to blocked exercises."""

    def __init__(self, injury_rules: dict[str, list[str]] | None = None):
        self.injury_rules = {
            _normalize_term(injury): [_normalize_term(muscle) for muscle in muscles]
            for injury, muscles in (injury_rules or INJURY_TO_RESTRICTED_MUSCLES).items()
        }

    def normalize_injuries(self, injuries: list[str]) -> list[str]:
        normalized: list[str] = []
        for raw_injury in injuries:
            key = str(raw_injury).strip().lower()
            if not key:
                continue

            canonical = INJURY_ALIASES.get(key)
            if canonical:
                normalized.append(canonical)
                continue

            token = _normalize_term(key)
            if token in self.injury_rules:
                normalized.append(token)
                continue

            if token.endswith("_injury"):
                normalized.append(token)

        # Preserve order while removing duplicates.
        unique = list(dict.fromkeys(normalized))
        return unique

    def blocked_exercise_ids(self, exercises: list[dict[str, Any]], injuries: list[str]) -> set[str]:
        canonical_injuries = self.normalize_injuries(injuries)
        if not canonical_injuries:
            return set()

        pyDatalog.clear()
        pyDatalog.load(
            """
            blocked_exercise(U, E) <= user_injury(U, I) & injury_blocks(I, M) & exercise_target(E, M)
            blocked_exercise(U, E) <= user_injury(U, I) & injury_blocks(I, M) & exercise_secondary(E, M)
            blocked_exercise(U, E) <= user_injury(U, I) & injury_blocks(I, M) & exercise_body_part(E, M)
            """
        )

        for injury, restricted_muscles in self.injury_rules.items():
            for muscle in restricted_muscles:
                pyDatalog.assert_fact("injury_blocks", injury, muscle)

        for injury in canonical_injuries:
            pyDatalog.assert_fact("user_injury", "active_user", injury)

        for exercise in exercises:
            exercise_id = str(exercise.get("id", "")).strip()
            if not exercise_id:
                continue

            pyDatalog.assert_fact(
                "exercise_target",
                exercise_id,
                _normalize_term(str(exercise.get("target", ""))),
            )
            pyDatalog.assert_fact(
                "exercise_body_part",
                exercise_id,
                _normalize_term(str(exercise.get("bodyPart", ""))),
            )
            for secondary in exercise.get("secondaryMuscles", []) or []:
                pyDatalog.assert_fact("exercise_secondary", exercise_id, _normalize_term(str(secondary)))

        query = pyDatalog.ask("blocked_exercise('active_user', E)")
        if query is None:
            return set()

        return {str(answer[0]) for answer in query.answers}

    def filter_safe_exercises(self, exercises: list[dict[str, Any]], injuries: list[str]) -> list[dict[str, Any]]:
        blocked = self.blocked_exercise_ids(exercises, injuries)
        if not blocked:
            return exercises
        return [exercise for exercise in exercises if str(exercise.get("id", "")) not in blocked]


def _normalize_term(value: str) -> str:
    lowered = value.strip().lower()
    lowered = lowered.replace("&", " and ").replace("/", " ")
    normalized = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    return normalized
