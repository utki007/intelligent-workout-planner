from __future__ import annotations

import unittest

from api.app import _extract_days_hint, _heuristic_intent, _heuristic_profile_updates


class IntentRoutingTests(unittest.TestCase):
    def test_extract_days_hint_numeric(self):
        self.assertEqual(_extract_days_hint("make me a 4 day split"), 4)

    def test_extract_days_hint_word(self):
        self.assertEqual(_extract_days_hint("I want a three day workout"), 3)

    def test_plan_like_prompt_routes_generate_plan(self):
        intent, days = _heuristic_intent("make me a plan")
        self.assertEqual(intent, "generate_plan")
        self.assertIsNone(days)

    def test_profile_update_routes_profile_intent(self):
        intent, days = _heuristic_intent("update my injuries and intensity")
        self.assertEqual(intent, "profile_update")
        self.assertIsNone(days)

    def test_non_plan_prompt_remains_chat(self):
        intent, days = _heuristic_intent("why was this exercise picked?")
        self.assertEqual(intent, "chat")
        self.assertIsNone(days)

    def test_heuristic_profile_updates_detects_recovery(self):
        updates = _heuristic_profile_updates(
            "My shoulder is better now",
            {"injury_muscles": ["delts", "quads"]},
        )
        self.assertEqual(updates.get("injury_update", {}).get("status"), "recovered")
        self.assertEqual(updates.get("injury_update", {}).get("operation"), "remove")
        self.assertEqual(updates.get("injury_muscles"), ["quads"])

    def test_heuristic_profile_updates_detects_new_injury(self):
        updates = _heuristic_profile_updates(
            "I hurt my shoulder during training",
            {"injury_muscles": []},
        )
        self.assertEqual(updates.get("injury_update", {}).get("status"), "injured")
        self.assertEqual(updates.get("injury_update", {}).get("operation"), "add")
        self.assertIn("delts", updates.get("injury_muscles", []))

    def test_heuristic_profile_updates_detects_cleared_injuries(self):
        updates = _heuristic_profile_updates(
            "I am fully recovered and have no injuries now",
            {"injury_muscles": ["delts", "quads"]},
        )
        self.assertEqual(updates.get("injury_update", {}).get("status"), "cleared")
        self.assertEqual(updates.get("injury_update", {}).get("operation"), "clear")
        self.assertEqual(updates.get("injury_muscles"), [])


if __name__ == "__main__":
    unittest.main()
