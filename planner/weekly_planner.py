"""
Weekly plan generation driven by LLM + KB grounding.

This module uses split templates from planner/splits.json as the source of truth
for day themes/muscle focus, then asks the LLM to select exercises within those
constraints. Output is validated against hard safety constraints.
"""

from __future__ import annotations

from collections import defaultdict
import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from agent.agent_state import AgentGoals, AgentState
from agent.decision_policy import DecisionPolicy
from agent.krr import get_krr_reasoning, load_kb, normalize_injury_muscles, query_recommended
from agent.llm import LLM
from agent.reliability_metrics import increment_metric
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

_SPLITS_PATH = Path(__file__).with_name("splits.json")


def _load_split_templates() -> dict[int, list[dict[str, Any]]]:
    if not _SPLITS_PATH.exists():
        logger.warning("planner.splits missing path=%s", _SPLITS_PATH)
        return {}
    try:
        raw = json.loads(_SPLITS_PATH.read_text())
    except Exception:
        logger.exception("planner.splits failed_to_parse path=%s", _SPLITS_PATH)
        return {}

    out: dict[int, list[dict[str, Any]]] = {}
    for key, value in (raw.items() if isinstance(raw, dict) else []):
        try:
            days = int(key)
        except Exception:
            continue
        if not isinstance(value, list):
            continue
        normalized_days: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            theme = str(item.get("theme") or "").strip()
            muscles = [
                str(m).strip().lower()
                for m in (item.get("muscles") or [])
                if str(m).strip()
            ]
            normalized_days.append({"theme": theme or f"Day {len(normalized_days) + 1}", "muscles": muscles})
        if normalized_days:
            out[days] = normalized_days
    return out


SPLIT_TEMPLATES = _load_split_templates()


def _split_for_days(days_per_week: int) -> list[dict[str, Any]]:
    template = SPLIT_TEMPLATES.get(days_per_week)
    if template and len(template) == days_per_week:
        return template
    return [{"theme": f"Day {i + 1}", "muscles": []} for i in range(days_per_week)]


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
    split_template: list[dict[str, Any]],
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
        template_day = split_template[idx] if idx < len(split_template) else {"theme": day_label, "muscles": []}
        theme = str(template_day.get("theme") or day_label).strip()
        template_muscles = [
            str(m).strip().lower()
            for m in (template_day.get("muscles") or [])
            if str(m).strip()
        ]
        safe_template_muscles = [
            m for m in template_muscles
            if m not in forbidden_muscles and by_muscle.get(m)
        ]

        # If this themed day has no injury-safe muscle left, enforce rest day
        # instead of substituting exercises from unrelated muscles.
        if template_muscles and not safe_template_muscles:
            day_specs.append({
                "day": day_label,
                "theme": "Rest",
                "muscles": [],
                "exercises": [],
            })
            continue

        muscles = list(safe_template_muscles)

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

            if safe_template_muscles and not any(t in safe_template_muscles for t in targets):
                continue

            target = str(ex.get("target", "")).strip().lower()
            if target not in targets:
                target = next((t for t in targets if not safe_template_muscles or t in safe_template_muscles), targets[0])

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

        # If LLM under-selected exercises, fill from evidence to keep a usable day.
        if len(exercises) < 3:
            candidate_pool: list[str] = []
            source_muscles = safe_template_muscles or sorted(by_muscle.keys(), key=lambda m: (-len(by_muscle[m]), m))
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
                target = next((t for t in targets if not safe_template_muscles or t in safe_template_muscles), targets[0])
                exercises.append({
                    "name": name,
                    "sets": _derive_sets(target, prescription),
                    "reps": 12,
                    "target": target,
                })
                seen.add(name)
                if len(exercises) >= MAX_EXERCISES_PER_DAY:
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

        day_specs.append({
            "day": day_label,
            "theme": theme,
            "muscles": muscles,
            "exercises": exercises[:MAX_EXERCISES_PER_DAY],
        })

    return day_specs


