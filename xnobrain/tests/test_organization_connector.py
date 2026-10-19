import asyncio
import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from xnobrain.integrations import organization_funding
from xnobrain.repositories.base import RepositoryBase, StoreError
from xnobrain.repositories.organization_connector import OrganizationConnectorRepository
from xnobrain.services.base import ServiceError
from xnobrain.services.organization_connector import OrganizationConnector

ORG_A = "org_aaaaaaaa11111111"
ORG_B = "org_bbbbbbbb22222222"
CONTROL = "https://control.example"


class FakeControl:
    """Runner endpoints of Control with per-organization one-time tokens."""

    def __init__(self, *tokens):
        self.tokens = set(tokens)
        self.challenges = {}
        self.devices = {}
        self.calls = []
        self.commands = []
        self.refuse_refresh = False
        self.artifacts_status = 200

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/xnobrain/api/control/v1")
        body = json.loads(request.content or b"{}")
        self.calls.append(path)
        if path == "/runner/enrollment-challenges":
            token = body["enrollment_token"]
            if token not in self.tokens:
                return httpx.Response(401, json={"message": "workload_identity_required"})
            self.tokens.discard(token)
            organization = token.split(".", 1)[0]
            self.challenges["chl_" + organization] = organization
            return httpx.Response(
                200, json={"data": {"challenge_id": "chl_" + organization, "challenge": "nonce"}}
            )
        if path == "/runner/enroll":
            organization = self.challenges.pop(body["challenge_id"])
            access = "access-" + organization
            self.devices[access] = organization
            return httpx.Response(
                200,
                json={
                    "data": {
                        "device_id": "dev_" + organization,
                        "access_token": access,
                        "expires_at": "2999-01-01T00:00:00Z",
                        "refresh_token": "refresh-" + organization,
                    }
                },
            )
        if path == "/runner/token":
            if self.refuse_refresh:
                return httpx.Response(401, json={"message": "workload_identity_required"})
            organization = body["refresh_token"].removeprefix("refresh-")
            self.devices["access-" + organization] = organization
            return httpx.Response(
                200,
                json={
                    "data": {
                        "access_token": "access-" + organization,
                        "expires_at": "2999-01-01T00:00:00Z",
                    }
                },
            )
        organization = self.devices.get(
            request.headers.get("authorization", "").removeprefix("Bearer ")
        )
        if organization is None or f"/organizations/{organization}/" not in path:
            return httpx.Response(401, json={"message": "workload_identity_required"})
        if path.endswith("/runner/agent-registrations"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "registrations": [
                            {"registration_id": "agt_" + a["agent_public_id"], **a}
                            for a in body["agents"]
                        ]
                    }
                },
            )
        if path.endswith("/runner/heartbeat"):
            return httpx.Response(200, json={"data": {}})
        if path.endswith("/runner/commands:poll"):
            return httpx.Response(200, json={"data": {"commands": self.commands}})
        if path.endswith("/ack") or path.endswith("/lease"):
            return httpx.Response(200, json={"data": {}})
        if path.endswith("/artifacts"):
            return httpx.Response(self.artifacts_status, json={"data": {"items": []}})
        return httpx.Response(404)


