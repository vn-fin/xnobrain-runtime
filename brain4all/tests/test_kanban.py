"""Real Hermes-backed Kanban API contract tests."""

from __future__ import annotations

import os
from pathlib import Path
import re
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
            "scheduled": "todo",
            "ready": "running",
            "running": "running",
            "review": "running",
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
        self.profiles = profiles
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
            researcher = await client.post(
                "/agent-gateway/v1/agents",
                json={"name": "researcher"},
            )
            reviewer = await client.post(
                "/agent-gateway/v1/agents",
                json={"name": "reviewer"},
            )
            researcher_id = researcher.json()["data"]["id"]
            reviewer_id = reviewer.json()["data"]["id"]
            created = await client.post(
                "/agent-gateway/v1/kanban/boards/default/tasks",
                json={"title": "Assignable task", "description": "Assign this before it runs.", "status": "backlog"},
            )
            task_id = created.json()["data"]["id"]
            assigned = await client.post(
                f"/agent-gateway/v1/kanban/boards/default/tasks/{task_id}/assign",
                json={"assignee": researcher_id},
            )
            self.assertEqual(assigned.status_code, 200, assigned.text)
            self.assertEqual(assigned.json()["data"]["assignees"], [researcher_id])
            self.assertEqual(assigned.json()["data"]["workspace_kind"], "dir")
            self.assertIsNone(assigned.json()["data"]["workspace_path"])

            await client.post(
                f"/agent-gateway/v1/kanban/boards/default/tasks/{task_id}/move",
                json={"status": "todo"},
            )
            reassigned = await client.post(
                f"/agent-gateway/v1/kanban/boards/default/tasks/{task_id}/assign",
                json={"assignee": reviewer_id},
            )
            self.assertEqual(reassigned.status_code, 200, reassigned.text)
            self.assertEqual(reassigned.json()["data"]["assignees"], [reviewer_id])
            from brain4all.integrations import kanban as kanban_adapter
            from hermes_cli import kanban_db
            with kanban_adapter.connection("default") as conn:
                native_task = kanban_db.get_task(conn, task_id)
            self.assertEqual(
                Path(native_task.workspace_path),
                self.profiles / reviewer_id / "workspace",
            )

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
                json={"title": "Event task", "description": "Generate a safe event.", "status": "backlog"},
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
            first = await client.post("/agent-gateway/v1/kanban/boards/default/tasks", json={"title": "Parent", "description": "Finish the parent.", "status": "todo"})
            second = await client.post("/agent-gateway/v1/kanban/boards/default/tasks", json={"title": "Child", "description": "Wait for the parent.", "status": "todo"})
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

    async def test_pre_run_edit_skills_comments_activity_and_conversation_link(self):
        from hermes_cli import kanban_db

        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            missing_brief = await client.post(
                "/agent-gateway/v1/kanban/boards/default/tasks",
                json={"title": "Missing worker brief", "status": "backlog"},
            )
            self.assertEqual(missing_brief.status_code, 422, missing_brief.text)
            agent = await client.post("/agent-gateway/v1/agents", json={"name": "Brief writer"})
            self.assertEqual(agent.status_code, 201, agent.text)
            agent_id = agent.json()["data"]["id"]
            created = await client.post(
                "/agent-gateway/v1/kanban/boards/default/tasks",
                json={
                    "title": "Draft brief",
                    "description": "Create the first draft.",
                    "status": "backlog",
                    "assignee": agent_id,
                    "skills": ["writing"],
                },
            )
            self.assertEqual(created.status_code, 201, created.text)
            task_id = created.json()["data"]["id"]
            self.assertEqual(created.json()["data"]["workspace_kind"], "dir")

            updated = await client.patch(
                f"/agent-gateway/v1/kanban/boards/default/tasks/{task_id}",
                json={
                    "title": "Draft launch brief",
                    "description": "Create a concise launch brief with sources.",
                    "priority": "high",
                    "skills": ["writing", "web-research"],
                },
            )
            self.assertEqual(updated.status_code, 200, updated.text)
            task = updated.json()["data"]
            self.assertEqual(task["status"], "triage")
            self.assertEqual(task["title"], "Draft launch brief")
            self.assertEqual(task["skills"], ["writing", "web-research"])
            self.assertEqual(task["allowed_kanban_statuses"], ["todo", "archived"])
            self.assertTrue(any(event["kind"] == "edited" for event in task["events"]))

            comment = await client.post(
                f"/agent-gateway/v1/kanban/boards/default/tasks/{task_id}/comments",
                json={"body": "Use the approved brand voice."},
            )
            self.assertEqual(comment.status_code, 201, comment.text)

            log_path = kanban_db.worker_log_path(task_id, board="default")
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(
                "Query: private prompt must not escape\n"
                "  ┊ ⚡ browser_navigate  5.2s\n"
                "secret tool output must not escape\n"
                "Session:        20260724_102648_3e2c61\n",
                encoding="utf-8",
            )
            detail = await client.get(
                f"/agent-gateway/v1/kanban/boards/default/tasks/{task_id}",
            )
            self.assertEqual(detail.status_code, 200, detail.text)
            payload = detail.json()["data"]
            self.assertEqual(payload["comments"][0]["body"], "Use the approved brand voice.")
            self.assertEqual(payload["worker_activity"]["entries"][0]["name"], "browser_navigate")
            self.assertEqual(payload["conversation"]["id"], "20260724_102648_3e2c61")
            self.assertTrue(payload["conversation"]["url"].endswith("/20260724_102648_3e2c61"))
            self.assertNotIn("private prompt", detail.text)
            self.assertNotIn("secret tool output", detail.text)

    async def test_api_created_conversations_use_the_native_session_id_shape(self):
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            agent = await client.post("/agent-gateway/v1/agents", json={"name": "Session worker"})
            agent_id = agent.json()["data"]["id"]
            conversation = await client.post(
                f"/conversations/v1/conversations?agent={agent_id}",
                json={"title": "Manual conversation"},
            )
            self.assertEqual(conversation.status_code, 201, conversation.text)
            conversation_id = conversation.json()["data"]["id"]
            self.assertIsNotNone(re.fullmatch(r"\d{8}_\d{6}_[0-9a-f]{6}", conversation_id))
