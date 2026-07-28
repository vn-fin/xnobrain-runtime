"""Contract tests for the unified FastAPI composition and file persistence."""

from __future__ import annotations

import os
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from zipfile import ZipFile

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import yaml

from brain4all.app import Brain4AllApplication
from brain4all.defaults import (
    BIG_BROTHER_AGENT_ID,
    BIG_BROTHER_APPROVAL_DEFAULT_MARKER,
    BIG_BROTHER_MODEL_DEFAULT_MARKER,
    BIG_BROTHER_NATIVE_TOOLSETS,
)
from brain4all.integrations import AgentManager, GlobalConfigManager
from brain4all.services import ServiceError


class FakeRouter:
    async def list_connections(self): return {"connections": []}
    async def list_models(self): return {"data": []}


class StudioFastAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "root"
        self.profiles = Path(self.temporary.name) / "profiles"
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
        composition = Brain4AllApplication(
            AgentManager(root_profile=self.root, profiles_root=self.profiles, legacy_agents_root=Path(self.temporary.name) / "legacy-agents"),
            GlobalConfigManager(root_profile=self.root), FakeRouter(),
        )
        composition.register(app)
        self.composition = composition
        self.app = app

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def client(self):
        return AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")

    async def test_big_brother_bootstrap_aliases_root_profile_and_is_idempotent(self):
        enabled = self.root / "skills" / "enabled-default" / "SKILL.md"
        disabled = self.root / "skills" / "disabled-default" / "SKILL.md"
        enabled.parent.mkdir(parents=True)
        disabled.parent.mkdir(parents=True)
        enabled.write_text(
            "---\nname: enabled-default\ndescription: Enabled by default\n---\n",
            encoding="utf-8",
        )
        disabled.write_text(
            "---\nname: disabled-default\ndescription: Disabled by default\n---\n",
            encoding="utf-8",
        )
        config = yaml.safe_load((self.root / "config.yaml").read_text(encoding="utf-8"))
        config["skills"] = {"disabled": ["disabled-default"], "write_approval": True}
        (self.root / "config.yaml").write_text(
            yaml.safe_dump(config, sort_keys=False),
            encoding="utf-8",
        )

        async with self.app.router.lifespan_context(self.app):
            first = self.composition.service.get_agent(BIG_BROTHER_AGENT_ID)

        profile_config_path = self.root / "config.yaml"
        migrated_config = yaml.safe_load(
            profile_config_path.read_text(encoding="utf-8")
        )
        migrated_config["platform_toolsets"]["api_server"].append(
            "brain4all-control"
        )
        migrated_config["approvals"]["mode"] = "manual"
        migrated_config["model"]["default"] = "pinned-before-model-default"
        migrated_config["brain4all"].pop(BIG_BROTHER_APPROVAL_DEFAULT_MARKER)
        migrated_config["brain4all"].pop(BIG_BROTHER_MODEL_DEFAULT_MARKER)
        profile_config_path.write_text(
            yaml.safe_dump(migrated_config, sort_keys=False),
            encoding="utf-8",
        )
        second = await self.composition.service.ensure_default_agent()
        migrated_config = yaml.safe_load(
            profile_config_path.read_text(encoding="utf-8")
        )
        self.assertEqual(migrated_config["approvals"]["mode"], "off")
        self.assertFalse(migrated_config["skills"]["write_approval"])
        self.assertFalse(migrated_config["memory"]["write_approval"])
        self.assertEqual(migrated_config["model"]["default"], "auto")

        migrated_config["approvals"]["mode"] = "manual"
        migrated_config["model"]["default"] = "user-selected-model"
        profile_config_path.write_text(
            yaml.safe_dump(migrated_config, sort_keys=False),
            encoding="utf-8",
        )
        third = await self.composition.service.ensure_default_agent()

        self.assertEqual(first["id"], BIG_BROTHER_AGENT_ID)
        self.assertEqual(second["display_name"], "Big Brother")
        self.assertEqual(third["config"]["approval_mode"], "on")
        self.assertEqual(third["config"]["model"], "user-selected-model")
        agents = self.composition.service.list_agents()
        self.assertEqual(agents[0]["id"], BIG_BROTHER_AGENT_ID)
        self.assertEqual(
            sum(item["id"] == BIG_BROTHER_AGENT_ID for item in agents),
            1,
        )
        skills = {
            item["skill_id"]: item["enabled"]
            for item in self.composition.service.list_skills(BIG_BROTHER_AGENT_ID)
        }
        self.assertEqual(
            skills,
            {
                "big-brother-control": True,
                "disabled-default": False,
                "enabled-default": True,
            },
        )
        self.assertFalse((self.profiles / BIG_BROTHER_AGENT_ID).exists())
        self.assertEqual(first["metadata"]["display_name"], "Big Brother")
        self.assertEqual(
            self.composition.service.agents.describe_agent("default")["name"],
            BIG_BROTHER_AGENT_ID,
        )
        self.assertEqual(
            self.composition.service.agents.describe_agent("Big Brother")["name"],
            BIG_BROTHER_AGENT_ID,
        )
        profile_config = yaml.safe_load(
            profile_config_path.read_text(encoding="utf-8")
        )
        self.assertIn("kanban", profile_config["toolsets"])
        self.assertEqual(
            set(profile_config["platform_toolsets"]["api_server"]),
            set(BIG_BROTHER_NATIVE_TOOLSETS),
        )
        self.assertEqual(profile_config["approvals"]["mode"], "manual")
        self.assertTrue(
            profile_config["brain4all"][BIG_BROTHER_APPROVAL_DEFAULT_MARKER]
        )
        self.assertTrue(
            profile_config["brain4all"][BIG_BROTHER_MODEL_DEFAULT_MARKER]
        )
        with self.assertRaises(ServiceError) as protected:
            self.composition.service.delete_agent(BIG_BROTHER_AGENT_ID)
        self.assertEqual(protected.exception.code, "protected_agent")

        from hermes_cli.tools_config import _get_platform_tools

        enabled_toolsets = _get_platform_tools(profile_config, "api_server")
        self.assertTrue(set(BIG_BROTHER_NATIVE_TOOLSETS).issubset(enabled_toolsets))
        self.assertNotIn("brain4all-control", enabled_toolsets)

        installed = await self.composition.service.install_skill(
            BIG_BROTHER_AGENT_ID,
            {
                "skill_id": "global-from-big-brother",
                "content": (
                    "---\nname: global-from-big-brother\n"
                    "description: Root skill\n---\n"
                ),
            },
        )
        self.assertTrue(
            (
                self.root
                / "skills"
                / "custom"
                / "global-from-big-brother"
                / "SKILL.md"
            ).is_file()
        )
        self.assertIn(
            "global-from-big-brother",
            {item["skill_id"] for item in installed},
        )
        self.assertIn(
            "global-from-big-brother",
            {
                item["skill_id"]
                for item in self.composition.service.list_default_skills()
            },
        )

        custom = self.composition.service.create_agent({"display_name": "Worker"})
        custom_skill_ids = {
            item["skill_id"]
            for item in self.composition.service.list_skills(custom["id"])
        }
        self.assertIn("big-brother-control", custom_skill_ids)
        self.assertIn("global-from-big-brother", custom_skill_ids)
        self.assertTrue(
            (self.profiles / custom["id"] / "skills" / "custom").exists()
        )

    async def test_big_brother_migrates_legacy_named_profile_data_to_root(self):
        legacy = self.profiles / BIG_BROTHER_AGENT_ID
        (legacy / "workspace").mkdir(parents=True)
        (legacy / "agent.json").write_text(
            json.dumps({"name": BIG_BROTHER_AGENT_ID}),
            encoding="utf-8",
        )
        legacy_skill = legacy / "skills" / "custom" / "legacy-installed" / "SKILL.md"
        legacy_skill.parent.mkdir(parents=True)
        legacy_skill.write_text(
            "---\nname: legacy-installed\ndescription: Legacy skill\n---\n",
            encoding="utf-8",
        )
        manager = self.composition.service.agents
        manager._initialize_state_db(legacy)
        manager._create_session(
            legacy,
            "legacy-big-brother-conversation",
            model="auto",
            title="Legacy conversation",
        )
        with sqlite3.connect(legacy / "state.db") as connection:
            connection.execute(
                """
                INSERT INTO messages (session_id, role, content, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (
                    "legacy-big-brother-conversation",
                    "user",
                    "Preserved legacy message",
                    1.0,
                ),
            )
            connection.commit()

        async with self.app.router.lifespan_context(self.app):
            migrated = self.composition.service.get_conversation(
                BIG_BROTHER_AGENT_ID,
                "legacy-big-brother-conversation",
            )

        self.assertTrue(
            (
                self.root
                / "skills"
                / "custom"
                / "legacy-installed"
                / "SKILL.md"
            ).is_file()
        )
        self.assertEqual(
            migrated["messages"][0]["content"],
            "Preserved legacy message",
        )
        self.assertTrue(legacy.is_dir())
        self.assertTrue(
            any((self.root / "snapshots" / "migrations").glob("*.db"))
        )

    async def test_health_identifies_fastapi_database_free_runtime(self):
        async with self.client() as client:
            response = await client.get("/api/v1/health")
            deployment_response = await client.get("/api/v1/system/deployment")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["api"], "fastapi")
        deployment = deployment_response.json()["data"]
        self.assertFalse(deployment["database"])

    async def test_local_only_deployment_has_no_enterprise_proxy_routes(self):
        with patch.dict(os.environ, {"ENTERPRISE_API_URL": "https://control.example.test"}):
            async with self.client() as client:
                deployment = await client.get("/api/v1/system/deployment")
                dashboard = await client.get("/api/v1/dashboard/overview")
                skills = await client.get("/api/v1/skills")
                device = await client.get("/api/v1/device")

        self.assertEqual(deployment.status_code, 200, deployment.text)
        self.assertEqual(deployment.json()["data"], {
            "mode": "local", "runtime": "hermes-fastapi", "runtime_transport": "in-process", "database": False,
        })
        self.assertEqual([dashboard.status_code, skills.status_code, device.status_code], [404, 404, 404])

    async def test_sandbox_detail_reports_only_important_runtime_usage(self):
        async with self.client() as client:
            response = await client.get("/sandboxes/v1/me/sandboxes/detail")
        self.assertEqual(response.status_code, 200, response.text)
        detail = response.json()["data"]
        self.assertEqual(detail["info"]["type"], "container")
        self.assertIn("cpu_percent", detail["metrics"])
        self.assertIn("memory_bytes", detail["metrics"])
        self.assertIn("disk_usage_bytes", detail["metrics"])
        self.assertIn("net_rx_bytes", detail["metrics"])
        self.assertTrue(detail["health"]["healthy"])
        self.assertNotIn("top_processes", json.dumps(detail))

    async def test_sandbox_detail_stream_emits_an_immediate_stats_event(self):
        class ConnectedRequest:
            async def is_disconnected(self):
                return False

        with patch("brain4all.handlers.api.asyncio.sleep", new=AsyncMock()) as sleep:
            response = await self.composition.handlers.sandbox_detail_stream(ConnectedRequest())
            event = await anext(response.body_iterator)
            next_event = await anext(response.body_iterator)
            await response.body_iterator.aclose()

        self.assertEqual(response.media_type, "text/event-stream")
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        sleep.assert_awaited_once_with(1)
        self.assertIn("id: 1\n", next_event)
        self.assertIn("event: stats\n", event)
        payload = json.loads(event.split("data: ", 1)[1])
        self.assertIn("cpu_percent", payload["metrics"])
        self.assertNotIn("top_processes", event)

    async def test_default_profile_installs_skill_from_url_without_returning_command_output(self):
        source = "https://example.com/office-helper/SKILL.md"

        async def install_from_url(_command, *, timeout_seconds):
            self.assertGreater(timeout_seconds, 0)
            skill = self.root / "skills" / "office" / "office-helper" / "SKILL.md"
            skill.parent.mkdir(parents=True, exist_ok=True)
            skill.write_text(
                "---\nname: office-helper\ndescription: Helps with office files\n---\n",
                encoding="utf-8",
            )
            return {"exit_code": 0, "stdout": "internal installer output", "stderr": ""}

        self.composition.service.config._run_command = AsyncMock(side_effect=install_from_url)
        async with self.client() as client:
            response = await client.post("/agent-gateway/v1/agents-skills", json={"source": source})
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()["data"]
        self.assertEqual([item["skill_id"] for item in data], ["office-helper"])
        self.assertNotIn("internal installer output", response.text)
        command = self.composition.service.config._run_command.await_args.args[0]
        self.assertEqual(command[1:4], ["skills", "install", source])
        snapshots = list((self.root / "snapshots" / "skills").rglob("*.md"))
        self.assertEqual(len(snapshots), 1)

    async def test_default_skill_toggle_controls_which_skills_new_profiles_copy(self):
        for skill_id in ("enabled-skill", "disabled-skill"):
            skill = self.root / "skills" / skill_id / "SKILL.md"
            skill.parent.mkdir(parents=True, exist_ok=True)
            skill.write_text(
                f"---\nname: {skill_id}\ndescription: {skill_id}\n---\n",
                encoding="utf-8",
            )

        async with self.client() as client:
            toggled = await client.patch(
                "/agent-gateway/v1/agents-skills/disabled-skill",
                json={"enabled": False},
            )
            created = await client.post(
                "/agent-gateway/v1/agents",
                json={"display_name": "Enabled Skills Only"},
            )

        self.assertEqual(toggled.status_code, 200, toggled.text)
        states = {item["skill_id"]: item["enabled"] for item in toggled.json()["data"]}
        self.assertEqual(states, {"disabled-skill": False, "enabled-skill": True})
        self.assertEqual(created.status_code, 201, created.text)
        agent_id = created.json()["data"]["id"]
        profile_skills = self.profiles / agent_id / "skills"
        self.assertTrue((profile_skills / "enabled-skill" / "SKILL.md").is_file())
        self.assertFalse((profile_skills / "disabled-skill").exists())
        profile_config = yaml.safe_load((self.profiles / agent_id / "config.yaml").read_text(encoding="utf-8"))
        self.assertEqual(profile_config["skills"]["disabled"], [])
        self.assertEqual(len(list((self.root / "snapshots" / "config").glob("*.yaml"))), 1)

    async def test_profile_registry_uses_generated_ids_and_display_names(self):
        async with self.client() as client:
            created = await client.post("/agent-gateway/v1/agents", json={
                "display_name": "Research Lead",
                "description": "Coordinates research workflows.",
            })
        self.assertEqual(created.status_code, 201, created.text)
        profile = created.json()["data"]
        self.assertRegex(profile["id"], r"^[a-z][a-z0-9]{5}$")
        self.assertNotEqual(profile["id"], "Research Lead")
        self.assertEqual(profile["name"], "Research Lead")
        self.assertEqual(profile["display_name"], "Research Lead")
        self.assertNotIn("provider", profile["config"])
        self.assertNotIn("profile_path", profile["metadata"])
        self.assertNotIn("workspace_path", profile["metadata"])
        self.assertNotIn(str(self.profiles), created.text)
        self.assertEqual(profile["metadata"], {
            "display_name": "Research Lead",
            "description": "Coordinates research workflows.",
        })
        registry_path = self.root / "profiles.yaml"
        registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
        entries = {item["name"]: item for item in registry["profiles"]}
        self.assertEqual(set(entries[profile["id"]]), {"description", "name", "display_name", "updated_at"})
        self.assertEqual(entries[profile["id"]]["display_name"], "Research Lead")
        self.assertIn("default", entries)

        async with self.client() as client:
            updated = await client.patch(
                f"/agent-gateway/v1/agents/{profile['id']}/metadata",
                json={"display_name": "Research Director"},
            )
        self.assertEqual(updated.json()["data"]["display_name"], "Research Director")
        registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
        entries = {item["name"]: item for item in registry["profiles"]}
        self.assertEqual(entries[profile["id"]]["display_name"], "Research Director")

        entries[profile["id"]]["display_name"] = "Registry Name"
        registry_path.write_text(
            yaml.safe_dump({"profiles": list(entries.values())}, sort_keys=False),
            encoding="utf-8",
        )
        async with self.client() as client:
            listed = await client.get("/agent-gateway/v1/agents")
        listed_profile = next(item for item in listed.json()["data"] if item["id"] == profile["id"])
        self.assertEqual(listed_profile["display_name"], "Registry Name")

    async def test_write_approvals_default_on_and_allow_always_disables_the_selected_gate(self):
        async with self.client() as client:
            created = await client.post("/agent-gateway/v1/agents", json={"display_name": "Safe Writer"})
        self.assertEqual(created.status_code, 201, created.text)
        agent = created.json()["data"]
        agent_id = agent["id"]
        self.assertTrue(agent["config"]["skills_write_approval"])
        self.assertTrue(agent["config"]["memory_write_approval"])

        config_path = self.profiles / agent_id / "config.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        self.assertTrue(config["skills"]["write_approval"])
        self.assertTrue(config["memory"]["write_approval"])

        async with self.client() as client:
            toggled = await client.patch(
                f"/agent-gateway/v1/agents-configs/{agent_id}",
                json={"skills_write_approval": False},
            )
        self.assertEqual(toggled.status_code, 200, toggled.text)
        self.assertFalse(toggled.json()["data"]["skills_write_approval"])
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        self.assertFalse(config["skills"]["write_approval"])
        self.assertTrue(config["memory"]["write_approval"])

        self.composition.service.agents.resolve_approval = unittest.mock.Mock(
            return_value={"run_id": "run_test", "choice": "always", "resolved": 1}
        )
        async with self.client() as client:
            allowed = await client.post(
                "/conversations/v1/conversations/conversation/runs/run_test/approval",
                params={"agent": agent_id},
                json={"choice": "always", "subsystem": "memory"},
            )
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertTrue(allowed.json()["data"]["write_approval_disabled"])
        self.composition.service.agents.resolve_approval.assert_called_once_with(
            "run_test", {"choice": "once", "subsystem": "memory"}
        )
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        self.assertFalse(config["memory"]["write_approval"])
        self.assertGreaterEqual(len(self.composition.service.repository.list_snapshots(agent_id, "config")), 2)

    async def test_agent_skill_memory_mcp_and_snapshots_are_profile_local(self):
        async with self.client() as client:
            created = await client.post("/agent-gateway/v1/agents", json={"name": "Researcher", "description": "test"})
        self.assertEqual(created.status_code, 201, created.text)
        agent_id = created.json()["data"]["id"]
        profile = self.profiles / agent_id

        async with self.client() as client:
            skill = await client.post(f"/agent-gateway/v1/agents-skills/{agent_id}", json={
                "skill_id": "notes", "content": "---\nname: notes\n---\n# Notes\n",
            })
            memory = await client.patch(f"/agent-gateway/v1/agents/{agent_id}/memory", json={"memory": "remember this"})
            mcp = await client.put(f"/agent-gateway/v1/agents-mcp/{agent_id}", json={"servers": {"docs": {"command": "docs-mcp"}}})
            snapshot_response = await client.get(f"/agent-gateway/v1/agents/{agent_id}/snapshots")
        self.assertEqual(skill.status_code, 201, skill.text)
        self.assertEqual(memory.status_code, 200, memory.text)
        self.assertEqual(mcp.status_code, 200, mcp.text)

        self.assertTrue((profile / "skills" / "notes" / "SKILL.md").is_file())
        self.assertTrue((profile / "mcp.json").is_file())
        snapshots = snapshot_response.json()["data"]
        self.assertEqual({item["kind"] for item in snapshots}, {"skills", "memory"})
        self.assertFalse((self.root / "skills" / "notes" / "SKILL.md").exists())

    async def test_default_and_agent_skill_catalogs_stay_profile_scoped(self):
        default_skill = self.root / "skills" / "office" / "default-notes" / "SKILL.md"
        default_skill.parent.mkdir(parents=True)
        default_skill.write_text(
            "---\nname: default-notes\ndescription: Default profile notes\n---\n",
            encoding="utf-8",
        )
        async with self.client() as client:
            created = await client.post("/agent-gateway/v1/agents", json={"name": "Scoped"})
        agent_id = created.json()["data"]["id"]
        agent_skill = self.profiles / agent_id / "skills" / "custom" / "agent-only" / "SKILL.md"
        agent_skill.parent.mkdir(parents=True)
        agent_skill.write_text(
            "---\nname: agent-only\ndescription: Custom profile only\n---\n",
            encoding="utf-8",
        )

        async with self.client() as client:
            default_response = await client.get("/agent-gateway/v1/agents-skills")
            agent_response = await client.get(f"/agent-gateway/v1/agents-skills/{agent_id}")

        self.assertEqual(default_response.status_code, 200, default_response.text)
        self.assertEqual(agent_response.status_code, 200, agent_response.text)
        default_skills = default_response.json()["data"]
        agent_skills = agent_response.json()["data"]
        self.assertEqual([item["skill_id"] for item in default_skills], ["default-notes"])
        self.assertEqual(default_skills[0]["category"], "office")
        self.assertEqual(
            {item["skill_id"] for item in agent_skills},
            {"default-notes", "agent-only"},
        )
        self.assertEqual(
            next(item for item in agent_skills if item["skill_id"] == "agent-only")["category"],
            "custom",
        )

    async def test_team_files_and_cron_kanban_database_persist(self):
        ids = []
        async with self.client() as client:
            for title in ("Lead", "Worker"):
                result = await client.post("/agent-gateway/v1/agents", json={"name": title})
                ids.append(result.json()["data"]["id"])
            team = await client.post("/api/v1/teams", json={
                "name": "Research", "orchestrator_id": ids[0],
                "members": [{"agent_id": ids[1], "role": "researcher", "allowed_tools": ["web"]}],
            })
            cron = await client.post("/agent-gateway/v1/cron/jobs", json={
                "agent_id": ids[0], "name": "Digest", "prompt": "Summarize", "interval_minutes": 60,
            })
            teams = await client.get("/api/v1/teams")
            crons = await client.get("/agent-gateway/v1/cron/jobs")
        self.assertEqual(team.status_code, 201, team.text)
        self.assertEqual(cron.status_code, 201, cron.text)
        self.assertEqual(len(teams.json()["data"]), 1)
        self.assertEqual(len(crons.json()["data"]), 1)

    async def test_team_workflow_runs_dependency_dag_and_synthesizes(self):
        ids = []
        async with self.client() as client:
            for display_name in ("Coordinator", "Researcher", "Reviewer"):
                response = await client.post("/agent-gateway/v1/agents", json={"display_name": display_name})
                ids.append(response.json()["data"]["id"])
            team_response = await client.post("/api/v1/teams", json={
                "name": "DAG team",
                "orchestrator_id": ids[0],
                "members": [
                    {"agent_id": ids[1], "role": "researcher", "allowed_tools": ["web"]},
                    {"agent_id": ids[2], "role": "reviewer", "allowed_tools": ["web"]},
                ],
                "workflow": [
                    {"id": "research", "task": "Research the API", "role": "researcher"},
                    {"id": "review", "task": "Review the findings", "role": "reviewer", "needs": ["research"]},
                ],
                "max_parallel": 2,
            })
        team_id = team_response.json()["data"]["id"]
        self.assertEqual(
            [step["id"] for step in team_response.json()["data"]["workflow"]],
            ["research", "review"],
        )
        prompts: list[tuple[str, str]] = []

        async def fake_chat(agent_id, body):
            prompts.append((agent_id, body["message"]))
            if agent_id == ids[0]:
                return {"response": "final synthesis"}
            if agent_id == ids[1]:
                return {"response": "research result"}
            return {"response": "review result"}

        self.composition.service.agents.chat = AsyncMock(side_effect=fake_chat)
        async with self.client() as client:
            response = await client.post(f"/api/v1/teams/{team_id}/run", json={
                "synthesis": "Produce the final answer.",
            })
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["data"]
        self.assertEqual([item["id"] for item in result["workflow_results"]], ["research", "review"])
        self.assertEqual(result["orchestrator_summary"], "final synthesis")
        reviewer_prompt = next(prompt for agent_id, prompt in prompts if agent_id == ids[2])
        self.assertIn("[research] research result", reviewer_prompt)
        self.assertEqual(prompts[-1][0], ids[0])

        async with self.client() as client:
            cycle = await client.post(f"/api/v1/teams/{team_id}/run", json={
                "workflow": [
                    {"id": "a", "task": "A", "needs": ["b"]},
                    {"id": "b", "task": "B", "needs": ["a"]},
                ],
            })
        self.assertEqual(cycle.status_code, 400, cycle.text)
        self.assertEqual(cycle.json()["error"]["code"], "workflow_cycle")

    async def test_due_profile_cron_executes_in_the_unified_process(self):
        from brain4all.integrations import kanban as kanban_adapter
        from hermes_cli import kanban_db

        async with self.client() as client:
            created = await client.post("/agent-gateway/v1/agents", json={"name": "Scheduler"})
            agent_id = created.json()["data"]["id"]
            response = await client.post("/agent-gateway/v1/cron/jobs", json={
                "agent_id": agent_id, "name": "Due", "prompt": "Run", "interval_minutes": 60,
            })
        job = response.json()["data"]
        with kanban_adapter.connection("default") as conn:
            conn.execute(
                "UPDATE brain4all_task_schedules SET next_run_at = 1 "
                "WHERE task_id = ?",
                (job["id"],),
            )
            released = kanban_adapter.release_due_schedules(
                conn,
                board="default",
                now=2,
            )
            tasks = kanban_db.list_tasks(conn, include_archived=True)
            schedule = kanban_adapter.task_schedule(conn, job["id"])
        self.assertEqual(len(released), 1)
        self.assertEqual(schedule["occurrence_count"], 1)
        self.assertTrue(any(
            str(task.idempotency_key or "").startswith(f"schedule:{job['id']}:")
            for task in tasks
        ))

    async def test_swagger_documents_typed_management_and_stream_requests(self):
        schema = self.app.openapi()
        self.assertEqual(
            schema["paths"]["/agent-gateway/v1/agents"]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/AgentCreate",
        )
        self.assertEqual(
            schema["paths"]["/conversations/v1/conversations/{conversation_id}/chat/stream"]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/ChatRequest",
        )

    async def test_bundle_round_trip_is_checked_and_excludes_credentials(self):
        async with self.client() as client:
            created = await client.post("/agent-gateway/v1/agents", json={"name": "Portable"})
            agent_id = created.json()["data"]["id"]
            (self.profiles / agent_id / ".env").write_text("SECRET=never-export\n", encoding="utf-8")
            exported = await client.post("/api/v1/bundles/export", json={"agent_ids": [agent_id]})
        self.assertEqual(exported.status_code, 200, exported.text)
        with ZipFile(BytesIO(exported.content)) as archive:
            self.assertNotIn(f"profiles/{agent_id}/.env", archive.namelist())

        self.assertEqual(exported.headers["content-type"], "application/zip")
        self.assertIn('.zip"', exported.headers["content-disposition"])
        upload = {"file": ("profile.zip", exported.content, "application/zip")}
        async with self.client() as client:
            inspected = await client.post("/api/v1/bundles/inspect", files=upload)
            preview = await client.post("/api/v1/bundles/dry-run", files=upload)
            applied = await client.post("/api/v1/bundles/apply", files=upload)
        self.assertEqual(inspected.status_code, 200, inspected.text)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()["data"]["collisions"], [agent_id])
        self.assertEqual(applied.status_code, 201, applied.text)
        imported_id = applied.json()["data"]["agent_id_mappings"][agent_id]
        self.assertNotEqual(imported_id, agent_id)
        self.assertTrue((self.profiles / imported_id / "config.yaml").is_file())

    async def test_team_snapshot_exports_profiles_and_remaps_the_complete_workflow(self):
        (self.root / "auth.json").write_text(
            json.dumps({"version": 1, "session": "team-snapshot-secret"}, indent=2) + "\n",
            encoding="utf-8",
        )
        async with self.client() as client:
            agent_ids = []
            for name in ("Snapshot coordinator", "Snapshot researcher", "Snapshot reviewer"):
                created = await client.post(
                    "/agent-gateway/v1/agents",
                    json={"display_name": name},
                )
                agent_ids.append(created.json()["data"]["id"])
            (self.profiles / agent_ids[0] / "workspace" / "secret.txt").write_text(
                "team-snapshot-secret\n",
                encoding="utf-8",
            )
            created_team = await client.post("/api/v1/teams", json={
                "name": "Portable Team",
                "description": "A complete portable workflow.",
                "orchestrator_id": agent_ids[0],
                "members": [
                    {"agent_id": agent_ids[1], "role": "researcher"},
                    {"agent_id": agent_ids[2], "role": "reviewer"},
                ],
                "workflow": [
                    {"id": "research", "task": "Research.", "agent_id": agent_ids[1], "role": "researcher"},
                    {"id": "review", "task": "Review.", "agent_id": agent_ids[2], "role": "reviewer", "needs": ["research"]},
                ],
            })
            team_id = created_team.json()["data"]["id"]
            exported = await client.post(
                "/api/v1/bundles/export",
                json={"team_ids": [team_id]},
            )
        self.assertEqual(exported.status_code, 200, exported.text)
        with ZipFile(BytesIO(exported.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["version"], 1)
            self.assertNotIn(b"team-snapshot-secret", archive.read("manifest.json"))
            self.assertNotIn(
                b"team-snapshot-secret",
                archive.read(f"profiles/{agent_ids[0]}/workspace/secret.txt"),
            )
            self.assertEqual([team["id"] for team in manifest["teams"]], [team_id])
            self.assertEqual({agent["id"] for agent in manifest["agents"]}, set(agent_ids))
            self.assertIn(f"teams/{team_id}.yaml", archive.namelist())

        upload = {"file": ("team.zip", exported.content, "application/zip")}
        async with self.client() as client:
            applied = await client.post("/api/v1/bundles/apply", files=upload)
            self.assertEqual(applied.status_code, 201, applied.text)
            report = applied.json()["data"]
            imported_team_id = report["team_id_mappings"][team_id]
            imported = await client.get(f"/api/v1/teams/{imported_team_id}")
        self.assertEqual(imported.status_code, 200, imported.text)
        team = imported.json()["data"]
        mappings = report["agent_id_mappings"]
        self.assertEqual(team["orchestrator_id"], mappings[agent_ids[0]])
        self.assertEqual(
            [member["agent_id"] for member in team["members"]],
            [mappings[agent_ids[1]], mappings[agent_ids[2]]],
        )
        self.assertEqual(
            [step["agent_id"] for step in team["workflow"]],
            [mappings[agent_ids[1]], mappings[agent_ids[2]]],
        )

    async def test_profile_bundle_chunk_transfer_redacts_and_inherits_default_credentials(self):
        (self.root / ".env").write_text("DEFAULT_SECRET=from-default\n", encoding="utf-8")
        (self.root / "auth.json").write_text('{"session":"default-auth"}\n', encoding="utf-8")
        async with self.client() as client:
            created = await client.post("/agent-gateway/v1/agents", json={"display_name": "Chunked profile"})
        agent_id = created.json()["data"]["id"]
        profile = self.profiles / agent_id
        (profile / ".env").write_text("SOURCE_TOKEN=never-export-this\n", encoding="utf-8")
        (profile / "workspace" / "secret.txt").write_text("token=never-export-this\n", encoding="utf-8")
        (profile / "workspace" / "large.bin").write_bytes(os.urandom(4 * 1024 * 1024 + 256))
        config = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))
        config["providers"] = {"private": {"api_key": "never-export-this", "key_env": "MISSING_API_KEY"}}
        (profile / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

        async with self.client() as client:
            started = await client.post("/api/v1/bundles/exports", json={"agent_ids": [agent_id]})
            transfer = started.json()["data"]
            self.assertTrue(transfer["filename"].endswith(".zip"))
            parts = []
            for number in range(transfer["total_parts"]):
                response = await client.get(f"/api/v1/bundles/exports/{transfer['export_id']}/parts/{number}")
                self.assertEqual(response.status_code, 200, response.text)
                parts.append(response.content)
        self.assertGreater(transfer["total_parts"], 1)
        bundle = b"".join(parts)
        self.assertEqual(len(bundle), transfer["size"])
        self.assertEqual(__import__("hashlib").sha256(bundle).hexdigest(), transfer["sha256"])
        with ZipFile(BytesIO(bundle)) as archive:
            names = archive.namelist()
            self.assertNotIn(f"profiles/{agent_id}/.env", names)
            self.assertNotIn(b"never-export-this", archive.read(f"profiles/{agent_id}/workspace/secret.txt"))
            exported_config = yaml.safe_load(archive.read(f"profiles/{agent_id}/config.yaml"))
            self.assertNotIn("api_key", exported_config.get("providers", {}).get("private", {}))

        async with self.client() as client:
            upload_started = await client.post("/api/v1/bundles/uploads", json={"filename": "profile.zip", "size": len(bundle)})
            upload = upload_started.json()["data"]
            for number in range(upload["total_parts"]):
                chunk = bundle[number * upload["chunk_size"]:(number + 1) * upload["chunk_size"]]
                response = await client.put(
                    f"/api/v1/bundles/uploads/{upload['upload_id']}/parts/{number}",
                    content=chunk,
                    headers={"Content-Type": "application/octet-stream"},
                )
                self.assertEqual(response.status_code, 201, response.text)
            completed = await client.post(f"/api/v1/bundles/uploads/{upload['upload_id']}/complete", json={})
            self.assertEqual(completed.status_code, 200, completed.text)
            self.assertIn("MISSING_API_KEY", completed.json()["data"]["preview"]["missing_environment"])
            applied = await client.post(
                f"/api/v1/bundles/uploads/{upload['upload_id']}/apply",
                json={"environment": {"MISSING_API_KEY": "server-specific-value"}},
            )
        self.assertEqual(applied.status_code, 201, applied.text)
        imported_id = applied.json()["data"]["agent_id_mappings"][agent_id]
        imported = self.profiles / imported_id
        imported_env = (imported / ".env").read_text(encoding="utf-8")
        self.assertIn("DEFAULT_SECRET=from-default", imported_env)
        self.assertIn("MISSING_API_KEY=server-specific-value", imported_env)
        self.assertNotIn("never-export-this", imported_env)
        self.assertEqual(json.loads((imported / "auth.json").read_text())["session"], "default-auth")

    async def test_missing_run_control_has_a_stable_response(self):
        async with self.client() as client:
            stopped = await client.post("/conversations/v1/conversations/c/runs/run_00000000000000000000000000000000/stop?agent=a")
        self.assertEqual(stopped.status_code, 404)
        self.assertEqual(stopped.json()["error"]["code"], "run_not_found")

    async def test_chat_stream_rejects_a_conversation_that_is_not_opened(self):
        missing_id = "20260727_092138_26112b"
        async with self.client() as client:
            created = await client.post(
                "/agent-gateway/v1/agents",
                json={"display_name": "Session worker"},
            )
            agent_id = created.json()["data"]["id"]
            streamed = await client.post(
                f"/conversations/v1/conversations/{missing_id}/chat/stream?agent={agent_id}",
                json={"input": "Do not create a conversation for this message."},
            )
            conversations = await client.get(
                f"/conversations/v1/conversations?agent={agent_id}",
            )

        self.assertEqual(streamed.status_code, 200)
        self.assertIn("event: error", streamed.text)
        self.assertIn(f"Conversation not found: {missing_id}", streamed.text)
        self.assertNotIn(
            missing_id,
            [item["id"] for item in conversations.json()["data"]["conversations"]],
        )

    async def test_default_conversation_names_are_numbered_by_the_backend(self):
        async with self.client() as client:
            created = await client.post(
                "/agent-gateway/v1/agents",
                json={"display_name": "Conversation numbering"},
            )
            agent_id = created.json()["data"]["id"]
            first = await client.post(
                f"/conversations/v1/conversations?agent={agent_id}",
                json={},
            )
            second = await client.post(
                f"/conversations/v1/conversations?agent={agent_id}",
                json={},
            )
            third = await client.post(
                f"/conversations/v1/conversations?agent={agent_id}",
                json={"title": "New Conversation"},
            )

        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual(third.status_code, 201, third.text)
        self.assertEqual(first.json()["data"]["title"], "New Conversation")
        self.assertEqual(second.json()["data"]["title"], "New Conversation 2")
        self.assertEqual(third.json()["data"]["title"], "New Conversation 3")

    async def test_conversation_usage_is_aggregated_from_the_hermes_session(self):
        async with self.client() as client:
            created = await client.post(
                "/agent-gateway/v1/agents",
                json={"display_name": "Usage worker"},
            )
            agent_id = created.json()["data"]["id"]
            conversation = await client.post(
                f"/conversations/v1/conversations?agent={agent_id}",
                json={"title": "Usage test"},
            )
            conversation_id = conversation.json()["data"]["id"]

            database = self.profiles / agent_id / "state.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """
                    UPDATE sessions
                    SET model = ?, started_at = ?, ended_at = ?, message_count = ?,
                        tool_call_count = ?, input_tokens = ?, output_tokens = ?,
                        cache_read_tokens = ?, cache_write_tokens = ?,
                        reasoning_tokens = ?, api_call_count = ?,
                        estimated_cost_usd = ?, cost_status = ?, model_config = ?
                    WHERE id = ?
                    """,
                    (
                        "test/model", 100.0, 103.25, 3, 1, 1_000, 200,
                        100, 20, 50, 2, 0.25, "estimated",
                        json.dumps({"brain4all_context": {"used": 10_000, "limit": 200_000}}),
                        conversation_id,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO messages
                        (session_id, role, content, tool_call_id, tool_name, timestamp, token_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (conversation_id, "user", "question", None, None, 100.0, 10),
                        (conversation_id, "assistant", "", None, None, 101.0, 20),
                        (conversation_id, "tool", "result", "call-1", "search", 102.0, 30),
                    ],
                )
                connection.commit()
            finally:
                connection.close()

            response = await client.get(
                f"/conversations/v1/conversations/{conversation_id}/usage?agent={agent_id}",
            )

        self.assertEqual(response.status_code, 200, response.text)
        usage = response.json()["data"]
        self.assertEqual(usage["tokens"], {
            "cache_read": 100,
            "cache_write": 20,
            "input": 1_000,
            "output": 200,
            "reasoning": 50,
            "total": 1_320,
        })
        self.assertEqual(usage["steps"], 1)
        self.assertEqual(usage["tool_calls"], 1)
        self.assertEqual(usage["messages"], 3)
        self.assertEqual(usage["execution_seconds"], 3.25)
        self.assertEqual(usage["api_calls"], 2)
        self.assertEqual(usage["cost"]["total_usd"], 0.25)
        self.assertEqual(usage["context"], {
            "used": 10_000,
            "limit": 200_000,
            "percent": 5.0,
        })


if __name__ == "__main__":
    unittest.main()
