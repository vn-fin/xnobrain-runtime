"""Public API namespace and modular route ownership tests."""

import unittest

from brain4all.routes.definition import API_PREFIX
from brain4all.routes.setup import ROUTES, ROUTE_GROUPS


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
            "/api/v1/",
            "/agent-gateway/v1/",
            "/conversations/v1/",
            "/sandboxes/v1/",
        )
        for route in ROUTES:
            self.assertFalse(route.path.startswith(legacy), route.path)


if __name__ == "__main__":
    unittest.main()