def _fallback_plan(
    days_per_week: int,
    split_template: list[dict[str, Any]],
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
        template_day = split_template[i] if i < len(split_template) else {"theme": day, "muscles": []}
        day_muscles = [
            str(m).strip().lower()
            for m in (template_day.get("muscles") or [])
            if str(m).strip()
        ]
        theme = str(template_day.get("theme") or day).strip()
        day_muscles = [m for m in day_muscles if m not in forbidden_muscles and by_muscle.get(m)]

        # If this themed day has no injury-safe muscle left, enforce rest day.
        if (template_day.get("muscles") or []) and not day_muscles:
            out.append({
                "day": day,
                "theme": "Rest",
                "muscles": [],
                "exercises": [],
            })
            continue

        if not day_muscles:
            primary = muscles[i % len(muscles)]
            secondary = muscles[(i + 1) % len(muscles)] if len(muscles) > 1 else primary
            day_muscles = [primary] if secondary == primary else [primary, secondary]

        pool: list[str] = []
        for m in day_muscles:
            for n in by_muscle.get(m, []):
                if n not in pool:
                    pool.append(n)

        chosen = pool[:6]
        exercises: list[dict[str, Any]] = []
        for name in chosen:
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

        if not exercises:
            theme = "Rest"
            day_muscles = []

        out.append({
            "day": day,
            "theme": theme,
            "muscles": day_muscles,
            "exercises": exercises,
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

    load_kb(exercises_df, user_id, injury_muscles)
    recommended_all = query_recommended(user_id)
    policy = DecisionPolicy(state, goals)
    prescription = policy.get_prescription()

    by_muscle, targets_by_exercise, canonical_by_lower = _build_exercise_catalog(recommended_all, exercises_df)
    split_template = _split_for_days(days_per_week)
    logger.info(
        "planner.catalog user_id=%s recommended_exercises=%s muscles=%s",
        user_id,
        len(targets_by_exercise),
        len(by_muscle),
    )

    kb_evidence = _kb_evidence_for_llm(by_muscle, injury_muscles)
    llm_profile = {
        "experience_level": user_profile.get("experience_level", "beginner"),
        "fitness_goals": user_profile.get("fitness_goals", ""),
        "days_per_week": days_per_week,
        "intensity": user_profile.get("intensity", 3),
        "injury_muscles": injury_muscles,
    }
    llm_constraints = {
        "days_per_week": days_per_week,
        "day_templates": split_template,
        "max_exercises_per_day": MAX_EXERCISES_PER_DAY,
        "sets_range": [MIN_SETS, MAX_SETS],
        "reps_range": [MIN_REPS, MAX_REPS],
        "preferred_reps": 12,
        "target_sets_by_muscle": {
            m: data.get("target_sets", 6) for m, data in prescription.items()
        },
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
        increment_metric("schema_fail_rate")
        logger.warning("planner.schema valid=false user_id=%s errors=%s", user_id, schema_errors[:8])
    else:
        increment_metric("schema_pass_rate")

    day_specs = _sanitize_llm_plan(
        llm_payload=validated_payload,
        days_per_week=days_per_week,
        split_template=split_template,
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
        increment_metric("verifier_fail_rate")
        logger.warning(
            "planner.verify failed=true user_id=%s issues=%s",
            user_id,
            verification.issues[:8],
        )
        increment_metric("repair_attempt_count")
        llm_payload_retry = llm.build_weekly_plan(
            user_id=user_id,
            profile=llm_profile,
            kb_evidence=kb_evidence,
            constraints=llm_constraints,
            repair_feedback=verification.issues[:10],
        )
        validated_retry, retry_schema_errors = parse_llm_plan_payload(llm_payload_retry)
        if validated_retry is None:
            increment_metric("schema_fail_rate")
            logger.warning("planner.schema retry_valid=false user_id=%s errors=%s", user_id, retry_schema_errors[:8])
        else:
            increment_metric("schema_pass_rate")
        day_specs_retry = _sanitize_llm_plan(
            llm_payload=validated_retry,
            days_per_week=days_per_week,
            split_template=split_template,
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
            increment_metric("repair_success_rate")
            logger.info("planner.verify repair_success=true user_id=%s", user_id)

    if not day_specs or not verification.is_valid:
        increment_metric("fallback_rate")
        logger.warning("planner.llm unusable_output=true fallback=algorithmic user_id=%s", user_id)
        day_specs = _fallback_plan(
            days_per_week=days_per_week,
            split_template=split_template,
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
    increment_metric("plans_generated")

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

