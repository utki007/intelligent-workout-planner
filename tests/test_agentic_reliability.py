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

    def test_krr_maps_injury_condition_to_forbidden_muscles(self):
        df = pd.DataFrame(
            [
                {"name": "Squat", "target": "quads", "difficulty": "beginner"},
                {"name": "Biceps Curl", "target": "biceps", "difficulty": "beginner"},
            ]
        )
        user_id = "krr_injury_map_user"
        load_kb(df, user_id, ["knee"])
        recommended = set(query_recommended(user_id))
        self.assertNotIn("Squat", recommended)
        self.assertIn("Biceps Curl", recommended)

    def test_krr_filters_when_secondary_muscle_is_injured(self):
        df = pd.DataFrame(
            [
                {
                    "name": "Calf Raise + Rear Delt Assist",
                    "target": "calves",
                    "secondaryMuscles": "['rear deltoids']",
                    "difficulty": "beginner",
                },
                {
                    "name": "Leg Extension",
                    "target": "quads",
                    "secondaryMuscles": "['hamstrings']",
                    "difficulty": "beginner",
                },
            ]
        )
        user_id = "krr_secondary_user"
        load_kb(df, user_id, ["shoulder"])
        recommended = set(query_recommended(user_id))
        self.assertNotIn("Calf Raise + Rear Delt Assist", recommended)
        self.assertIn("Leg Extension", recommended)


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
    def test_planner_removes_forbidden_theme_muscles_from_day_metadata(self, mock_build):
        mock_build.return_value = {"unexpected": "shape"}

        df = pd.DataFrame(
            [
                {"name": "Shoulder Press", "target": "delts", "difficulty": "beginner"},
                {"name": "Triceps Pushdown", "target": "triceps", "difficulty": "beginner"},
                {"name": "Bench Press", "target": "pectorals", "difficulty": "beginner"},
            ]
        )
        state = AgentState(user_id="theme_meta_user")
        goals = AgentGoals()
        profile = {
            "user_id": "theme_meta_user",
            "name": "theme_meta_user",
            "experience_level": "beginner",
            "injury_muscles": ["chest"],
            "days_per_week": 4,
            "intensity": 3,
            "fitness_goals": "stay active",
        }

        _, _, _, day_theme_muscles = generate_weekly_plan("theme_meta_user", profile, df, state, goals)
        self.assertNotIn("pectorals", day_theme_muscles.get("Day 1", []))

    @patch("planner.weekly_planner.LLM.build_weekly_plan")
    def test_planner_marks_day_as_rest_when_no_safe_exercises(self, mock_build):
        mock_build.return_value = {"unexpected": "shape"}

        df = pd.DataFrame(
            [
                {"name": "Bench Press", "target": "pectorals", "difficulty": "beginner"},
                {"name": "Pec Deck", "target": "pectorals", "difficulty": "beginner"},
            ]
        )
        state = AgentState(user_id="rest_day_user")
        goals = AgentGoals()
        profile = {
            "user_id": "rest_day_user",
            "name": "rest_day_user",
            "experience_level": "beginner",
            "injury_muscles": ["chest"],
            "days_per_week": 2,
            "intensity": 3,
            "fitness_goals": "recover",
        }

        plan, _, day_themes, day_theme_muscles = generate_weekly_plan("rest_day_user", profile, df, state, goals)
        for day, exercises in plan.items():
            self.assertEqual(exercises, [])
            self.assertEqual(day_themes.get(day), "Rest")
            self.assertEqual(day_theme_muscles.get(day), [])

    @patch("planner.weekly_planner.LLM.build_weekly_plan")
    def test_five_day_chest_theme_becomes_rest_instead_of_substitute(self, mock_build):
        mock_build.return_value = {"unexpected": "shape"}

        df = pd.DataFrame(
            [
                {"name": "Bench Press", "target": "pectorals", "difficulty": "beginner"},
                {"name": "Dumbbell Biceps Curl", "target": "biceps", "difficulty": "beginner"},
                {"name": "Triceps Extension", "target": "triceps", "difficulty": "beginner"},
                {"name": "Lat Pulldown", "target": "lats", "difficulty": "beginner"},
            ]
        )
        state = AgentState(user_id="five_day_chest_rest_user")
        goals = AgentGoals()
        profile = {
            "user_id": "five_day_chest_rest_user",
            "name": "five_day_chest_rest_user",
            "experience_level": "beginner",
            "injury_muscles": ["chest"],
            "days_per_week": 5,
            "intensity": 3,
            "fitness_goals": "stay active",
        }

        plan, _, day_themes, day_theme_muscles = generate_weekly_plan(
            "five_day_chest_rest_user", profile, df, state, goals
        )

        # Day 1 in 5-day split is "Chest" theme; with chest injury it must be Rest,
        # and must not be auto-filled with unrelated arm exercises.
        self.assertEqual(day_themes.get("Day 1"), "Rest")
        self.assertEqual(day_theme_muscles.get("Day 1"), [])
        self.assertEqual(plan.get("Day 1", []), [])


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
