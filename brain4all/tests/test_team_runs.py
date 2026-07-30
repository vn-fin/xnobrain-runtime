"""Persistent, observable, cancellable team runs (plan 010).

Compatibility assertions for the Hermes execution surface plus async lifecycle,
cancellation (including the real subprocess kill), SSE shape, restart staleness,
record sanitization, and retention.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import yaml

from brain4all.app import Brain4AllApplication
from brain4all.integrations import AgentManager, GlobalConfigManager
from brain4all.repositories.files import TEAM_RUN_RETENTION


_HERMES_BINARY = shutil.which(os.environ.get("HERMES_CLI", "hermes"))
_STEP_SCHEMA_KEYS = {
    "id", "agent_id", "role", "task", "needs", "allowed_tools", "skills", "status",
    "summary", "summary_chars", "error", "conversation_id", "started_at", "ended_at",
}
_RUN_SCHEMA_KEYS = {
    "id", "team_id", "status", "error", "mode", "task", "synthesis_instruction",
    "orchestrator_id", "orchestrator_summary", "created_at", "started_at",
    "ended_at", "updated_at", "revision", "steps",
}


class FakeRouter:
    async def list_connections(self):
        return {"connections": []}

    async def list_models(self):
        return {"data": []}


class CompatibilityTests(unittest.TestCase):
    """Tripwires guarding the exact Hermes symbols the team path rides."""

    def test_chat_is_a_coroutine_with_the_expected_signature(self):
        self.assertTrue(inspect.iscoroutinefunction(AgentManager.chat))
        self.assertEqual(list(inspect.signature(AgentManager.chat).parameters), ["self", "raw_name", "body"])

    def test_adapter_surface_exists(self):
        for name in ("stop_run", "_run_hermes_command", "_chat_stream_events"):
            self.assertTrue(hasattr(AgentManager, name), name)

    def test_profile_inventory_imports(self):
        from hermes_cli.profiles import list_profiles  # noqa: F401

    def test_cancelled_error_kill_fix_is_present(self):
        source = inspect.getsource(AgentManager._run_hermes_command)
        self.assertIn("CancelledError", source)

    @unittest.skipUnless(_HERMES_BINARY, "hermes binary is not installed")
    def test_hermes_cli_still_accepts_the_composed_flags(self):
        help_text = subprocess.run([_HERMES_BINARY, "--help"], capture_output=True, text=True, timeout=30).stdout
        for flag in ("-z", "--resume", "--toolsets", "--skills", "--model"):
            self.assertIn(flag, help_text, flag)


class _TeamRunBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.root = base / "root"
        self.profiles = base / "profiles"
        self.root.mkdir(parents=True)
        self.profiles.mkdir(parents=True)
        (self.root / "config.yaml").write_text(yaml.safe_dump({
            "model": {"provider": "custom:nine-router", "default": "auto"},
            "providers": {}, "agent": {"reasoning_effort": "medium"},
            "approvals": {"mode": "manual"}, "terminal": {"backend": "local"},
        }), encoding="utf-8")
        self.environment = patch.dict(os.environ, {
            "HERMES_HOME": str(self.root), "HERMES_ROOT_PROFILE": str(self.root),
            "HERMES_PROFILES_ROOT": str(self.profiles), "DATA_DIR": self.temporary.name,
        })
        self.environment.start()
        app = FastAPI()
        self.composition = Brain4AllApplication(
            AgentManager(root_profile=self.root, profiles_root=self.profiles, legacy_agents_root=base / "legacy-agents"),
            GlobalConfigManager(root_profile=self.root), FakeRouter(),
        )
        self.composition.register(app)
        self.app = app
        self.data_dir = base

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def client(self):
        return AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")

    async def _make_team(self, client) -> tuple[str, list[str]]:
        ids = []
        for display_name in ("Coordinator", "Researcher", "Reviewer"):
            response = await client.post("/api/brain/v1/agents", json={"display_name": display_name})
            ids.append(response.json()["data"]["id"])
        team = await client.post("/api/brain/v1/teams", json={
            "name": "DAG team",
            "orchestrator_id": ids[0],
            "members": [
                {"agent_id": ids[1], "role": "researcher", "allowed_tools": ["web"]},
                {"agent_id": ids[2], "role": "reviewer", "allowed_tools": ["web"]},
            ],
            "max_parallel": 2,
        })
        return team.json()["data"]["id"], ids

    def _dag_body(self) -> dict:
        return {
            "workflow": [
                {"id": "research", "task": "Research the API", "role": "researcher"},
                {"id": "review", "task": "Review the findings", "role": "reviewer", "needs": ["research"]},
            ],
            "synthesis": "Produce the final answer.",
        }

    def _mock_completing_chat(self, ids: list[str]) -> AsyncMock:
        async def fake_chat(agent_id, body):
            if agent_id == ids[0]:
                return {"response": "final synthesis", "conversation_id": "c-final"}
            if agent_id == ids[1]:
                return {"response": "research result", "conversation_id": "c-research"}
            return {"response": "review result", "conversation_id": "c-review"}

        mock = AsyncMock(side_effect=fake_chat)
        self.composition.service.agents.chat = mock
        return mock

    async def _poll_until_terminal(self, client, team_id, run_id, tries=100):
        for _ in range(tries):
            response = await client.get(f"/api/brain/v1/teams/{team_id}/runs/{run_id}")
            record = response.json()["data"]
            if record["status"] in {"completed", "failed", "cancelled"}:
                return record
            await asyncio.sleep(0.02)
        self.fail(f"run {run_id} did not reach a terminal state")


class TeamRunLifecycleTests(_TeamRunBase):
    async def test_team_defaults_to_all_enabled_skills_and_reuses_a_profile_across_nodes(self):
        async with self.client() as client:
            ids = []
            for display_name in ("Coordinator", "Researcher"):
                response = await client.post("/api/brain/v1/agents", json={"display_name": display_name})
                ids.append(response.json()["data"]["id"])

            def listed_skills(agent_id):
                enabled = "team-planning" if agent_id == ids[0] else "news-research"
                return {"skills": [
                    {"skill_id": enabled, "enabled": True},
                    {"skill_id": "disabled-skill", "enabled": False},
                ]}

            with patch.object(self.composition.service.agents, "list_skills", side_effect=listed_skills):
                created = await client.post("/api/brain/v1/teams", json={
                    "name": "Repeated profile DAG",
                    "orchestrator_id": ids[0],
                    "members": [
                        {"agent_id": ids[0], "role": "planner", "allowed_tools": ["todo"]},
                        {"agent_id": ids[1], "role": "researcher", "allowed_tools": []},
                    ],
                    "workflow": [
                        {
                            "id": "plan", "task": "Check the execution plan",
                            "agent_id": ids[0], "role": "planner",
                        },
                        {
                            "id": "research", "task": "Research the subject",
                            "agent_id": ids[1], "role": "researcher", "needs": ["plan"],
                        },
                        {
                            "id": "verify", "task": "Verify the research",
                            "agent_id": ids[1], "role": "verifier",
                            "skills": [], "needs": ["research"],
                        },
                    ],
                    "synthesis_agent_id": ids[1],
                })

        self.assertEqual(created.status_code, 201, created.text)
        team = created.json()["data"]
        self.assertEqual(
            team["coordinator_prompt"],
            "Plan the workflow and give every stage clear, actionable execution guidance.",
        )
        self.assertEqual(
            team["synthesis_instruction"],
            "Synthesize all completed stage outputs into one clear, accurate final answer.",
        )
        self.assertEqual(team["coordinator_skills"], ["team-planning"])
        self.assertEqual(team["synthesis_skills"], ["news-research"])
        self.assertEqual(
            [step["agent_id"] for step in team["workflow"]],
            [ids[0], ids[1], ids[1]],
        )
        self.assertEqual(team["workflow"][0]["skills"], ["team-planning"])
        self.assertEqual(team["workflow"][1]["skills"], ["news-research"])
        self.assertEqual(team["workflow"][2]["skills"], [])

    async def test_async_run_lifecycle(self):
        async with self.client() as client:
            team_id, ids = await self._make_team(client)
            self._mock_completing_chat(ids)
            start = await client.post(f"/api/brain/v1/teams/{team_id}/runs", json=self._dag_body())
            self.assertEqual(start.status_code, 202, start.text)
            record = start.json()["data"]
            run_id = record["id"]
            self.assertTrue(run_id.startswith("tr_"))
            self.assertIn(record["status"], {"pending", "running"})

            final = await self._poll_until_terminal(client, team_id, run_id)

        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["orchestrator_summary"], "final synthesis")
        by_id = {step["id"]: step for step in final["steps"]}
        self.assertEqual(by_id["research"]["status"], "completed")
        self.assertEqual(by_id["research"]["summary"], "research result")
        self.assertEqual(by_id["research"]["conversation_id"], "c-research")
        self.assertEqual(by_id["review"]["status"], "completed")
        self.assertEqual([step["id"] for step in final["steps"]], ["research", "review"])

        path = self.data_dir / "teams" / "runs" / team_id / f"{run_id}.json"
        self.assertTrue(path.is_file())
        stored = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(set(stored), _RUN_SCHEMA_KEYS)
        self.assertEqual(set(stored["steps"][0]), _STEP_SCHEMA_KEYS)

    async def test_team_inherits_agent_tools_and_supports_skills_scratchpad_and_dialogue(self):
        async with self.client() as client:
            team_id, ids = await self._make_team(client)
            updated = await client.put(f"/api/brain/v1/teams/{team_id}", json={
                "name": "Capable DAG team",
                "orchestrator_id": ids[0],
                "members": [
                    {"agent_id": ids[1], "role": "researcher", "allowed_tools": []},
                    {"agent_id": ids[2], "role": "reviewer", "allowed_tools": []},
                ],
                "workflow": [
                    {
                        "id": "research", "task": "Research current news",
                        "agent_id": ids[1], "role": "researcher",
                        "allowed_tools": [], "skills": ["news-research"],
                    },
                    {
                        "id": "review", "task": "Review the findings",
                        "agent_id": ids[2], "role": "reviewer", "needs": ["research"],
                        "allowed_tools": [], "skills": ["critical-review"],
                    },
                ],
                "communication_level": 3,
                "shared_workspace": True,
                "coordinator_prompt": "Guide the workers with a source-first plan.",
                "coordinator_allowed_tools": ["todo", "web"],
                "coordinator_skills": ["team-planning"],
                "synthesis_agent_id": ids[2],
                "synthesis_allowed_tools": ["file"],
                "synthesis_skills": ["final-writing"],
                "synthesis_instruction": "Synthesize these workflow results with citations.",
                "max_parallel": 2,
                "max_depth": 1,
            })
            self.assertEqual(updated.status_code, 200, updated.text)

            calls: list[tuple[str, dict]] = []

            async def capable_chat(agent_id, body):
                calls.append((agent_id, dict(body)))
                message = body["message"]
                if "Return concise execution guidance" in message:
                    return {"response": "Verify dates and cite primary sources."}
                if "Synthesize these workflow results with citations" in message:
                    return {"response": "final synthesis"}
                if "Review the downstream" in message:
                    return {"response": "Cite the primary source."}
                if "Revise your draft" in message:
                    return {"response": "reviewed result", "conversation_id": "c-review-final"}
                if agent_id == ids[1]:
                    return {"response": "research result", "conversation_id": "c-research"}
                return {"response": "review draft", "conversation_id": "c-review"}

            self.composition.service.agents.chat = AsyncMock(side_effect=capable_chat)
            response = await client.post(f"/api/brain/v1/teams/{team_id}/run", json={})

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["data"]
        self.assertEqual(result["workflow_results"][1]["summary"], "reviewed result")
        self.assertEqual(result["orchestrator_summary"], "final synthesis")
        coordinator_call = next(body for agent_id, body in calls if agent_id == ids[0])
        self.assertEqual(coordinator_call["skills"], ["team-planning"])
        self.assertEqual(coordinator_call["toolsets"], ["todo", "web"])
        research_call = next(body for agent_id, body in calls if agent_id == ids[1] and "Task: Research current news" in body["message"])
        self.assertNotIn("toolsets", research_call)
        self.assertEqual(research_call["skills"], ["news-research"])
        self.assertIn("Verify dates and cite primary sources.", research_call["message"])
        reviewer_call = next(body for agent_id, body in calls if agent_id == ids[2] and "Task: Review the findings" in body["message"])
        self.assertIn("[research] research result", reviewer_call["message"])
        self.assertIn("Shared team scratchpad:", reviewer_call["message"])
        scratchpads = list((self.data_dir / "teams" / "workspaces" / team_id).glob("*/SCRATCHPAD.md"))
        self.assertEqual(len(scratchpads), 1)
        self.assertIn("reviewed result", scratchpads[0].read_text(encoding="utf-8"))
        synthesis_call = next(
            body for agent_id, body in calls
            if agent_id == ids[2] and "Synthesize these workflow results with citations" in body["message"]
        )
        self.assertEqual(synthesis_call["skills"], ["final-writing"])
        self.assertEqual(synthesis_call["toolsets"], ["file"])

    async def test_completed_run_can_be_deleted(self):
        async with self.client() as client:
            team_id, ids = await self._make_team(client)
            self._mock_completing_chat(ids)
            started = await client.post(f"/api/brain/v1/teams/{team_id}/runs", json=self._dag_body())
            run_id = started.json()["data"]["id"]
            await self._poll_until_terminal(client, team_id, run_id)

            deleted = await client.delete(f"/api/brain/v1/teams/{team_id}/runs/{run_id}")
            missing = await client.get(f"/api/brain/v1/teams/{team_id}/runs/{run_id}")
            history = await client.get(f"/api/brain/v1/teams/{team_id}/runs")

        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json()["data"], {
            "id": run_id,
            "team_id": team_id,
            "deleted": True,
        })
        self.assertEqual(missing.status_code, 404)
        self.assertNotIn(run_id, [record["id"] for record in history.json()["data"]])
        self.assertFalse((self.data_dir / "teams" / "runs" / team_id / f"{run_id}.json").exists())

    async def test_sync_run_persists_and_keeps_legacy_shape(self):
        async with self.client() as client:
            team_id, ids = await self._make_team(client)
            self._mock_completing_chat(ids)
            response = await client.post(f"/api/brain/v1/teams/{team_id}/run", json=self._dag_body())
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["data"]
        self.assertEqual(set(result), {
            "team_id", "member_results", "workflow_results", "orchestrator_summary",
            "started_at", "completed_at",
        })
        self.assertEqual([item["id"] for item in result["workflow_results"]], ["research", "review"])
        self.assertEqual(result["orchestrator_summary"], "final synthesis")
        self.assertEqual(result["member_results"][0]["summary"], "research result")

        runs_dir = self.data_dir / "teams" / "runs" / team_id
        files = list(runs_dir.glob("*.json"))
        self.assertEqual(len(files), 1)
        stored = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(stored["mode"], "sync")
        self.assertEqual(stored["status"], "completed")

    async def test_run_active_conflict(self):
        gate = asyncio.Event()

        async def gated(agent_id, body):
            await gate.wait()
            return {"response": "done", "conversation_id": "c1"}

        self.composition.service.agents.chat = AsyncMock(side_effect=gated)
        async with self.client() as client:
            team_id, _ = await self._make_team(client)
            first = await client.post(f"/api/brain/v1/teams/{team_id}/runs", json=self._dag_body())
            self.assertEqual(first.status_code, 202, first.text)
            second = await client.post(f"/api/brain/v1/teams/{team_id}/runs", json=self._dag_body())
            self.assertEqual(second.status_code, 409, second.text)
            self.assertEqual(second.json()["error"]["code"], "team_run_active")
            deleting = await client.delete(f"/api/brain/v1/teams/{team_id}/runs/{first.json()['data']['id']}")
            self.assertEqual(deleting.status_code, 409, deleting.text)
            self.assertEqual(deleting.json()["error"]["code"], "team_run_active")
            await client.post(f"/api/brain/v1/teams/{team_id}/runs/{first.json()['data']['id']}/cancel")

    async def test_cancel_marks_steps_and_record(self):
        gate = asyncio.Event()  # never set

        async def blocking(agent_id, body):
            await gate.wait()
            return {"response": "unreachable"}

        self.composition.service.agents.chat = AsyncMock(side_effect=blocking)
        async with self.client() as client:
            team_id, _ = await self._make_team(client)
            start = await client.post(f"/api/brain/v1/teams/{team_id}/runs", json=self._dag_body())
            run_id = start.json()["data"]["id"]
            for _ in range(100):
                current = (await client.get(f"/api/brain/v1/teams/{team_id}/runs/{run_id}")).json()["data"]
                if any(step["status"] == "running" for step in current["steps"]):
                    break
                await asyncio.sleep(0.02)
            cancel = await client.post(f"/api/brain/v1/teams/{team_id}/runs/{run_id}/cancel")

        self.assertEqual(cancel.status_code, 200, cancel.text)
        record = cancel.json()["data"]
        self.assertEqual(record["status"], "cancelled")
        self.assertIsNotNone(record["ended_at"])
        self.assertTrue(all(step["status"] in {"completed", "failed", "cancelled"} for step in record["steps"]))
        self.assertTrue(any(step["status"] == "cancelled" for step in record["steps"]))
        self.assertEqual(self.composition.service.team_runs._active, {})

    async def test_cancel_kills_subprocess(self):
        manager = self.composition.service.agents
        created: dict[str, int] = {}
        real = asyncio.create_subprocess_exec

        async def recording(*args, **kwargs):
            proc = await real(*args, **kwargs)
            created["pid"] = proc.pid
            return proc

        with patch("brain4all.integrations.hermes.asyncio.create_subprocess_exec", side_effect=recording):
            task = asyncio.ensure_future(
                manager._run_hermes_command(self.root, self.root, ["/bin/sleep", "60"], timeout_seconds=120)
            )
            for _ in range(200):
                if "pid" in created:
                    break
                await asyncio.sleep(0.02)
            self.assertIn("pid", created, "subprocess was never spawned")
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        pid = created["pid"]
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.05)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)


class TeamRunStreamAndStoreTests(_TeamRunBase):
    async def test_sse_event_shape(self):
        gate = asyncio.Event()

        async def gated(agent_id, body):
            await gate.wait()
            return {"response": "summary", "conversation_id": "c1"}

        self.composition.service.agents.chat = AsyncMock(side_effect=gated)
        async with self.client() as client:
            team_id, _ = await self._make_team(client)
            start = await client.post(f"/api/brain/v1/teams/{team_id}/runs", json=self._dag_body())
            run_id = start.json()["data"]["id"]

        class FakeRequest:
            path_params = {"team_id": team_id, "run_id": run_id}
            query_params: dict = {}
            headers: dict = {}

            async def is_disconnected(self):
                return False

        response = await self.composition.handlers.team_run_event_stream(FakeRequest())
        iterator = response.body_iterator
        frames = [await anext(iterator)]  # connected
        gate.set()
        async for chunk in iterator:
            frames.append(chunk)

        text = [frame if isinstance(frame, str) else frame.decode() for frame in frames]
        self.assertIn("event: connected", text[0])
        run_frames = [frame for frame in text if "event: run" in frame]
        self.assertTrue(run_frames, "no run frames streamed")
        records = [json.loads(frame.split("data: ", 1)[1]) for frame in run_frames]
        revisions = [record["revision"] for record in records]
        self.assertEqual(revisions, sorted(revisions))
        self.assertGreater(revisions[-1], 0)
        self.assertEqual(set(records[-1]), _RUN_SCHEMA_KEYS)
        self.assertIn("event: done", text[-1])

    async def test_restart_staleness(self):
        repository = self.composition.repository
        team_id = "a" * 32
        run_id = "tr_" + "b" * 32
        stale = {
            "id": run_id, "team_id": team_id, "status": "running", "error": None,
            "mode": "async", "task": "t", "synthesis_instruction": "s",
            "orchestrator_id": "o", "orchestrator_summary": "",
            "created_at": "2026-07-25T00:00:00Z", "started_at": "2026-07-25T00:00:00Z",
            "ended_at": None, "updated_at": "2026-07-25T00:00:00Z", "revision": 3,
            "steps": [{
                "id": "research", "agent_id": "w", "role": "researcher", "task": "t",
                "needs": [], "allowed_tools": ["web"], "status": "running", "summary": "",
                "summary_chars": 0, "error": None, "conversation_id": None,
                "started_at": "2026-07-25T00:00:00Z", "ended_at": None,
            }],
        }
        repository.put_team_run(stale)
        async with self.client() as client:
            response = await client.get(f"/api/brain/v1/teams/{team_id}/runs/{run_id}")
        record = response.json()["data"]
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["error"], "interrupted_by_restart")
        self.assertEqual(record["steps"][0]["status"], "cancelled")
        self.assertGreater(record["revision"], 3)
        stored = json.loads((self.data_dir / "teams" / "runs" / team_id / f"{run_id}.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["status"], "failed")

    async def test_record_sanitization(self):
        received: dict[str, str] = {}

        async def recording_chat(agent_id, body):
            received[agent_id] = body["message"]
            return {"response": "clean summary", "conversation_id": "c1"}

        self.composition.service.agents.chat = AsyncMock(side_effect=recording_chat)
        async with self.client() as client:
            team_id, ids = await self._make_team(client)
            start = await client.post(f"/api/brain/v1/teams/{team_id}/runs", json=self._dag_body())
            run_id = start.json()["data"]["id"]
            await self._poll_until_terminal(client, team_id, run_id)

        # The composed prompt the engine sent contained the role preamble sentinel.
        self.assertTrue(any("Role:" in message for message in received.values()))
        raw = (self.data_dir / "teams" / "runs" / team_id / f"{run_id}.json").read_text(encoding="utf-8")
        self.assertNotIn("Role:", raw)
        self.assertNotIn("Upstream results", raw)
        stored = json.loads(raw)
        self.assertEqual(set(stored), _RUN_SCHEMA_KEYS)
        self.assertNotIn("stderr", raw)
        self.assertNotIn("stdout", raw)
        for step in stored["steps"]:
            self.assertEqual(set(step), _STEP_SCHEMA_KEYS)

    async def test_retention_prunes(self):
        repository = self.composition.repository
        team_id = "c" * 32
        total = TEAM_RUN_RETENTION + 5
        for index in range(total):
            repository.put_team_run({
                "id": f"tr_{index:032d}", "team_id": team_id, "status": "completed",
                "error": None, "mode": "async", "task": "t", "synthesis_instruction": "s",
                "orchestrator_id": "o", "orchestrator_summary": "",
                "created_at": f"2026-07-25T00:00:{index:05d}Z",
                "started_at": None, "ended_at": None,
                "updated_at": "2026-07-25T00:00:00Z", "revision": 1, "steps": [],
            })
        files = list((self.data_dir / "teams" / "runs" / team_id).glob("*.json"))
        self.assertEqual(len(files), TEAM_RUN_RETENTION)


if __name__ == "__main__":
    unittest.main()
