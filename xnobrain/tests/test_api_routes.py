"""Public API namespace and modular route ownership tests."""

import unittest

from xnobrain.models import KanbanTaskCreate, KanbanTaskPatch
from xnobrain.routes.definition import API_PREFIX
from xnobrain.routes.setup import ROUTE_GROUPS, ROUTES


class APIRouteContractTests(unittest.TestCase):
    def test_every_route_uses_the_current_brain_api_prefix(self):
        self.assertGreater(len(ROUTE_GROUPS), 1)
        self.assertTrue(ROUTES)
        for route in ROUTES:
            with self.subTest(method=route.method, path=route.path):
                self.assertTrue(route.path.startswith(f"{API_PREFIX}/"))

    def test_route_method_and_path_pairs_are_unique(self):
        keys = [(route.method, route.path) for route in ROUTES]
        self.assertEqual(len(keys), len(set(keys)))

    def test_legacy_route_families_are_not_declared(self):
        legacy = (
            "/api/brain/",
            "/api/v1/",
            "/agent-gateway/v1/",
            "/conversations/v1/",
            "/sandboxes/v1/",
        )
        for route in ROUTES:
            self.assertFalse(route.path.startswith(legacy), route.path)

    def test_task_contract_allows_every_selected_skill(self):
        skills = [f"skill-{index}" for index in range(100)]

        created = KanbanTaskCreate(
            title="Task using every skill",
            description="Run with the complete agent skill set.",
            skills=skills,
        )
        patched = KanbanTaskPatch(skills=skills)

        self.assertEqual(created.skills, skills)
        self.assertEqual(patched.skills, skills)


if __name__ == "__main__":
    unittest.main()
