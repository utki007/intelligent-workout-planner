from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from agent.agent_state import AgentGoals, AgentState
from agent.decision_policy import DecisionPolicy
from agent.krr import load_kb, query_recommended
from agent.mcp import run_plan_cycle
from planner.weekly_planner import generate_weekly_plan


class KRRRegressionTests(unittest.TestCase):
    def test_krr_filters_injury_and_non_beginner(self):
        df = pd.DataFrame(
            [
                {"name": "Bench Press", "target": "pectorals", "difficulty": "beginner"},
                {"name": "Shoulder Press", "target": "delts", "difficulty": "beginner"},
                {"name": "Snatch", "target": "delts", "difficulty": "advanced"},
            ]
        )
        user_id = "krr_user"
        load_kb(df, user_id, ["shoulders"])
        recommended = set(query_recommended(user_id))
        self.assertIn("Bench Press", recommended)
        self.assertNotIn("Shoulder Press", recommended)
        self.assertNotIn("Snatch", recommended)

    def test_krr_maps_joint_injury_to_muscles(self):
        df = pd.DataFrame(
            [
                {"name": "Squat", "target": "quads", "difficulty": "beginner"},
                {"name": "Leg Curl", "target": "hamstrings", "difficulty": "beginner"},
                {"name": "Biceps Curl", "target": "biceps", "difficulty": "beginner"},
            ]
        )
        user_id = "krr_joint_map_user"
        load_kb(df, user_id, ["knee"])
        recommended = set(query_recommended(user_id))
        self.assertNotIn("Squat", recommended)
        self.assertNotIn("Leg Curl", recommended)
        self.assertIn("Biceps Curl", recommended)


class PolicyRegressionTests(unittest.TestCase):
    def test_policy_reduces_overtrained_muscle(self):
        state = AgentState(
            user_id="policy_user",
            soreness_per_muscle={"pectorals": 4.5},
            weekly_volume_per_muscle={"pectorals": 30},
        )
        goals = AgentGoals(max_sets_per_muscle_per_week=20)
        policy = DecisionPolicy(state, goals)
        prescription = policy.get_prescription()
        chest = prescription["pectorals"]
        self.assertEqual(chest["priority"], "low")
        self.assertLessEqual(chest["scale"], 0.6)