class Platform:
    def __init__(self, root):
        self.repository = RepositoryBase(Path(root) / "data", Path(root) / "profiles")
        self.agents = SimpleNamespace(pinned_auxiliary_models=lambda agent_id: [])

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
        store = OrganizationConnectorRepository(self.platform.repository, ORG_A)
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
            c = OrganizationConnector(self.platform).link("org_1abcdefgh")
        self.assertEqual(
            await c.publish_outputs(
                None,
                {},
                {"run_id": "run", "command_id": "cmd", "idempotency_key": "key"},
                "agent-1",
            ),
            [],
        )

    async def test_output_upload_names_the_executing_agent(self):
        # Control rejects an output upload without the agent that produced it.
        workspace = Path(self.tmp.name) / "workspace"
        outputs = workspace / "organization-runs" / "run" / "outputs"
        outputs.mkdir(parents=True)
        (outputs / "report.md").write_text("done")
        self.platform.agents = SimpleNamespace(workspace_dir=lambda agent_id: workspace)
        self.platform.inspect_organization_artifact = lambda agent_id, body: {
            "size_bytes": 4,
            "sha256": "a" * 64,
        }
        self.platform.publish_organization_artifact = lambda agent_id, body: None
        requests = []

        def handle(request):
            requests.append((request.url.path, json.loads(request.content or b"{}")))
            if request.url.path.endswith("/output-uploads"):
                return httpx.Response(
                    201,
                    json={
                        "data": {
                            "upload_id": "upl_1",
                            "file": {"id": "fil_1"},
                            "version": {"id": "ver_1"},
                            "transfer": {},
                        }
                    },
                )
            return httpx.Response(200, json={"data": {"artifact_id": "fil_1"}})

        with patch.dict("os.environ", {"RUNTIME_CONTROL_URL": CONTROL}, clear=True):
            link = OrganizationConnector(self.platform).link(ORG_A)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            artifacts = await link.publish_outputs(
                client,
                {"access_token": "token"},
                {"run_id": "run", "command_id": "cmd_1", "idempotency_key": "key"},
                "agent-1",
            )
        self.assertEqual(artifacts, [{"artifact_id": "fil_1"}])
        self.assertEqual(requests[0][1]["agent_id"], "agent-1")


class MultiOrganizationConnectorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.platform = Platform(self.tmp.name)
        with patch.dict("os.environ", {"RUNTIME_CONTROL_URL": CONTROL}, clear=True):
            self.connector = OrganizationConnector(self.platform)
        self.token_a = f"{ORG_A}." + "a" * 43
        self.token_b = f"{ORG_B}." + "b" * 43
        self.control = FakeControl(self.token_a)
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.control.handle))

    async def asyncTearDown(self):
        await self.client.aclose()
        self.tmp.cleanup()

    def states(self):
        return {
            item["organization_id"]: (item["state"], item["error_code"])
            for item in self.connector.connections()["items"]
        }

    async def test_organizations_enroll_independently(self):
        self.connector.connect({"organization_id": ORG_A, "enrollment_token": self.token_a})
        self.connector.connect({"organization_id": ORG_B, "enrollment_token": self.token_b})
        self.assertEqual(self.states()[ORG_A], ("pending", None))

        await self.connector.link(ORG_A).run_once(self.client)
        await self.connector.link(ORG_B).run_once(self.client)

        self.assertEqual(self.states()[ORG_A], ("connected", None))
        self.assertEqual(self.states()[ORG_B], ("error", "enrollment_token_invalid"))
        stored = self.connector.link(ORG_B).store.state()
        self.assertIsNone(stored.get("enrollment_token"))
        state_a = self.connector.link(ORG_A).store.state()
        self.assertEqual(state_a["registrations"], {"agt_agent-1": "agent-1"})
        self.assertEqual(self.connector.link(ORG_A).store.state_path.stat().st_mode & 0o777, 0o600)

    async def test_inventory_revision_advances_only_when_inventory_changes(self):
        self.connector.connect({"organization_id": ORG_A, "enrollment_token": self.token_a})
        link = self.connector.link(ORG_A)
        await link.tick(self.client)
        await link.tick(self.client)
        self.assertEqual(link.store.state()["inventory_revision"], 1)
        self.platform.agent_activity = lambda: {"agents": {"agent-1": "running"}}
        await link.tick(self.client)
        self.assertEqual(link.store.state()["inventory_revision"], 2)
        self.assertEqual(link.store.state()["heartbeat_sequence"], 3)

    async def test_refused_refresh_revokes_and_stops(self):
        self.connector.connect({"organization_id": ORG_A, "enrollment_token": self.token_a})
        link = self.connector.link(ORG_A)
        await link.tick(self.client)
        state = link.store.state()
        state["expires_at"] = "2000-01-01T00:00:00Z"
        link.store.save_state(state)
        self.control.refuse_refresh = True

        await link.run_once(self.client)

        self.assertEqual(self.states()[ORG_A], ("revoked", "device_revoked"))
        self.assertIsNone(link.store.state().get("refresh_token"))

    async def test_unreachable_control_keeps_credentials_and_backs_off(self):
        self.connector.connect({"organization_id": ORG_A, "enrollment_token": self.token_a})
        link = self.connector.link(ORG_A)
        await link.tick(self.client)

        def offline(request):
            raise httpx.ConnectError("down", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(offline)) as client:
            delay = await link.run_once(client)

        self.assertEqual(self.states()[ORG_A], ("error", "control_unreachable"))
        self.assertEqual(link.store.state()["access_token"], f"access-{ORG_A}")
        self.assertEqual(delay, self.connector.interval * 2)
        self.assertEqual(link.failed(httpx.ConnectError("down"), 200), 300)

    def test_connect_rejects_foreign_tokens_and_unconfigured_runtime(self):
        with self.assertRaises(StoreError) as raised:
            self.connector.connect({"organization_id": ORG_A, "enrollment_token": self.token_b})
        self.assertEqual(raised.exception.code, "invalid_enrollment_token")
        with patch.dict("os.environ", {}, clear=True):
            dormant = OrganizationConnector(self.platform)
        with self.assertRaises(StoreError) as raised:
            dormant.connect({"organization_id": ORG_A, "enrollment_token": self.token_a})
        self.assertEqual(raised.exception.status, 503)

    def test_disconnect_removes_local_state(self):
        self.connector.connect({"organization_id": ORG_A, "enrollment_token": self.token_a})
        self.connector.disconnect(ORG_A)
        self.assertEqual(self.connector.connections(), {"items": []})
        self.assertFalse((self.connector.store.root / ORG_A).exists())

    def test_env_token_seeds_one_pending_connection(self):
        env = {
            "RUNTIME_CONTROL_URL": CONTROL,
            "RUNTIME_ORGANIZATION_ID": ORG_A,
            "RUNTIME_RUNNER_ENROLLMENT_PROOF": self.token_a,
        }
        with patch.dict("os.environ", env, clear=True):
            connector = OrganizationConnector(self.platform)
        connector.seed_env_connection()
        connector.seed_env_connection()
        self.assertEqual(connector.connections()["items"][0]["state"], "pending")
        self.assertEqual(connector.link(ORG_A).store.state()["enrollment_token"], self.token_a)


class FundedCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.platform = Platform(self.tmp.name)
        self.started = []
        self.release = asyncio.Event()
        self.conversations = []

        def create_conversation(agent_id, body, trusted):
            self.conversations.append(body["ownership_context"])
            return {"id": f"conv-{len(self.conversations)}"}

        async def start_conversation_run(agent_id, conversation_id, body):
            self.started.append(
                (conversation_id, body, organization_funding.funding_for(conversation_id))
            )
            await self.release.wait()
            raise RuntimeError("stop after start")

        self.platform.create_conversation = create_conversation
        self.platform.start_conversation_run = start_conversation_run
        self.platform.import_organization_artifact = lambda *args: {}
        self.platform.runtime_updates = SimpleNamespace(
            register_organization_command=lambda cid: None,
            release_organization_command=AsyncMock(),
        )
        with patch.dict("os.environ", {"RUNTIME_CONTROL_URL": CONTROL}, clear=True):
            self.connector = OrganizationConnector(self.platform)
        self.token = f"{ORG_A}." + "a" * 43
        self.control = FakeControl(self.token)
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.control.handle))

    async def asyncTearDown(self):
        await self.client.aclose()
        self.tmp.cleanup()

    def command(self, cid, funding):
        instruction = "Summarize the figures."
        digest = base64.urlsafe_b64encode(hashlib.sha256(instruction.encode()).digest())
        return {
            "command_id": cid,
            "organization_id": ORG_A,
            "device_id": f"dev_{ORG_A}",
            "run_id": "run_0123456789",
            "registration_id": "agt_agent-1",
            "deadline": "2999-01-01T00:00:00Z",
            "payload_reference": instruction,
            "payload_digest": digest.decode().rstrip("="),
            "work_context_id": "cctx_run",
            "subject_user_id": "initiator",
            "idempotency_key": cid,
            "lease_id": "lea_1",
            "funding": funding,
        }

    async def test_commands_run_beside_heartbeats_with_their_funding(self):
        self.connector.connect({"organization_id": ORG_A, "enrollment_token": self.token})
        link = self.connector.link(ORG_A)
        await link.tick(self.client)
        self.control.commands = [
            self.command("cmd_org", {"mode": "organization", "model": "org/acme/fast"}),
            self.command("cmd_owner", {"mode": "owner"}),
        ]
        await link.tick(self.client)
        for _ in range(50):
            if len(self.started) == 2:
                break
            await asyncio.sleep(0.01)

        # The loop keeps heartbeating while both commands are still running.
        await link.tick(self.client)
        self.assertEqual(set(link.running), {"cmd_org", "cmd_owner"})
        self.assertEqual(link.store.state()["heartbeat_sequence"], 3)

        by_conversation = {
            conversation: (body, funding) for conversation, body, funding in self.started
        }
        org_body, org_funding = by_conversation["conv-1"]
        self.assertEqual(org_body["model"], "org/acme/fast")
        self.assertEqual(org_funding, {"mode": "organization", "model": "org/acme/fast"})
        owner_body, owner_funding = by_conversation["conv-2"]
        self.assertNotIn("model", owner_body)
        self.assertEqual(owner_funding["mode"], "owner")
        self.assertEqual(
            [context["payer_kind"] for context in self.conversations], ["organization", "personal"]
        )

        self.release.set()
        for _ in range(50):
            if not link.running:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(link.running, {})
        self.assertIsNone(organization_funding.funding_for("conv-1"))

    async def test_workspace_command_keeps_the_agents_model(self):
        self.connector.connect({"organization_id": ORG_A, "enrollment_token": self.token})
        link = self.connector.link(ORG_A)
        await link.tick(self.client)
        state = link.store.state()
        task = asyncio.create_task(
            link.command(
                self.client,
                state,
                self.command("cmd_ws", {"mode": "organization", "workspace": True}),
            )
        )
        for _ in range(50):
            if self.started:
                break
            await asyncio.sleep(0.01)
        conversation, body, funding = self.started[0]
        self.assertNotIn("model", body)
        self.assertEqual(funding, {"mode": "organization", "model": ""})
        self.release.set()
        with self.assertRaises(RuntimeError):
            await task

    async def test_owner_stage_pin_is_applied_or_refused_before_conversation(self):
        self.connector.connect({"organization_id": ORG_A, "enrollment_token": self.token})
        link = self.connector.link(ORG_A)
        await link.tick(self.client)
        state = link.store.state()
        with patch.object(link, "import_inputs", new_callable=AsyncMock) as import_inputs:
            await link.command(
                self.client,
                state,
                self.command("cmd_denied_model", {"mode": "owner", "model": "personal/denied"}),
            )
            import_inputs.assert_not_awaited()
        self.assertEqual(self.conversations, [])
        self.assertEqual(self.started, [])
        results = [
            item["body"] for item in link.store.outbox() if "/cmd_denied_model/" in item["url"]
        ]
        self.assertEqual(results[0]["state"], "failed")
        self.assertEqual(results[0]["error_code"], "stage_model_not_allowed")
        task = asyncio.create_task(
            link.command(
                self.client,
                state,
                self.command(
                    "cmd_allowed_model",
                    {
                        "mode": "owner",
                        "model": "personal/allowed",
                        "allowed_models": ["personal/allowed"],
                    },
                ),
            )
        )
        for _ in range(50):
            if self.started:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(self.started[0][1]["model"], "personal/allowed")
        self.release.set()
        with self.assertRaises(RuntimeError):
            await task

    async def test_command_without_funding_never_starts(self):
        self.connector.connect({"organization_id": ORG_A, "enrollment_token": self.token})
        link = self.connector.link(ORG_A)
        await link.tick(self.client)
        state = link.store.state()
        with self.assertRaises(ValueError):
            await link.command(self.client, state, self.command("cmd_none", {}))
        self.assertEqual(self.started, [])

    async def test_organization_funding_refuses_pinned_auxiliary_models(self):
        # Hermes uses the run's model for auxiliary calls unless the profile
        # pins one; a pinned model would bill the agent owner.
        pinned = {"agent-1": ["vision"]}
        self.platform.agents = SimpleNamespace(
            pinned_auxiliary_models=lambda agent_id: pinned.get(agent_id, [])
        )
        self.connector.connect({"organization_id": ORG_A, "enrollment_token": self.token})
        link = self.connector.link(ORG_A)
        await link.tick(self.client)
        state = link.store.state()
        await link.command(
            self.client,
            state,
            self.command("cmd_pinned", {"mode": "organization", "model": "org/acme/fast"}),
        )
        self.assertEqual(self.started, [])
        results = [item["body"] for item in link.store.outbox() if "/cmd_pinned/" in item["url"]]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["state"], "failed")
        self.assertEqual(results[0]["error_code"], "organization_auxiliary_model_configured")

        # Owner funding pays with the owner's own models, so pins are allowed.
        task = asyncio.create_task(
            link.command(self.client, state, self.command("cmd_owner_pinned", {"mode": "owner"}))
        )
        for _ in range(50):
            if self.started:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(len(self.started), 1)
        self.release.set()
        with self.assertRaises(RuntimeError):
            await task

    async def test_acknowledged_command_reports_failure_when_inputs_are_unavailable(self):
        # Control does not re-lease an acknowledged command, so a silent
        # failure would leave the step queued until the run deadline.
        self.control.artifacts_status = 503
        self.connector.connect({"organization_id": ORG_A, "enrollment_token": self.token})
        link = self.connector.link(ORG_A)
        await link.tick(self.client)
        state = link.store.state()
        with self.assertRaises(httpx.HTTPStatusError):
            await link.command(self.client, state, self.command("cmd_inputs", {"mode": "owner"}))
        self.assertEqual(self.started, [])
        results = [item["body"] for item in link.store.outbox() if "/cmd_inputs/" in item["url"]]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["state"], "failed")
        self.assertEqual(results[0]["error_code"], "runtime_execution_failed")

    async def test_completed_answer_is_reported_when_outputs_cannot_be_published(self):
        async def start_conversation_run(agent_id, conversation_id, body):
            return {"id": "run-1"}

        async def renew(client, state, cmd, done):
            await done.wait()

        self.platform.start_conversation_run = start_conversation_run
        self.platform.conversation_runs = SimpleNamespace(
            get_run=lambda *args: {"status": "completed", "output": "Result: PASSED"}
        )
        self.connector.connect({"organization_id": ORG_A, "enrollment_token": self.token})
        link = self.connector.link(ORG_A)
        await link.tick(self.client)
        state = link.store.state()
        failure = ServiceError(
            "transfer authorization is invalid", status=400, code="invalid_transfer_authorization"
        )
        with (
            patch.object(link, "renew", renew),
            patch.object(link, "publish_outputs", AsyncMock(side_effect=failure)),
        ):
            await link.command(self.client, state, self.command("cmd_outputs", {"mode": "owner"}))
        results = [item["body"] for item in link.store.outbox() if "/cmd_outputs/" in item["url"]]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["state"], "completed")
        self.assertEqual(results[0]["error_code"], "")
        self.assertEqual(results[0]["artifacts"], [])
        self.assertEqual(results[0]["safe_summary"]["output"], "Result: PASSED")
        self.assertEqual(
            results[0]["safe_summary"]["output_publish_error"], "invalid_transfer_authorization"
        )
