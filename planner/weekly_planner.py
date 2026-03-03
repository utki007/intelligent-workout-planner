"""
Weekly plan generation driven by LLM + KB grounding.

This module no longer uses static split presets. It encodes KB evidence
(recommended exercises filtered by injuries) and asks the LLM to synthesize
an N-day plan that is then validated against hard constraints.
"""

from __future__ import annotations

from collections import defaultdict
import logging
from typing import Any

import pandas as pd

from agent.agent_state import AgentGoals, AgentState
from agent.decision_policy import DecisionPolicy
from agent.krr import get_krr_reasoning, load_kb, query_recommended
from agent.llm import LLM


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
        day_label = f"Day{idx + 1}"
        theme = str(raw_day.get("theme", "")).strip() or f"Day {idx + 1}"

        muscles_raw = raw_day.get("muscles")
        muscles: list[str] = []
        if isinstance(muscles_raw, list):
            for m in muscles_raw:
                ms = str(m).strip().lower()
                if ms and ms in by_muscle and ms not in muscles:
                    muscles.append(ms)

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
            target = str(ex.get("target", "")).strip().lower()
            if target not in targets:
                target = targets[0]

            default_sets = _derive_sets(target, prescription)
            sets = _safe_int(ex.get("sets"), default_sets)
            sets = max(2, min(4, sets))
            reps = _safe_int(ex.get("reps"), 12)
            reps = max(6, min(20, reps))

            exercises.append({
                "name": canonical,
                "sets": sets,
                "reps": reps,
                "target": target,
            })
            seen.add(canonical)
            if len(exercises) >= 6:
                break

        # If LLM under-selected exercises, fill from evidence to keep a usable day.
        if len(exercises) < 3:
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
                target = targets[0]
                exercises.append({
                    "name": name,
                    "sets": _derive_sets(target, prescription),
                    "reps": 12,
                    "target": target,
                })
                seen.add(name)
                if len(exercises) >= 6:
                    break

        if not muscles:
            inferred: list[str] = []
            for ex in exercises:
                m = str(ex.get("target", "")).strip().lower()
                if m and m not in inferred:
                    inferred.append(m)
            muscles = inferred[:2]

        day_specs.append({
            "day": day_label,
            "theme": theme,
            "muscles": muscles,
            "exercises": exercises[:6],
        })

    return day_specs


def _fallback_plan(
    days_per_week: int,
    by_muscle: dict[str, list[str]],
    targets_by_exercise: dict[str, list[str]],
    prescription: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Algorithmic fallback if LLM is unavailable or output is invalid."""
    muscles = sorted(by_muscle.keys(), key=lambda m: (-len(by_muscle[m]), m))
    if not muscles:
        return [{"day": f"Day{i + 1}", "theme": "Rest", "muscles": [], "exercises": []} for i in range(days_per_week)]

    out: list[dict[str, Any]] = []
    for i in range(days_per_week):
        day = f"Day{i + 1}"
        primary = muscles[i % len(muscles)]
        secondary = muscles[(i + 1) % len(muscles)] if len(muscles) > 1 else primary
        day_muscles = [primary] if secondary == primary else [primary, secondary]
        theme = f"{primary.title()} Focus"

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
            target = targets[0]
            exercises.append({
                "name": name,
                "sets": _derive_sets(target, prescription),
                "reps": 12,
                "target": target,
            })

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
    days_per_week = max(3, min(7, int(user_profile.get("days_per_week") or 5)))

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
        "fitness_goals": user_profile.get("fitness_goals", ""),
        "days_per_week": days_per_week,
        "intensity": user_profile.get("intensity", 3),
        "injury_muscles": injury_muscles,
    }
    llm_constraints = {
        "days_per_week": days_per_week,
        "max_exercises_per_day": 6,
        "sets_range": [2, 4],
        "reps_range": [6, 20],
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
    day_specs = _sanitize_llm_plan(
        llm_payload=llm_payload,
        days_per_week=days_per_week,
        by_muscle=by_muscle,
        targets_by_exercise=targets_by_exercise,
        canonical_by_lower=canonical_by_lower,
        prescription=prescription,
    )

    if not day_specs:
        logger.warning("planner.llm unusable_output=true fallback=algorithmic user_id=%s", user_id)
        day_specs = _fallback_plan(
            days_per_week=days_per_week,
            by_muscle=by_muscle,
            targets_by_exercise=targets_by_exercise,
            prescription=prescription,
        )
    else:
        logger.info("planner.llm usable_output=true user_id=%s days=%s", user_id, len(day_specs))

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

