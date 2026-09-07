import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xnobrain.repositories.base import RepositoryBase, StoreError
from xnobrain.repositories.organization_connector import OrganizationConnectorRepository
from xnobrain.services.organization_connector import OrganizationConnector


class Platform:
    def __init__(self, root):
        self.repository = RepositoryBase(Path(root) / "data", Path(root) / "profiles")

    def list_agents(self):
        return [{"id": "agent-1", "display_name": "Agent One"}]

    def agent_activity(self):
        return {"agents": {"agent-1": "idle"}}


class ConnectorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.platform = Platform(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    async def test_dormant_without_explicit_enrollment(self):
        with patch.dict("os.environ", {}, clear=True):
            c = OrganizationConnector(self.platform)
        self.assertFalse(c.enabled)
        await c.start()
        self.assertIsNone(c.task)

    def test_journal_deduplicates_and_bounds_replay_digest(self):
        store = OrganizationConnectorRepository(self.platform.repository)
        store.record("cmd", "a", "done")
        self.assertTrue(store.seen("cmd"))
        with self.assertRaises(StoreError):
            store.record("cmd", "b", "done")
        store.enqueue({"idempotency_key": "once", "url": "x", "body": {}})
        store.enqueue({"idempotency_key": "once", "url": "y", "body": {}})
        self.assertEqual(len(store.outbox()), 1)

    def test_inventory_is_safe_allowlist(self):
        with patch.dict(
            "os.environ",
            {
                "RUNTIME_CONTROL_URL": "https://control.example",
                "RUNTIME_ORGANIZATION_ID": "org_1",
                "RUNTIME_RUNNER_ENROLLMENT_PROOF": "proof",
            },
        ):
            c = OrganizationConnector(self.platform)
        row = c.inventory()[0]
        self.assertEqual(
            set(row),
            {"agent_public_id", "display_name", "capabilities", "availability", "revision"},
        )


class ConnectorPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.platform = Platform(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_execution_body_applies_remote_ceiling(self):
        with patch.dict(
            "os.environ",
            {
                "RUNTIME_CONTROL_URL": "https://control.example",
                "RUNTIME_ORGANIZATION_ID": "org_1",
                "RUNTIME_RUNNER_ENROLLMENT_PROOF": "proof",
            },
        ):
            c = OrganizationConnector(self.platform)
        from datetime import datetime, timedelta, timezone

        body = c.execution_body(
            {
                "allowed_toolsets": ["search"],
                "allowed_skills": ["summarize"],
                "limits": {"max_duration_seconds": 45},
                "approval_mode": "every_run",
            },
            "bounded task",
            datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        self.assertEqual(body["toolsets"], ["search"])
        self.assertEqual(body["skills"], ["summarize"])
        self.assertEqual(body["timeout_seconds"], 45)
        self.assertEqual(body["approval_mode"], "every_run")

    def test_device_key_is_persistent_and_private(self):
        with patch.dict(
            "os.environ",
            {
                "RUNTIME_CONTROL_URL": "https://control.example",
                "RUNTIME_ORGANIZATION_ID": "org_1",
                "RUNTIME_RUNNER_ENROLLMENT_PROOF": "proof",
            },
        ):
            c = OrganizationConnector(self.platform)
        first = c.key().private_bytes_raw()
        second = c.key().private_bytes_raw()
        self.assertEqual(first, second)
        self.assertEqual(c.key_path.stat().st_mode & 0o777, 0o600)


class OutputDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.platform = Platform(self.tmp.name)
        (self.platform.repository.profiles_root / "agent-1").mkdir()
        self.platform.agents = type(
            "Agents", (), {"workspace_dir": lambda _s, _a: Path(self.tmp.name) / "workspace"}
        )()
        (Path(self.tmp.name) / "workspace").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    async def test_no_output_directory_publishes_nothing(self):
        with patch.dict(
            "os.environ",
            {
                "RUNTIME_CONTROL_URL": "https://control.example",
                "RUNTIME_ORGANIZATION_ID": "org_1",
                "RUNTIME_RUNNER_ENROLLMENT_PROOF": "proof",
            },
        ):
            c = OrganizationConnector(self.platform)
        self.assertEqual(
            await c.publish_outputs(
                None,
                {},
                {"run_id": "run", "command_id": "cmd", "idempotency_key": "key"},
                "agent-1",
            ),
            [],
        )
