from __future__ import annotations

import unittest
from unittest.mock import patch

from api.app import (
    _auto_refresh_stored_plan_for_profile,
    _dynamic_profile_fields_changed,
    _merge_regenerated_remaining_days,
)


def _plan(days: int, prefix: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for i in range(1, days + 1):
        out[f"Day {i}"] = [{"name": f"{prefix}-{i}", "sets": 3, "reps": 10, "target": "biceps"}]
    return out


def _krr(days: int, prefix: str) -> dict[str, str]:
    return {f"Day {i}": f"{prefix}-{i}" for i in range(1, days + 1)}


class DynamicPlanUpdateTests(unittest.TestCase):
    def test_dynamic_profile_fields_changed_ignores_non_dynamic_flags(self):
        before = {
            "onboarding_complete": False,
            "days_per_week": 5,
            "intensity": 3,
            "injury_muscles": ["delts"],
            "equipment_preferences": ["dumbbell"],
        }
        after = {
            "onboarding_complete": True,
            "days_per_week": 5,
            "intensity": 3.0,
            "injury_muscles": ["delts"],
            "equipment_preferences": ["dumbbell"],
        }
        self.assertEqual(_dynamic_profile_fields_changed(before, after), [])

    def test_dynamic_profile_fields_changed_detects_actual_training_changes(self):
        before = {
            "days_per_week": 5,
            "intensity": 3,
            "injury_muscles": ["delts"],
            "equipment_preferences": ["dumbbell"],
        }
        after = {
            "days_per_week": 4,
            "intensity": 4,
            "injury_muscles": ["quads"],
            "equipment_preferences": ["barbell"],
        }
        changed = _dynamic_profile_fields_changed(before, after)
        self.assertEqual(
            sorted(changed),
            ["days_per_week", "equipment_preferences", "injury_muscles", "intensity"],
        )

    def test_merge_updates_selected_day_and_after(self):
        existing_plan = _plan(5, "old")
        existing_krr = _krr(5, "old-krr")
        generated_plan = _plan(5, "new")
        generated_krr = _krr(5, "new-krr")
        generated_themes = {f"Day {i}": f"Theme {i}" for i in range(1, 6)}
        generated_muscles = {f"Day {i}": ["biceps"] for i in range(1, 6)}

        merged_plan, merged_krr, _, _, mode = _merge_regenerated_remaining_days(
            existing_plan=existing_plan,
            existing_krr=existing_krr,
            generated_plan=generated_plan,
            generated_krr=generated_krr,
            generated_day_themes=generated_themes,
            generated_day_theme_muscles=generated_muscles,
            selected_day="Day 2",
        )

        self.assertEqual(mode, "remaining")
        self.assertEqual(merged_plan["Day 1"][0]["name"], "old-1")
        self.assertEqual(merged_plan["Day 2"][0]["name"], "new-2")
        self.assertEqual(merged_plan["Day 5"][0]["name"], "new-5")
        self.assertEqual(merged_krr["Day 1"], "old-krr-1")
        self.assertEqual(merged_krr["Day 2"], "new-krr-2")

    def test_merge_falls_back_to_full_when_day_count_changes(self):
        existing_plan = _plan(5, "old")
        existing_krr = _krr(5, "old-krr")
        generated_plan = _plan(4, "new")
        generated_krr = _krr(4, "new-krr")
        generated_themes = {f"Day {i}": f"Theme {i}" for i in range(1, 5)}
        generated_muscles = {f"Day {i}": ["biceps"] for i in range(1, 5)}

        merged_plan, merged_krr, _, _, mode = _merge_regenerated_remaining_days(
            existing_plan=existing_plan,
            existing_krr=existing_krr,
            generated_plan=generated_plan,
            generated_krr=generated_krr,
            generated_day_themes=generated_themes,
            generated_day_theme_muscles=generated_muscles,
            selected_day="Day 2",
        )

        self.assertEqual(mode, "full")
        self.assertEqual(len(merged_plan), 4)
        self.assertEqual(merged_plan["Day 1"][0]["name"], "new-1")
        self.assertEqual(merged_krr["Day 4"], "new-krr-4")

    @patch("api.app.save_plan_for_user")
    @patch("api.app._run_stateless_plan_cycle")
    @patch("api.app._build_user_profile_for_planner")
    @patch("api.app.get_exercises_df")
    @patch("api.app.load_plan_for_user")
    def test_auto_refresh_uses_remaining_mode_when_possible(
        self,
        mock_load_plan,
        mock_get_df,
        mock_build_profile,
        mock_run_cycle,
        mock_save,
    ):
        existing_plan = _plan(5, "old")
        existing_krr = _krr(5, "old-krr")
        generated_plan = _plan(5, "new")
        generated_krr = _krr(5, "new-krr")
        generated_themes = {f"Day {i}": f"Theme {i}" for i in range(1, 6)}
        generated_muscles = {f"Day {i}": ["biceps"] for i in range(1, 6)}

        mock_load_plan.return_value = (existing_plan, existing_krr)
        mock_get_df.return_value = object()
        mock_build_profile.return_value = ({"user_id": "default"}, object())
        mock_run_cycle.return_value = (generated_plan, True, [], generated_krr, generated_themes, generated_muscles)

        payload = _auto_refresh_stored_plan_for_profile(
            user_id="default",
            selected_day="Day 3",
            force_full_regen=False,
        )

        self.assertIsNotNone(payload)
        self.assertEqual(payload["update_mode"], "remaining")
        self.assertEqual(payload["plan"]["Day 1"][0]["name"], "old-1")
        self.assertEqual(payload["plan"]["Day 3"][0]["name"], "new-3")
        mock_save.assert_called_once()

    @patch("api.app.save_plan_for_user")
    @patch("api.app._run_stateless_plan_cycle")
    @patch("api.app._build_user_profile_for_planner")
    @patch("api.app.get_exercises_df")
    @patch("api.app.load_plan_for_user")
    def test_auto_refresh_forced_full_regen(
        self,
        mock_load_plan,
        mock_get_df,
        mock_build_profile,
        mock_run_cycle,
        mock_save,
    ):
        existing_plan = _plan(5, "old")
        existing_krr = _krr(5, "old-krr")
        generated_plan = _plan(4, "new")
        generated_krr = _krr(4, "new-krr")
        generated_themes = {f"Day {i}": f"Theme {i}" for i in range(1, 5)}
        generated_muscles = {f"Day {i}": ["biceps"] for i in range(1, 5)}

        mock_load_plan.return_value = (existing_plan, existing_krr)
        mock_get_df.return_value = object()
        mock_build_profile.return_value = ({"user_id": "default"}, object())
        mock_run_cycle.return_value = (generated_plan, True, [], generated_krr, generated_themes, generated_muscles)

        payload = _auto_refresh_stored_plan_for_profile(
            user_id="default",
            selected_day="Day 2",
            force_full_regen=True,
        )

        self.assertIsNotNone(payload)
        self.assertEqual(payload["update_mode"], "full")
        self.assertEqual(len(payload["plan"]), 4)
        self.assertEqual(payload["plan"]["Day 1"][0]["name"], "new-1")
        mock_save.assert_called_once()


if __name__ == "__main__":
    unittest.main()
