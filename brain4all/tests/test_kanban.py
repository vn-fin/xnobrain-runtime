"""Real Hermes-backed Kanban API contract tests."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from brain4all.app import Brain4AllApplication
from brain4all.integrations import AgentManager, GlobalConfigManager
from brain4all.services.kanban import _status

try:
    import hermes_cli.kanban_db  # type: ignore  # noqa: F401
except Exception:  # pragma: no cover - the source checkout lacks Hermes
    HERMES_AVAILABLE = False
else:
    HERMES_AVAILABLE = True


class _Router:
    async def list_connections(self):
        return {"connections": []}

    async def list_models(self):
        return {"data": []}


class KanbanProjectionTests(unittest.TestCase):
    def test_native_states_map_to_the_five_product_columns(self):
        expected = {
            "triage": "backlog",
            "todo": "todo",
            "ready": "running",
            "running": "running",
            "blocked": "done",
            "done": "done",
            "archived": "archived",
        }
        self.assertEqual({native: _status(native) for native in expected}, expected)


@unittest.skipUnless(HERMES_AVAILABLE, "Hermes runtime is not installed")
class HermesKanbanAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name) / "root"
        profiles = Path(self.temp.name) / "profiles"
        root.mkdir()
        profiles.mkdir()
        self.env = patch.dict(os.environ, {
            "HERMES_HOME": str(root),
            "HERMES_ROOT_PROFILE": str(root),
            "HERMES_PROFILES_ROOT": str(profiles),
            "HERMES_KANBAN_HOME": str(root),
            "DATA_DIR": self.temp.name,
        })
        self.env.start()
        self.app = FastAPI()
        self.composition = Brain4AllApplication(
            AgentManager(root_profile=root, profiles_root=profiles, legacy_agents_root=Path(self.temp.name) / "legacy"),
            GlobalConfigManager(root_profile=root),
            _Router(),
        )
        self.composition.register(self.app)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    async def test_real_sqlite_task_lifecycle_and_projection(self):
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            boards = await client.get("/agent-gateway/v1/kanban/boards")
            self.assertEqual(boards.status_code, 200, boards.text)
            self.assertEqual(boards.json()["data"][0]["id"], "default")

            created = await client.post("/agent-gateway/v1/kanban/boards/default/tasks", json={
                "title": "Persisted task",
                "description": "Stored in Hermes SQLite",
                "status": "todo",
                "priority": "high",
            })
            self.assertEqual(created.status_code, 201, created.text)
            task_id = created.json()["data"]["id"]
            self.assertEqual(len(task_id), 10)
            self.assertEqual(created.json()["data"]["status"], "ready")
            self.assertEqual(created.json()["data"]["kanban_status"], "running")

            comment = await client.post(
                f"/agent-gateway/v1/kanban/boards/default/tasks/{task_id}/comments",
                json={"body": "Real comment"},
            )
            self.assertEqual(comment.status_code, 201, comment.text)

            running = await client.post(
                f"/agent-gateway/v1/kanban/boards/default/tasks/{task_id}/move",
                json={"status": "running"},
            )
            self.assertEqual(running.status_code, 200, running.text)
            self.assertEqual(running.json()["data"]["status"], "running")
            self.assertEqual(running.json()["data"]["kanban_status"], "running")

            done = await client.post(
                f"/agent-gateway/v1/kanban/boards/default/tasks/{task_id}/move",
                json={"status": "done"},
            )
            self.assertEqual(done.status_code, 200, done.text)
            self.assertEqual(done.json()["data"]["status"], "done")
            self.assertEqual(done.json()["data"]["kanban_status"], "done")

            archived = await client.post(
                f"/agent-gateway/v1/kanban/boards/default/tasks/{task_id}/archive",
            )
            self.assertEqual(archived.status_code, 200, archived.text)
            self.assertTrue(archived.json()["data"]["archived"])
            self.assertEqual(archived.json()["data"]["status"], "archived")
            self.assertEqual(archived.json()["data"]["kanban_status"], "archived")

            visible = await client.get("/agent-gateway/v1/kanban/boards/default/tasks")
            self.assertEqual(visible.json()["data"]["tasks"], [])
            all_tasks = await client.get("/agent-gateway/v1/kanban/boards/default/tasks?include_archived=true")
            self.assertEqual(len(all_tasks.json()["data"]["tasks"]), 1)
            boards_with_archive = await client.get("/agent-gateway/v1/kanban/boards?include_archived=true")
            self.assertEqual(boards_with_archive.json()["data"][0]["tasks"][0]["kanban_status"], "archived")

    async def test_compatibility_cron_creation_is_visible_on_default_board(self):
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            agent = await client.post("/agent-gateway/v1/agents", json={"name": "Scheduled worker"})
            self.assertEqual(agent.status_code, 201, agent.text)
            agent_id = agent.json()["data"]["id"]
            cron = await client.post("/agent-gateway/v1/cron/jobs", json={
                "agent_id": agent_id,
                "name": "Morning review",
                "prompt": "Review the inbox",
                "interval_minutes": 60,
            })
            self.assertEqual(cron.status_code, 201, cron.text)
            job = cron.json()["data"]
            self.assertEqual(job["kanban_board"], "default")
            self.assertTrue(job["kanban_task_id"])
            tasks = await client.get("/agent-gateway/v1/kanban/boards/default/tasks")
            self.assertEqual(tasks.status_code, 200, tasks.text)
            self.assertEqual(tasks.json()["data"]["tasks"][0]["title"], "Morning review")
            self.assertEqual(tasks.json()["data"]["tasks"][0]["status"], "triage")
            self.assertEqual(tasks.json()["data"]["tasks"][0]["kanban_status"], "backlog")

    async def test_pre_run_assignment_and_clean_transition_conflict(self):
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            created = await client.post(
                "/agent-gateway/v1/kanban/boards/default/tasks",
                json={"title": "Assignable task", "status": "backlog"},
            )
            task_id = created.json()["data"]["id"]
            assigned = await client.post(
                f"/agent-gateway/v1/kanban/boards/default/tasks/{task_id}/assign",
                json={"assignee": "researcher"},
            )
            self.assertEqual(assigned.status_code, 200, assigned.text)
            self.assertEqual(assigned.json()["data"]["assignees"], ["researcher"])

            await client.post(
                f"/agent-gateway/v1/kanban/boards/default/tasks/{task_id}/move",
                json={"status": "todo"},
            )
            reassigned = await client.post(
                f"/agent-gateway/v1/kanban/boards/default/tasks/{task_id}/assign",
                json={"assignee": "reviewer"},
            )
            self.assertEqual(reassigned.status_code, 200, reassigned.text)
            self.assertEqual(reassigned.json()["data"]["assignees"], ["reviewer"])

            await client.post(
                f"/agent-gateway/v1/kanban/boards/default/tasks/{task_id}/move",
                json={"status": "running"},
            )
            conflict = await client.post(
                f"/agent-gateway/v1/kanban/boards/default/tasks/{task_id}/move",
                json={"status": "backlog"},
            )
            self.assertEqual(conflict.status_code, 409, conflict.text)
            self.assertEqual(conflict.json()["message"], "active tasks cannot be returned to Backlog")
            self.assertNotIn("Hermes", conflict.text)

            assignment_conflict = await client.post(
                f"/agent-gateway/v1/kanban/boards/default/tasks/{task_id}/assign",
                json={"assignee": "writer"},
            )
            self.assertEqual(assignment_conflict.status_code, 409, assignment_conflict.text)
            self.assertEqual(assignment_conflict.json()["message"], "A task can only be assigned before it starts")

    async def test_board_event_feed_uses_safe_public_shapes(self):
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            created = await client.post(
                "/agent-gateway/v1/kanban/boards/default/tasks",
                json={"title": "Event task", "status": "backlog"},
            )
            task_id = created.json()["data"]["id"]
            rows = self.composition.service.kanban.board_events("default", after_id=0)
            event = next(item for item in rows if item["task_id"] == task_id and item["kind"] == "created")
            self.assertEqual(event["status"], "triage")
            self.assertEqual(event["kanban_status"], "backlog")
            self.assertNotIn("body", event["payload"])

    async def test_board_metadata_never_exposes_hermes_database_path(self):
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            created = await client.post("/agent-gateway/v1/kanban/boards", json={"slug": "safe-board", "name": "Safe board"})
            self.assertEqual(created.status_code, 201, created.text)
            self.assertNotIn("db_path", created.json()["data"])
            updated = await client.patch("/agent-gateway/v1/kanban/boards/safe-board", json={"description": "No path"})
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertNotIn("db_path", updated.json()["data"])

    async def test_dependencies_events_and_pagination_use_safe_public_shapes(self):
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            first = await client.post("/agent-gateway/v1/kanban/boards/default/tasks", json={"title": "Parent", "status": "todo"})
            second = await client.post("/agent-gateway/v1/kanban/boards/default/tasks", json={"title": "Child", "status": "todo"})
            parent_id = first.json()["data"]["id"]
            child_id = second.json()["data"]["id"]
            linked = await client.post(f"/agent-gateway/v1/kanban/boards/default/tasks/{child_id}/links", json={"parent_id": parent_id, "child_id": child_id})
            self.assertEqual(linked.status_code, 201, linked.text)
            self.assertEqual(linked.json()["data"]["parent_id"], parent_id)
            unlinked = await client.request("DELETE", f"/agent-gateway/v1/kanban/boards/default/tasks/{child_id}/links", json={"parent_id": parent_id, "child_id": child_id})
            self.assertEqual(unlinked.status_code, 200, unlinked.text)
            page = await client.get("/agent-gateway/v1/kanban/boards/default/tasks?limit=1&offset=0")
            self.assertEqual(page.status_code, 200, page.text)
            self.assertEqual(page.json()["data"]["limit"], 1)
            events = await client.get(f"/agent-gateway/v1/kanban/boards/default/tasks/{child_id}/events")
            self.assertEqual(events.status_code, 200, events.text)
            self.assertNotIn("workspace_path", events.text)
