"""
Weekly plan generation driven by LLM + KB grounding.

The LLM determines day themes/muscle focus directly from KB evidence + profile
constraints. Output is validated against hard safety constraints.
"""

from __future__ import annotations

from collections import defaultdict
import logging
from typing import Any

import pandas as pd

from agent.agent_state import AgentGoals, AgentState
from agent.decision_policy import DecisionPolicy
from agent.krr import get_krr_reasoning, load_kb, normalize_injury_muscles, query_recommended
from agent.llm import LLM
from planner.constraints import (
    MAX_EXERCISES_PER_DAY,
    MAX_REPS,
    MAX_SETS,
    MIN_REPS,
    MIN_SETS,
    clamp_days_per_week,
)
from planner.llm_contract import parse_llm_plan_payload
from planner.validation import verify_day_specs


logger = logging.getLogger("workout.planner")


def _exercise_targets(row: pd.Series) -> list[str]:
    """Return list of target muscles for an exercise (from DataFrame row)."""
    target = row.get("target")
    if pd.isna(target):
        return []
    s = str(target).strip().lower()
    if not s:
        return []
    return [m.strip().lower() for m in s.split(",") if m.strip()]


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _derive_sets(primary_target: str, prescription: dict[str, dict[str, Any]]) -> int:
    sets = 3
    if primary_target and primary_target in prescription:
        sets = min(4, max(2, prescription[primary_target]["target_sets"] // 2))
    return max(2, min(4, sets))


_HIGH_RECOVERY_MUSCLES = {
    "quads",
    "hamstrings",
    "glutes",
    "calves",
    "adductors",
    "abductors",
    "lats",
    "upper back",
    "traps",
    "levator scapulae",
}


def _intensity_exercise_band(intensity: float) -> tuple[int, int]:
    """
    Baseline per-day exercise count range from user intensity:
      1 -> 2-3, 2 -> 4, 3 -> 4-5, 4 -> 5-6, 5 -> 6-7
    """
    if intensity <= 1.5:
        return (2, 3)
    if intensity <= 2.5:
        return (4, 4)
    if intensity <= 3.5:
        return (4, 5)
    if intensity <= 4.5:
        return (5, 6)
    return (6, 7)


def _recovery_adjustment(day_muscles: list[str]) -> int:
    """
    High-recovery muscle groups should carry slightly fewer exercises
    at a given intensity.
    """
    muscles = [str(m).strip().lower() for m in day_muscles if str(m).strip()]
    if not muscles:
        return 0
    high_count = sum(1 for m in muscles if m in _HIGH_RECOVERY_MUSCLES)
    # If most muscles for the day are high-recovery, reduce exercise count by 1.
    if high_count * 2 >= len(muscles):
        return -1
    return 0


def _desired_exercise_range(intensity: float, day_muscles: list[str]) -> tuple[int, int]:
    base_min, base_max = _intensity_exercise_band(intensity)
    adjust = _recovery_adjustment(day_muscles)
    min_count = max(2, base_min + adjust)
    max_count = max(min_count, base_max + adjust)
    max_count = min(MAX_EXERCISES_PER_DAY, max_count)
    min_count = min(min_count, max_count)
    return (min_count, max_count)


def _build_exercise_catalog(
    recommended_all: list[str],
    exercises_df: pd.DataFrame,
) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, str]]:
    """
    Build:
      - by_muscle: muscle -> ordered unique exercise names
      - targets_by_exercise: exercise name -> target muscles
      - canonical_by_lower: lowercase exercise name -> canonical name
    """
    canonical_by_lower: dict[str, str] = {}
    targets_by_exercise: dict[str, list[str]] = {}
    by_muscle: dict[str, list[str]] = defaultdict(list)

    # Build lookup from DB
    row_by_name_lower: dict[str, tuple[str, list[str]]] = {}
    for _, row in exercises_df.iterrows():
        name = str(row.get("name", "")).strip()
        if not name:
            continue
        targets = _exercise_targets(row)
        row_by_name_lower[name.lower()] = (name, targets)

    # Keep only KB-recommended exercises
    for name in recommended_all:
        rec = row_by_name_lower.get(str(name).strip().lower())
        if rec is None:
            continue
        canonical, targets = rec
        if not targets:
            continue
        canonical_by_lower[canonical.lower()] = canonical
        targets_by_exercise[canonical] = targets
        for m in targets:
            if canonical not in by_muscle[m]:
                by_muscle[m].append(canonical)

    return dict(by_muscle), targets_by_exercise, canonical_by_lower


