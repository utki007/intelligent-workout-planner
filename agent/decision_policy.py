"""
Decision policy and plan evaluation for the agentic workout planner.

DecisionPolicy: adjusts sets/volume per muscle from AgentState + AgentGoals;
prioritizes/deprioritizes muscles.
PlanEvaluation: flags overtraining (muscle exceeds max sets), suggests modifications.
"""

from __future__ import annotations

from typing import Any

from .agent_state import AgentGoals, AgentState


class DecisionPolicy:
    """
    Produces a prescription per muscle (target sets or scale factor) from
    AgentState and AgentGoals. High soreness or near-max volume -> reduce sets;
    low soreness and under cap -> allow more sets.
    """

    def __init__(self, state: AgentState, goals: AgentGoals):
        self.state = state
        self.goals = goals

    def get_prescription(self) -> dict[str, dict[str, Any]]:
        """
        Returns a dict: muscle -> { "target_sets": int, "scale": float, "priority": str }.
        priority is "high" | "normal" | "low".
        """
        prescription: dict[str, dict[str, Any]] = {}
        soreness = self.state.soreness_per_muscle
        volume = self.state.weekly_volume_per_muscle
        lo, hi = self.goals.target_soreness_min, self.goals.target_soreness_max
        max_sets = self.goals.max_sets_per_muscle_per_week

        # Consider all muscles that appear in state (soreness or volume)
        muscles = set(soreness.keys()) | set(volume.keys())

        for muscle in muscles:
            s = soreness.get(muscle, 2.0)
            v = volume.get(muscle, 0)

            # Scale: reduce if soreness above target or volume near max
            scale = 1.0
            if s > hi:
                scale = 0.6  # reduce sets
            elif s < lo and v < max_sets * 0.8:
                scale = 1.2  # allow slightly more
            if v >= max_sets:
                scale = min(scale, 0.5)

            # Base target sets (e.g. 3–4 per muscle per day; we do 2 exercises/day so ~6–8/week per muscle for one focus day)
            base_sets = 6
            target_sets = max(2, min(max_sets, int(base_sets * scale)))

            if s > hi or v >= max_sets:
                priority = "low"
            elif s < lo and v < max_sets * 0.5:
                priority = "high"
            else:
                priority = "normal"

            prescription[muscle] = {
                "target_sets": target_sets,
                "scale": scale,
                "priority": priority,
            }

        return prescription

    def get_target_sets_for_muscle(self, muscle: str) -> int:
        """Convenience: target sets for one muscle (default if not in prescription)."""
        pre = self.get_prescription()
        if muscle in pre:
            return pre[muscle]["target_sets"]
        return 6


class PlanEvaluation:
    """
    Evaluates a generated plan: checks that no muscle exceeds max sets per week.
    Returns valid flag and list of suggestions if not valid.
    """

    def __init__(self, goals: AgentGoals):
        self.goals = goals

    def evaluate(self, plan: dict[str, list[dict]]) -> tuple[bool, list[str]]:
        """
        plan: {"Day1": [{"name": "...", "sets": 3, "target": "chest"}, ...], ...}
        Returns (is_valid, suggestions).
        """
        volume_per_muscle: dict[str, int] = {}
        for day_exercises in plan.values():
            for ex in day_exercises:
                sets = ex.get("sets", 3)
                target = ex.get("target")
                targets = ex.get("targets") or ([target] if target else [])
                if not targets and target:
                    targets = [target]
                for m in targets:
                    if m:
                        volume_per_muscle[m] = volume_per_muscle.get(m, 0) + sets

        max_sets = self.goals.max_sets_per_muscle_per_week
        suggestions: list[str] = []
        for muscle, total in volume_per_muscle.items():
            if total > max_sets:
                over = total - max_sets
                suggestions.append(f"Reduce sets for {muscle} by {over} (current: {total}, max: {max_sets}).")

        return (len(suggestions) == 0, suggestions)
