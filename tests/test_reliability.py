from __future__ import annotations

import unittest

from planner.constraints import (
    MAX_DAYS_PER_WEEK,
    MIN_DAYS_PER_WEEK,
    clamp_days_per_week,
    normalize_requested_days,
)
from planner.validation import verify_day_specs


class ConstraintsTests(unittest.TestCase):
    def test_clamp_days(self):
        self.assertEqual(clamp_days_per_week(1), MIN_DAYS_PER_WEEK)
        self.assertEqual(clamp_days_per_week(99), MAX_DAYS_PER_WEEK)
        self.assertEqual(clamp_days_per_week("4"), 4)
        self.assertEqual(clamp_days_per_week("bad"), 5)

    def test_normalize_requested_days(self):
        self.assertEqual(normalize_requested_days(2), 2)
        self.assertEqual(normalize_requested_days("7"), 7)
        self.assertIsNone(normalize_requested_days(1))
        self.assertIsNone(normalize_requested_days(8))
        self.assertIsNone(normalize_requested_days("bad"))


class VerificationTests(unittest.TestCase):
    def test_verify_rejects_unknown_exercise(self):
        day_specs = [
            {
                "day": "Day1",
                "theme": "Lower Body",
                "exercises": [
                    {"name": "Unknown Exercise", "sets": 3, "reps": 12, "target": "quads"},
                ],
            }
        ]
        result = verify_day_specs(
            day_specs,
            expected_days=1,
            allowed_exercises={"Squat"},
            min_sets=2,
            max_sets=4,
            min_reps=6,
            max_reps=20,
            max_exercises_per_day=6,
        )
        self.assertFalse(result.is_valid)
        self.assertGreater(result.stats["unknown_exercises"], 0)

    def test_verify_accepts_valid_plan(self):
        day_specs = [
            {
                "day": "Day1",
                "theme": "Lower Body",
                "exercises": [
                    {"name": "Squat", "sets": 3, "reps": 10, "target": "quads"},
                    {"name": "Lunge", "sets": 2, "reps": 12, "target": "glutes"},
                ],
            },
            {
                "day": "Day2",
                "theme": "Upper Body",
                "exercises": [
                    {"name": "Bench Press", "sets": 4, "reps": 8, "target": "pectorals"},
                ],
            },
        ]
        result = verify_day_specs(
            day_specs,
            expected_days=2,
            allowed_exercises={"Squat", "Lunge", "Bench Press"},
            min_sets=2,
            max_sets=4,
            min_reps=6,
            max_reps=20,
            max_exercises_per_day=6,
        )
        self.assertTrue(result.is_valid)
        self.assertEqual(result.stats["issue_count"], 0)


if __name__ == "__main__":
    unittest.main()
