"""
Deterministic verification for generated day specs/plans.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PlanVerificationResult:
    is_valid: bool
    issues: list[str]
    stats: dict[str, Any]


def verify_day_specs(
    day_specs: list[dict[str, Any]],
    *,
    expected_days: int,
    allowed_exercises: set[str],
    min_sets: int,
    max_sets: int,
    min_reps: int,
    max_reps: int,
    max_exercises_per_day: int,
    max_sets_per_muscle_per_week: int | None = None,
) -> PlanVerificationResult:
    issues: list[str] = []
    total_exercises = 0
    unknown_exercises = 0
    range_violations = 0
    volume_per_muscle: dict[str, int] = {}
    day_labels_seen: set[str] = set()

    if len(day_specs) != expected_days:
        issues.append(f"expected_days={expected_days} got_days={len(day_specs)}")

    for idx, day in enumerate(day_specs, start=1):
        label = str(day.get("day") or f"Day{idx}")
        if label in day_labels_seen:
            issues.append(f"duplicate_day_label={label}")
        day_labels_seen.add(label)

        theme = str(day.get("theme") or "").strip()
        if not theme:
            issues.append(f"{label}: missing_theme")

        exercises = day.get("exercises") or []
        if not isinstance(exercises, list):
            issues.append(f"{label}: exercises_not_list")
            continue

        if len(exercises) > max_exercises_per_day:
            issues.append(f"{label}: too_many_exercises={len(exercises)} max={max_exercises_per_day}")

        seen: set[str] = set()
        for ex in exercises:
            total_exercises += 1
            if not isinstance(ex, dict):
                issues.append(f"{label}: exercise_not_object")
                continue

            name = str(ex.get("name") or "").strip()
            if not name:
                issues.append(f"{label}: missing_exercise_name")
                continue

            if name in seen:
                issues.append(f"{label}: duplicate_exercise={name}")
            seen.add(name)

            if name not in allowed_exercises:
                unknown_exercises += 1
                issues.append(f"{label}: unknown_or_forbidden_exercise={name}")

            try:
                sets = int(ex.get("sets"))
                reps = int(ex.get("reps"))
            except Exception:
                range_violations += 1
                issues.append(f"{label}: invalid_set_rep_type={name}")
                continue

            if sets < min_sets or sets > max_sets:
                range_violations += 1
                issues.append(f"{label}: sets_out_of_range={name}:{sets}")
            if reps < min_reps or reps > max_reps:
                range_violations += 1
                issues.append(f"{label}: reps_out_of_range={name}:{reps}")

            target = str(ex.get("target") or "").strip().lower()
            if not target:
                issues.append(f"{label}: missing_target={name}")
            else:
                volume_per_muscle[target] = volume_per_muscle.get(target, 0) + sets

    if max_sets_per_muscle_per_week is not None and max_sets_per_muscle_per_week > 0:
        for muscle, total in volume_per_muscle.items():
            if total > max_sets_per_muscle_per_week:
                issues.append(
                    f"weekly_volume_exceeded={muscle}:{total}>{max_sets_per_muscle_per_week}"
                )

    return PlanVerificationResult(
        is_valid=(len(issues) == 0),
        issues=issues,
        stats={
            "days": len(day_specs),
            "total_exercises": total_exercises,
            "unknown_exercises": unknown_exercises,
            "range_violations": range_violations,
            "muscles_tracked": len(volume_per_muscle),
            "issue_count": len(issues),
        },
    )
