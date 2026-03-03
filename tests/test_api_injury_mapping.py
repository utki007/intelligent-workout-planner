from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from api.app import app


class InjuryResolveApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_resolve_endpoint_maps_condition_inputs(self):
        response = self.client.post(
            "/api/injury/resolve",
            json={"injury_muscles": ["knee"]},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        forbidden = set(payload.get("forbidden_muscles", []))

        # injury.json mapping: knee should imply these are forbidden
        self.assertIn("quads", forbidden)
        self.assertIn("hamstrings", forbidden)
        self.assertIn("calves", forbidden)

    def test_resolve_endpoint_blocks_direct_secondary_muscle_input(self):
        response = self.client.post(
            "/api/injury/resolve",
            json={"injury_muscles": ["rear deltoids"]},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        forbidden = set(payload.get("forbidden_muscles", []))
        self.assertIn("rear deltoids", forbidden)


if __name__ == "__main__":
    unittest.main()
