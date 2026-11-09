"""Workspace-local display-name uniqueness for agent creation."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier

from xnobrain.integrations import AgentAPIError, AgentManager


class AgentNameUniquenessTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.manager = self.manager_for("first")

    def manager_for(self, workspace):
        root = self.root / workspace
        return AgentManager(
            root_profile=root / "root",
            profiles_root=root / "profiles",
            legacy_agents_root=root / "legacy",
        )

    def test_duplicate_display_name_with_different_id_is_rejected(self):
        self.manager.create_agent({"name": "imported-id", "display_name": "HPG Analyst"})
        for name in ["HPG Analyst", "hpg analyst", "  HPG   Analyst  "]:
            with self.subTest(name=name), self.assertRaises(AgentAPIError) as caught:
                self.manager.create_agent({"name": "new-id", "display_name": name})
            self.assertEqual(caught.exception.status, 409)
            self.assertEqual(caught.exception.code, "agent_exists")
            self.assertFalse((self.manager.profiles_root / "new-id").exists())

    def test_same_name_in_another_workspace_is_allowed(self):
        body = {"name": "analyst", "display_name": "HPG Analyst"}
        self.manager.create_agent(body)
        _, status = self.manager_for("second").create_agent(body)
        self.assertEqual(status, 201)

    def test_idempotent_retry_keeps_existing_agent(self):
        body = {"name": "analyst", "display_name": "HPG Analyst", "idempotent": True}
        self.manager.create_agent(body)
        _, status = self.manager.create_agent(body)
        self.assertEqual(status, 200)

    def test_concurrent_creations_have_one_winner(self):
        barrier = Barrier(2)

        def create(index):
            manager = self.manager_for("first")
            barrier.wait()
            try:
                return manager.create_agent(
                    {"name": f"analyst-{index}", "display_name": "HPG Analyst"}
                )[1]
            except AgentAPIError as error:
                return error.status

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(create, range(2))), [201, 409])
