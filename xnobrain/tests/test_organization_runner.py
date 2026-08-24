"""Contract tests for the organization runner connector.

A fake control plane is served through an httpx MockTransport that enforces the
member auth token and the organization-scoped runner paths, proving the client
frames requests the way the control plane expects.
"""

from __future__ import annotations

import json
import unittest

import httpx

import threading

from xnobrain.integrations.organization_runner import (
    DEFAULT_CONTROL_PREFIX,
    CommandResult,
    OrganizationRunnerClient,
    RunnerCommand,
    RunnerError,
    RunnerOrchestrator,
    RunnerSupervisor,
)

ORG = "org_01"
TOKEN = "member-auth-token"


def _envelope(data, *, success=True, message="", status=200):
    return httpx.Response(status, json={"success": success, "data": data, "message": message, "status_code": status})


class FakeControlPlane:
    def __init__(self) -> None:
        self.commands: list[dict] = []
        self.results: list[dict] = []
        self.acknowledged: list[str] = []
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(f"{request.method} {path}")
        # Every runner call authenticates with the member's bearer token.
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return _envelope(None, success=False, message="workload_identity_required", status=401)
        base = f"{DEFAULT_CONTROL_PREFIX}/organizations/{ORG}/runner"
        if not path.startswith(base):
            return _envelope(None, success=False, message="not_found", status=404)
        body = json.loads(request.content or b"{}")

        if path == base + "/agent-registrations" and request.method == "PUT":
            return _envelope({"accepted_revision": body["inventory_revision"], "registrations": body["agents"]})
        if path == base + "/heartbeat":
            return _envelope({"status": "active", "availability": body.get("availability")})
        if path == base + "/commands:poll":
            out, self.commands = self.commands, []
            return _envelope({"commands": out, "next_cursor": "cur_1"})
        if path.endswith("/ack"):
            self.acknowledged.append(path.split("/commands/")[1].split("/")[0])
            return _envelope({"command_id": "cmd_01", "state": body["state"]})
        if path.endswith("/lease"):
            return _envelope({"lease_id": body["lease_id"], "expires_at": "2999-01-01T00:00:00Z"})
        if path.endswith("/result"):
            self.results.append(body)
            return _envelope({"command_id": "cmd_01", "recorded_state": body["state"], "duplicate": False})
        return _envelope(None, success=False, message="not_found", status=404)


class OrganizationRunnerClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plane = FakeControlPlane()
        self.http = httpx.Client(transport=httpx.MockTransport(self.plane.handler))
        self.client = OrganizationRunnerClient(
            "https://control.example", ORG, TOKEN, http_client=self.http
        )

    def tearDown(self) -> None:
        self.http.close()

    def test_requires_base_org_and_token(self) -> None:
        with self.assertRaises(RunnerError):
            OrganizationRunnerClient("", ORG, TOKEN, http_client=self.http)
        with self.assertRaises(RunnerError):
            OrganizationRunnerClient("https://c", "", TOKEN, http_client=self.http)
        with self.assertRaises(RunnerError):
            OrganizationRunnerClient("https://c", ORG, "", http_client=self.http)

    def test_bad_token_is_unauthorized(self) -> None:
        client = OrganizationRunnerClient("https://control.example", ORG, "wrong", http_client=self.http)
        with self.assertRaises(RunnerError) as ctx:
            client.heartbeat()
        self.assertEqual(ctx.exception.status, 401)

    def test_inventory_heartbeat_and_command_lifecycle(self) -> None:
        self.client.publish_inventory(
            1,
            [{"agent_public_id": "agent.local", "display_name": "Local", "capabilities": ["summarize"], "availability": "online", "revision": 1}],
        )
        self.client.heartbeat()

        self.plane.commands = [
            {"command_id": "cmd_01", "run_id": "run_01", "step_id": "stp_01", "registration_id": "agt_01", "grant_id": "grt_01", "lease_id": "lea_01", "lease_token": "tok", "attempt": 1}
        ]
        commands, cursor = self.client.poll_commands(max_commands=5)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].command_id, "cmd_01")
        self.assertEqual(commands[0].grant_id, "grt_01")
        self.assertEqual(cursor, "cur_1")

        self.client.acknowledge("cmd_01")
        self.assertEqual(self.plane.acknowledged, ["cmd_01"])
        self.client.renew_lease("cmd_01", "lea_01", extend_seconds=30)
        result = self.client.submit_result(
            "cmd_01",
            state="completed",
            result_digest="d" * 64,
            safe_summary={"status": "ok"},
            usage={"model": "m", "input_tokens": 10, "output_tokens": 5},
        )
        self.assertFalse(result["duplicate"])
        self.assertEqual(self.plane.results[0]["state"], "completed")
        self.assertIn("occurred_at", self.plane.results[0])

    def test_all_calls_are_organization_scoped(self) -> None:
        self.client.heartbeat()
        self.assertTrue(
            all(f"/organizations/{ORG}/runner" in call for call in self.plane.calls),
            self.plane.calls,
        )

    def test_token_can_be_rotated(self) -> None:
        self.client.set_auth_token(TOKEN)  # same value, should still work
        self.client.heartbeat()
        with self.assertRaises(RunnerError):
            self.client.set_auth_token("")


