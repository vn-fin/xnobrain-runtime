"""Private owner configuration projections and immutable revision checks."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from xnobrain.services.base import ServiceError
from xnobrain.services.personal_resource_definitions import PersonalResourceDefinitions


class PersonalDefinitionTests(unittest.TestCase):
    def setUp(self):
        self.platform = SimpleNamespace(
            repository=SimpleNamespace(_id=lambda value: str(value), get_team=Mock()),
            agents=SimpleNamespace(describe_agent=Mock()),
            kanban=SimpleNamespace(get_board=Mock()),
        )
        self.service = PersonalResourceDefinitions(self.platform)

    def test_agent_projection_excludes_private_data(self):
        self.platform.agents.describe_agent.return_value = {
            "metadata": {"display_name": "Writer", "description": "Writes", "secret": "hidden"},
            "config": {"api_key": "secret-value"},
            "soul": "private prompt",
            "memory": "private memory",
            "path": "/private/workspace",
            "skills": ["private"],
        }
        definition = self.service.load("agent", "writer", {})
        self.assertEqual(
            definition["configuration"],
            {"agent_id": "writer", "name": "Writer", "description": "Writes"},
        )
        self.assertNotIn("private", json.dumps(definition))
        self.assertNotIn("secret", json.dumps(definition))
        self.platform.agents.describe_agent.assert_called_once_with("writer", include_memory=False)

    def test_team_snapshot_retains_typed_references_not_runs(self):
        ref = {"organization_id": "org_a", "registration_id": "agt_a", "grant_id": "dgr_a"}
        team = {
            "id": "team",
            "name": "Mixed",
            "orchestrator_id": "big-brother",
            "members": [{"agent_id": "external_a", "role": "writer", "external_agent": ref}],
            "runs": [{"output": "private result"}],
            "source_path": "/private",
        }
        self.platform.repository.get_team.return_value = team
        first = self.service.load("agent_team", "team", {})
        self.assertEqual(first["agent_references"][-1]["external_agent"], ref)
        self.assertNotIn("runs", first["configuration"])
        same = self.service.load(
            "agent_team", "team", {"source_revision": first["source_revision"]}
        )
        self.assertEqual(same, first)
        team["members"][0]["external_agent"] = {**ref, "grant_id": "dgr_changed"}
        with self.assertRaises(ServiceError) as raised:
            self.service.load("agent_team", "team", {"source_revision": first["source_revision"]})
        self.assertEqual(raised.exception.status, 409)

    def test_board_snapshot_never_contains_live_tasks(self):
        self.platform.kanban.get_board.return_value = {
            "name": "Work",
            "description": "Board",
            "tasks": [{"body": "private"}],
            "db_path": "/private/tasks.db",
            "revision": "mutable",
        }
        result = self.service.load("board", "work", {})
        self.assertEqual(result["configuration"], {"name": "Work", "description": "Board"})
        self.assertEqual(result["agent_references"], [])