def _kb_evidence_for_llm(
    by_muscle: dict[str, list[str]],
    injury_muscles: list[str],
) -> dict[str, Any]:
    """
    Compact KB evidence payload for LLM grounding.
    Keep payload bounded to avoid oversized prompts.
    """
    muscles_sorted = sorted(by_muscle.keys(), key=lambda m: (-len(by_muscle[m]), m))
    limited: dict[str, list[str]] = {}
    for muscle in muscles_sorted[:14]:
        limited[muscle] = by_muscle[muscle][:20]

    return {
        "rules": [
            "forbidden(User, Exercise) <- trains_muscle(Exercise, M) and injured_muscle(User, M)",
            "recommended(User, Exercise) <- is_exercise(Exercise) and difficulty=beginner and not forbidden(User, Exercise)",
        ],
        "injury_muscles": sorted({str(m).strip().lower() for m in injury_muscles if str(m).strip()}),
        "recommended_by_muscle": limited,
    }


def _sanitize_llm_plan(
    llm_payload: dict[str, Any] | None,
    days_per_week: int,
    intensity: float,
    forbidden_muscles: set[str],
    by_muscle: dict[str, list[str]],
    targets_by_exercise: dict[str, list[str]],
    canonical_by_lower: dict[str, str],
    prescription: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Validate/normalize LLM plan output.
    Returns day specs:
      [{"day","theme","muscles","exercises":[{name,target,sets,reps}, ...]}, ...]
    """
    if not isinstance(llm_payload, dict):
        return []

    days_raw = llm_payload.get("days")
    if not isinstance(days_raw, list) or not days_raw:
        return []

    all_recommended = set(targets_by_exercise.keys())
    day_specs: list[dict[str, Any]] = []

    for idx in range(days_per_week):
        raw_day = days_raw[idx] if idx < len(days_raw) and isinstance(days_raw[idx], dict) else {}
        day_label = f"Day {idx + 1}"
        theme = str(raw_day.get("theme") or day_label).strip()
        llm_muscles = [
            str(m).strip().lower()
            for m in (raw_day.get("muscles") or [])
            if str(m).strip()
        ]
        muscles: list[str] = []
        for m in llm_muscles:
            if m in forbidden_muscles:
                continue
            if m in by_muscle and m not in muscles:
                muscles.append(m)

        ex_raw = raw_day.get("exercises")
        ex_raw = ex_raw if isinstance(ex_raw, list) else []
        exercises: list[dict[str, Any]] = []
        seen: set[str] = set()

        for ex in ex_raw:
            if not isinstance(ex, dict):
                continue
            name_raw = str(ex.get("name", "")).strip()
            if not name_raw:
                continue
            canonical = canonical_by_lower.get(name_raw.lower(), name_raw)
            if canonical not in all_recommended or canonical in seen:
                continue

            targets = targets_by_exercise.get(canonical, [])
            if not targets:
                continue

            if forbidden_muscles and any(t in forbidden_muscles for t in targets):
                continue

            if muscles and not any(t in muscles for t in targets):
                continue

            target = str(ex.get("target", "")).strip().lower()
            if target not in targets:
                target = next((t for t in targets if not muscles or t in muscles), targets[0])

            default_sets = _derive_sets(target, prescription)
            sets = _safe_int(ex.get("sets"), default_sets)
            sets = max(MIN_SETS, min(MAX_SETS, sets))
            reps = _safe_int(ex.get("reps"), 12)
            reps = max(MIN_REPS, min(MAX_REPS, reps))

            exercises.append({
                "name": canonical,
                "sets": sets,
                "reps": reps,
                "target": target,
            })
            seen.add(canonical)
            if len(exercises) >= MAX_EXERCISES_PER_DAY:
                break

        if not muscles and exercises:
            inferred_from_ex: list[str] = []
            for ex in exercises:
                m = str(ex.get("target", "")).strip().lower()
                if m and m not in inferred_from_ex:
                    inferred_from_ex.append(m)
            muscles = inferred_from_ex[:2]

        desired_min, desired_max = _desired_exercise_range(intensity, muscles)

        # If LLM under-selected exercises, fill from evidence to keep a usable day.
        if len(exercises) < desired_min:
            candidate_pool: list[str] = []
            source_muscles = muscles or sorted(by_muscle.keys(), key=lambda m: (-len(by_muscle[m]), m))
            for m in source_muscles:
                for name in by_muscle.get(m, []):
                    if name not in candidate_pool:
                        candidate_pool.append(name)
            for name in candidate_pool:
                if name in seen:
                    continue
                targets = targets_by_exercise.get(name, [])
                if not targets:
                    continue
                if forbidden_muscles and any(t in forbidden_muscles for t in targets):
                    continue
                target = next((t for t in targets if not muscles or t in muscles), targets[0])
                exercises.append({
                    "name": name,
                    "sets": _derive_sets(target, prescription),
                    "reps": 12,
                    "target": target,
                })
                seen.add(name)
                if len(exercises) >= desired_max:
                    break

        if not muscles:
            inferred: list[str] = []
            for ex in exercises:
                m = str(ex.get("target", "")).strip().lower()
                if m and m not in inferred:
                    inferred.append(m)
            muscles = inferred[:2]

        if not exercises:
            theme = "Rest"
            muscles = []
        else:
            # Enforce intensity/recovery-aware upper bound deterministically.
            _, desired_max = _desired_exercise_range(intensity, muscles)
            exercises = exercises[:desired_max]

        day_specs.append({
            "day": day_label,
            "theme": theme,
            "muscles": muscles,
            "exercises": exercises[:MAX_EXERCISES_PER_DAY],
        })

    return day_specs


def _fallback_plan(
    days_per_week: int,
    intensity: float,
    forbidden_muscles: set[str],
    by_muscle: dict[str, list[str]],
    targets_by_exercise: dict[str, list[str]],
    prescription: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Algorithmic fallback if LLM is unavailable or output is invalid."""
    muscles = sorted(by_muscle.keys(), key=lambda m: (-len(by_muscle[m]), m))
    if not muscles:
        return [{"day": f"Day {i + 1}", "theme": "Rest", "muscles": [], "exercises": []} for i in range(days_per_week)]

    out: list[dict[str, Any]] = []
    for i in range(days_per_week):
        day = f"Day {i + 1}"
        primary = muscles[i % len(muscles)]
        secondary = muscles[(i + 1) % len(muscles)] if len(muscles) > 1 else primary
        day_muscles = [primary] if secondary == primary else [primary, secondary]
        theme = f"{' + '.join(day_muscles)} focus"
        desired_min, desired_max = _desired_exercise_range(intensity, day_muscles)

        pool: list[str] = []
        for m in day_muscles:
            for n in by_muscle.get(m, []):
                if n not in pool:
                    pool.append(n)

        seen_names: set[str] = set()
        exercises: list[dict[str, Any]] = []
        for name in pool:
            if name in seen_names:
                continue
            targets = targets_by_exercise.get(name, [])
            if not targets:
                continue
            if forbidden_muscles and any(t in forbidden_muscles for t in targets):
                continue
            target = targets[0]
            exercises.append({
                "name": name,
                "sets": _derive_sets(target, prescription),
                "reps": 12,
                "target": target,
            })
            seen_names.add(name)
            if len(exercises) >= desired_max:
                break

        if not exercises:
            theme = "Rest"
            day_muscles = []
        elif len(exercises) < desired_min:
            logger.info(
                "planner.fallback limited_catalog day=%s desired_min=%s got=%s",
                day,
                desired_min,
                len(exercises),
            )

        out.append({
            "day": day,
            "theme": theme,
            "muscles": day_muscles,
            "exercises": exercises[:desired_max],
        })

    return out


def generate_weekly_plan(
    user_id: str,
    user_profile: dict[str, Any],
    exercises_df: pd.DataFrame,
    state: AgentState,
    goals: AgentGoals,
    modifications: list[str] | None = None,
) -> tuple[dict[str, list[dict]], dict[str, str], dict[str, str], dict[str, list[str]]]:
    """
    Generate a weekly plan with KB-grounded LLM synthesis.

    Returns:
        (plan, krr_reasoning, day_themes, day_theme_muscles)
    """
    del modifications  # not currently used for LLM generation
    injury_muscles = user_profile.get("injury_muscles") or []
    forbidden_muscles = normalize_injury_muscles(injury_muscles)
    days_per_week = clamp_days_per_week(user_profile.get("days_per_week"))
    try:
        intensity = float(user_profile.get("intensity", 3))
    except Exception:
        intensity = 3.0
    intensity = max(1.0, min(5.0, intensity))

    load_kb(exercises_df, user_id, injury_muscles)
    recommended_all = query_recommended(user_id)
    policy = DecisionPolicy(state, goals)
    prescription = policy.get_prescription()

    by_muscle, targets_by_exercise, canonical_by_lower = _build_exercise_catalog(recommended_all, exercises_df)
    logger.info(
        "planner.catalog user_id=%s recommended_exercises=%s muscles=%s",
        user_id,
        len(targets_by_exercise),
        len(by_muscle),
    )

    kb_evidence = _kb_evidence_for_llm(by_muscle, injury_muscles)
    llm_profile = {
        "experience_level": user_profile.get("experience_level", "beginner"),
        "days_per_week": days_per_week,
        "intensity": intensity,
        "injury_muscles": injury_muscles,
    }
    llm_constraints = {
        "days_per_week": days_per_week,
        "max_exercises_per_day": MAX_EXERCISES_PER_DAY,
        "sets_range": [MIN_SETS, MAX_SETS],
        "reps_range": [MIN_REPS, MAX_REPS],
        "preferred_reps": 12,
        "target_sets_by_muscle": {
            m: data.get("target_sets", 6) for m, data in prescription.items()
        },
        "exercise_count_guide_by_intensity": {
            "1": [2, 3],
            "2": [4, 4],
            "3": [4, 5],
            "4": [5, 6],
            "5": [6, 7],
        },
        "high_recovery_muscles": sorted(_HIGH_RECOVERY_MUSCLES),
    }

    llm = LLM()
    llm_payload = llm.build_weekly_plan(
        user_id=user_id,
        profile=llm_profile,
        kb_evidence=kb_evidence,
        constraints=llm_constraints,
    )

    validated_payload, schema_errors = parse_llm_plan_payload(llm_payload)
    if validated_payload is None:
        logger.warning("planner.schema valid=false user_id=%s errors=%s", user_id, schema_errors[:8])

    day_specs = _sanitize_llm_plan(
        llm_payload=validated_payload,
        days_per_week=days_per_week,
        intensity=intensity,
        forbidden_muscles=forbidden_muscles,
        by_muscle=by_muscle,
        targets_by_exercise=targets_by_exercise,
        canonical_by_lower=canonical_by_lower,
        prescription=prescription,
    )

    verification = verify_day_specs(
        day_specs,
        expected_days=days_per_week,
        allowed_exercises=set(targets_by_exercise.keys()),
        min_sets=MIN_SETS,
        max_sets=MAX_SETS,
        min_reps=MIN_REPS,
        max_reps=MAX_REPS,
        max_exercises_per_day=MAX_EXERCISES_PER_DAY,
        max_sets_per_muscle_per_week=goals.max_sets_per_muscle_per_week,
    )

    if day_specs and not verification.is_valid:
        logger.warning(
            "planner.verify failed=true user_id=%s issues=%s",
            user_id,
            verification.issues[:8],
        )
        llm_payload_retry = llm.build_weekly_plan(
            user_id=user_id,
            profile=llm_profile,
            kb_evidence=kb_evidence,
            constraints=llm_constraints,
            repair_feedback=verification.issues[:10],
        )
        validated_retry, retry_schema_errors = parse_llm_plan_payload(llm_payload_retry)
        if validated_retry is None:
            logger.warning("planner.schema retry_valid=false user_id=%s errors=%s", user_id, retry_schema_errors[:8])
        day_specs_retry = _sanitize_llm_plan(
            llm_payload=validated_retry,
            days_per_week=days_per_week,
            intensity=intensity,
            forbidden_muscles=forbidden_muscles,
            by_muscle=by_muscle,
            targets_by_exercise=targets_by_exercise,
            canonical_by_lower=canonical_by_lower,
            prescription=prescription,
        )
        verification_retry = verify_day_specs(
            day_specs_retry,
            expected_days=days_per_week,
            allowed_exercises=set(targets_by_exercise.keys()),
            min_sets=MIN_SETS,
            max_sets=MAX_SETS,
            min_reps=MIN_REPS,
            max_reps=MAX_REPS,
            max_exercises_per_day=MAX_EXERCISES_PER_DAY,
            max_sets_per_muscle_per_week=goals.max_sets_per_muscle_per_week,
        )
        if verification_retry.is_valid:
            day_specs = day_specs_retry
            verification = verification_retry
            logger.info("planner.verify repair_success=true user_id=%s", user_id)

    if not day_specs or not verification.is_valid:
        logger.warning("planner.llm unusable_output=true fallback=algorithmic user_id=%s", user_id)
        day_specs = _fallback_plan(
            days_per_week=days_per_week,
            intensity=intensity,
            forbidden_muscles=forbidden_muscles,
            by_muscle=by_muscle,
            targets_by_exercise=targets_by_exercise,
            prescription=prescription,
        )
    else:
        logger.info(
            "planner.llm usable_output=true user_id=%s days=%s verification_issues=%s",
            user_id,
            len(day_specs),
            verification.stats.get("issue_count", 0),
        )

    plan: dict[str, list[dict]] = {}
    krr_reasoning: dict[str, str] = {}
    day_themes: dict[str, str] = {}
    day_theme_muscles: dict[str, list[str]] = {}

    for day_spec in day_specs:
        day = day_spec["day"]
        theme = day_spec.get("theme") or day
        muscles = day_spec.get("muscles") or []
        exercises = day_spec.get("exercises") or []

        plan[day] = exercises
        day_themes[day] = str(theme)
        day_theme_muscles[day] = [str(m).strip().lower() for m in muscles if str(m).strip()]

        recommended_names = [ex["name"] for ex in exercises]
        krr_reasoning[day] = get_krr_reasoning(
            user_id=user_id,
            day_label=day,
            day_theme_muscles=day_theme_muscles[day],
            recommended_for_day=recommended_names,
        )

    return plan, krr_reasoning, day_themes, day_theme_muscles