class PlannerRegressionTests(unittest.TestCase):
    @patch("planner.weekly_planner.LLM.build_weekly_plan")
    def test_planner_falls_back_when_llm_schema_invalid(self, mock_build):
        mock_build.return_value = {"unexpected": "shape"}

        df = pd.DataFrame(
            [
                {"name": "Bench Press", "target": "pectorals", "difficulty": "beginner"},
                {"name": "Squat", "target": "quads", "difficulty": "beginner"},
                {"name": "Row", "target": "lats", "difficulty": "beginner"},
            ]
        )
        state = AgentState(user_id="planner_user")
        goals = AgentGoals()
        profile = {
            "user_id": "planner_user",
            "name": "planner_user",
            "experience_level": "beginner",
            "injury_muscles": [],
            "days_per_week": 2,
            "intensity": 3,
            "fitness_goals": "build strength",
        }

        plan, krr_reasoning, day_themes, day_theme_muscles = generate_weekly_plan(
            "planner_user", profile, df, state, goals
        )

        self.assertEqual(len(plan), 2)
        self.assertEqual(set(plan.keys()), {"Day 1", "Day 2"})
        self.assertEqual(set(krr_reasoning.keys()), {"Day 1", "Day 2"})
        self.assertEqual(set(day_themes.keys()), {"Day 1", "Day 2"})
        self.assertEqual(set(day_theme_muscles.keys()), {"Day 1", "Day 2"})

    @patch("planner.weekly_planner.LLM.build_weekly_plan")
    def test_planner_never_returns_injured_chest_targets(self, mock_build):
        mock_build.return_value = {"unexpected": "shape"}

        df = pd.DataFrame(
            [
                {"name": "Bench Press", "target": "pectorals", "difficulty": "beginner"},
                {"name": "Shoulder Press", "target": "delts", "difficulty": "beginner"},
                {"name": "Triceps Pushdown", "target": "triceps", "difficulty": "beginner"},
            ]
        )
        state = AgentState(user_id="injury_user")
        goals = AgentGoals()
        profile = {
            "user_id": "injury_user",
            "name": "injury_user",
            "experience_level": "beginner",
            "injury_muscles": ["chest"],
            "days_per_week": 2,
            "intensity": 3,
            "fitness_goals": "stay active",
        }

        plan, _, _, _ = generate_weekly_plan("injury_user", profile, df, state, goals)

        all_targets = [
            str(ex.get("target", "")).lower()
            for day_exercises in plan.values()
            for ex in day_exercises
        ]
        self.assertNotIn("pectorals", all_targets)

    @patch("planner.weekly_planner.LLM.build_weekly_plan")
    def test_fallback_respects_intensity_for_exercise_count(self, mock_build):
        mock_build.return_value = {"unexpected": "shape"}

        df = pd.DataFrame(
            [
                {"name": "Chest Press A", "target": "pectorals", "difficulty": "beginner"},
                {"name": "Chest Press B", "target": "pectorals", "difficulty": "beginner"},
                {"name": "Chest Press C", "target": "pectorals", "difficulty": "beginner"},
                {"name": "Chest Press D", "target": "pectorals", "difficulty": "beginner"},
                {"name": "Chest Press E", "target": "pectorals", "difficulty": "beginner"},
                {"name": "Biceps Curl A", "target": "biceps", "difficulty": "beginner"},
                {"name": "Biceps Curl B", "target": "biceps", "difficulty": "beginner"},
                {"name": "Biceps Curl C", "target": "biceps", "difficulty": "beginner"},
                {"name": "Biceps Curl D", "target": "biceps", "difficulty": "beginner"},
                {"name": "Biceps Curl E", "target": "biceps", "difficulty": "beginner"},
                {"name": "Triceps Pushdown A", "target": "triceps", "difficulty": "beginner"},
                {"name": "Triceps Pushdown B", "target": "triceps", "difficulty": "beginner"},
            ]
        )
        state = AgentState(user_id="intensity_count_user")
        goals = AgentGoals()
        profile = {
            "user_id": "intensity_count_user",
            "name": "intensity_count_user",
            "experience_level": "beginner",
            "injury_muscles": [],
            "days_per_week": 2,
            "intensity": 4,
            "fitness_goals": "build strength",
        }

        plan, _, _, _ = generate_weekly_plan("intensity_count_user", profile, df, state, goals)
        for day_exercises in plan.values():
            # Intensity 4 should not collapse to 3/day when catalog is sufficient.
            self.assertGreaterEqual(len(day_exercises), 5)


class MCPRegressionTests(unittest.TestCase):
    @patch("agent.mcp.generate_weekly_plan")
    def test_run_plan_cycle_enforces_injury_post_filter(self, mock_generate):
        mock_generate.return_value = (
            {
                "Day 1": [
                    {"name": "Bench Press", "sets": 3, "reps": 10, "target": "pectorals"},
                    {"name": "Row", "sets": 3, "reps": 10, "target": "lats"},
                ]
            },
            {"Day 1": "reasoning"},
            {"Day 1": "Upper Push"},
            {"Day 1": ["pectorals", "delts", "triceps"]},
        )

        df = pd.DataFrame(
            [
                {"name": "Bench Press", "target": "pectorals", "difficulty": "beginner"},
                {"name": "Row", "target": "lats", "difficulty": "beginner"},
            ]
        )
        profile = {
            "user_id": "mcp_user",
            "injury_muscles": ["chest"],
            "days_per_week": 2,
            "experience_level": "beginner",
        }
        goals = AgentGoals()

        plan, _, _, _, _, _ = run_plan_cycle("mcp_user", df, profile, goals=goals)
        targets = [ex.get("target") for ex in plan.get("Day 1", [])]
        self.assertNotIn("pectorals", [str(t).lower() for t in targets])


if __name__ == "__main__":
    unittest.main()