class _RecordingExecutor:
    def __init__(self, result=None, raises=None):
        self.result = result
        self.raises = raises
        self.executed: list[str] = []

    def execute(self, command: RunnerCommand) -> CommandResult:
        self.executed.append(command.command_id)
        if self.raises is not None:
            raise self.raises
        return self.result or CommandResult(state="completed", safe_summary={"ok": True})


class RunnerOrchestratorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plane = FakeControlPlane()
        self.http = httpx.Client(transport=httpx.MockTransport(self.plane.handler))
        self.client = OrganizationRunnerClient("https://control.example", ORG, TOKEN, http_client=self.http)

    def tearDown(self) -> None:
        self.http.close()

    def _command(self) -> None:
        self.plane.commands = [
            {"command_id": "cmd_01", "run_id": "run_01", "step_id": "stp_01", "registration_id": "agt_01", "lease_id": "lea_01", "attempt": 1}
        ]

    def test_run_once_executes_and_reports_completed(self) -> None:
        self._command()
        executor = _RecordingExecutor(CommandResult(state="completed", safe_summary={"status": "ok"}, usage={"input_tokens": 3}))
        processed = RunnerOrchestrator(self.client, executor).run_once()
        self.assertEqual(processed, 1)
        self.assertEqual(executor.executed, ["cmd_01"])
        self.assertEqual(self.plane.acknowledged, ["cmd_01"])
        self.assertEqual(len(self.plane.results), 1)
        self.assertEqual(self.plane.results[0]["state"], "completed")
        self.assertEqual(len(self.plane.results[0]["result_digest"]), 64)

    def test_executor_failure_is_reported_as_failed(self) -> None:
        self._command()
        executor = _RecordingExecutor(raises=RuntimeError("boom"))
        RunnerOrchestrator(self.client, executor).run_once()
        self.assertEqual(self.plane.results[0]["state"], "failed")
        self.assertEqual(self.plane.results[0]["error_code"], "execution_error")

    def test_result_digest_is_stable_for_same_outcome(self) -> None:
        command = RunnerCommand.from_json({"command_id": "cmd_x"})
        result = CommandResult(state="completed", safe_summary={"a": 1})
        self.assertEqual(result.digest_for(command), result.digest_for(command))

    def test_run_forever_stops_when_event_set(self) -> None:
        executor = _RecordingExecutor()
        orch = RunnerOrchestrator(self.client, executor)
        stop = threading.Event()
        stop.set()  # already stopped: loop must return immediately
        orch.run_forever(stop, idle_sleep_seconds=0.01)


class RunnerSupervisorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plane = FakeControlPlane()
        self.http = httpx.Client(transport=httpx.MockTransport(self.plane.handler))

    def tearDown(self) -> None:
        self.http.close()

    def test_connect_publishes_inventory_and_runs_then_disconnects(self) -> None:
        agents = [{"agent_public_id": "agent.local", "display_name": "Local", "capabilities": [], "availability": "online", "revision": 1}]
        supervisor = RunnerSupervisor(
            client_factory=lambda org, token: OrganizationRunnerClient("https://control.example", org, token, http_client=self.http),
            executor_factory=lambda client: _RecordingExecutor(),
            inventory_provider=lambda: agents,
        )
        supervisor.connect(ORG, TOKEN)
        try:
            # Inventory was published as part of connect.
            self.assertTrue(any("/runner/agent-registrations" in call for call in self.plane.calls))
            self.assertTrue(supervisor.is_connected(ORG))
        finally:
            supervisor.disconnect(ORG)
        self.assertFalse(supervisor.is_connected(ORG))


if __name__ == "__main__":
    unittest.main()
