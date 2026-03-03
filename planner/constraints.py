"""
Shared planning constraints used across API, planner, and tests.
"""

from __future__ import annotations

from typing import Any


MIN_DAYS_PER_WEEK = 2
MAX_DAYS_PER_WEEK = 7
DEFAULT_DAYS_PER_WEEK = 5

MIN_SETS = 2
MAX_SETS = 4
MIN_REPS = 6
MAX_REPS = 20
MAX_EXERCISES_PER_DAY = 6


def clamp_days_per_week(value: Any, default: int = DEFAULT_DAYS_PER_WEEK) -> int:
    """Clamp arbitrary input to supported days/week range."""
    try:
        days = int(value)
    except Exception:
        days = default
    return max(MIN_DAYS_PER_WEEK, min(MAX_DAYS_PER_WEEK, days))


def normalize_requested_days(value: Any) -> int | None:
    """Return valid requested days/week or None if invalid/unset."""
    if value is None:
        return None
    try:
        days = int(value)
    except Exception:
        return None
    if days < MIN_DAYS_PER_WEEK or days > MAX_DAYS_PER_WEEK:
        return None
    return days
