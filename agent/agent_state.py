"""
Agent state and goals for the agentic workout planner.

AgentState: tracks soreness per muscle, weekly volume per muscle, missed sessions.
AgentGoals: target soreness range, max sets per muscle per week.
Persistence: JSON files in data/ so main.py and Streamlit can share state.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def _data_dir() -> Path:
    """Directory for persisting agent state (data/ under repo root)."""
    base = Path(__file__).resolve().parent.parent
    d = base / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class AgentGoals:
    """Targets for the agent: soreness range and volume cap."""

    target_soreness_min: float = 1.0
    target_soreness_max: float = 3.0
    max_sets_per_muscle_per_week: int = 20

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentGoals":
        return cls(
            target_soreness_min=d.get("target_soreness_min", 1.0),
            target_soreness_max=d.get("target_soreness_max", 3.0),
            max_sets_per_muscle_per_week=d.get("max_sets_per_muscle_per_week", 20),
        )


@dataclass
class AgentState:
    """
    Tracks per-user state: soreness per muscle, weekly volume per muscle, missed sessions.
    """

    user_id: str
    soreness_per_muscle: dict[str, float] = field(default_factory=dict)
    weekly_volume_per_muscle: dict[str, int] = field(default_factory=dict)
    missed_sessions: list[str] = field(default_factory=list)

    def update_from_feedback(
        self,
        soreness_by_muscle: dict[str, float] | None = None,
        overall_soreness: float | None = None,
    ) -> None:
        """
        Update state from end-of-week feedback.
        If soreness_by_muscle is provided, it updates per-muscle soreness.
        If only overall_soreness is provided, apply it to all muscles that had volume this week.
        """
        if soreness_by_muscle:
            for muscle, level in soreness_by_muscle.items():
                self.soreness_per_muscle[muscle] = level
        elif overall_soreness is not None:
            # Apply overall soreness to any muscle we track (e.g. had volume)
            muscles = set(self.soreness_per_muscle) | set(self.weekly_volume_per_muscle)
            for m in muscles:
                self.soreness_per_muscle[m] = overall_soreness

    def record_week_volume(self, plan: dict[str, list[dict]]) -> None:
        """
        Update weekly_volume_per_muscle from a generated plan.
        plan: {"Day1": [{"name": "...", "sets": 3, "target": "chest"}, ...], ...}
        Each exercise may have "target" (single muscle) or "targets" (list); we count sets per muscle.
        """
        volume: dict[str, int] = {}
        for day_exercises in plan.values():
            for ex in day_exercises:
                sets = ex.get("sets", 3)
                target = ex.get("target")
                targets = ex.get("targets") or ([target] if target else [])
                if not targets and target:
                    targets = [target]
                for m in targets:
                    if m:
                        volume[m] = volume.get(m, 0) + sets
        self.weekly_volume_per_muscle = volume

    def record_missed_session(self, day_label: str) -> None:
        """Record a missed session (e.g. 'Day2')."""
        if day_label not in self.missed_sessions:
            self.missed_sessions.append(day_label)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentState":
        return cls(
            user_id=d.get("user_id", ""),
            soreness_per_muscle=d.get("soreness_per_muscle", {}),
            weekly_volume_per_muscle=d.get("weekly_volume_per_muscle", {}),
            missed_sessions=d.get("missed_sessions", []),
        )

    def save(self) -> None:
        """Persist state to data/agent_state_{user_id}.json."""
        path = _data_dir() / f"agent_state_{self.user_id}.json"
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, user_id: str) -> "AgentState | None":
        """Load state from data/ if it exists."""
        path = _data_dir() / f"agent_state_{user_id}.json"
        if not path.exists():
            return None
        with open(path) as f:
            return cls.from_dict(json.load(f))

    @classmethod
    def get_or_create(cls, user_id: str) -> "AgentState":
        """Load existing state or create a new one."""
        state = cls.load(user_id)
        if state is not None:
            return state
        return cls(user_id=user_id)
