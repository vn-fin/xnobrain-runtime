"""Real Hermes-backed Kanban API contract tests."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from xnobrain.app import XNOBrainApplication
from xnobrain.integrations import AgentManager, GlobalConfigManager
from xnobrain.services.kanban import _status

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
    def test_native_states_map_to_product_columns(self):
        expected = {
            "triage": "backlog",
            "todo": "todo",
            "scheduled": "scheduled",
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
        self.composition = XNOBrainApplication(
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
            boards = await client.get("/xnobrain/api/runtime/v1/kanban/boards")
            self.assertEqual(boards.status_code, 200, boards.text)
            self.assertEqual(boards.json()["data"][0]["id"], "default")

            created = await client.post("/xnobrain/api/runtime/v1/kanban/boards/default/tasks", json={
                "title": "Persisted task",
                "description": "Stored in Hermes SQLite",
                "status": "todo",
                "priority": "high",
            })
            self.assertEqual(created.status_code, 201, created.text)
            task_id = created.json()["data"]["id"]
            self.assertEqual(len(task_id), 10)
            self.assertEqual(created.json()["data"]["status"], "todo")
            self.assertEqual(created.json()["data"]["kanban_status"], "todo")

            comment = await client.post(
                f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{task_id}/comments",
                json={"body": "Real comment"},
            )
            self.assertEqual(comment.status_code, 201, comment.text)

            running = await client.post(
                f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{task_id}/move",
                json={"status": "running"},
            )
            self.assertEqual(running.status_code, 200, running.text)
            self.assertEqual(running.json()["data"]["status"], "ready")
            self.assertEqual(running.json()["data"]["kanban_status"], "running")

            done = await client.post(
                f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{task_id}/move",
                json={"status": "done"},
            )
            self.assertEqual(done.status_code, 200, done.text)
            self.assertEqual(done.json()["data"]["status"], "done")
            self.assertEqual(done.json()["data"]["kanban_status"], "done")

            archived = await client.post(
                f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{task_id}/archive",
            )
            self.assertEqual(archived.status_code, 200, archived.text)
            self.assertTrue(archived.json()["data"]["archived"])
            self.assertEqual(archived.json()["data"]["status"], "archived")
            self.assertEqual(archived.json()["data"]["kanban_status"], "archived")

            visible = await client.get("/xnobrain/api/runtime/v1/kanban/boards/default/tasks")
            self.assertEqual(visible.json()["data"]["tasks"], [])
            all_tasks = await client.get("/xnobrain/api/runtime/v1/kanban/boards/default/tasks?include_archived=true")
            self.assertEqual(len(all_tasks.json()["data"]["tasks"]), 1)
            boards_with_archive = await client.get("/xnobrain/api/runtime/v1/kanban/boards?include_archived=true")
            self.assertNotIn("tasks", boards_with_archive.json()["data"][0])
            stats = await client.get("/xnobrain/api/runtime/v1/kanban/boards/default/stats")
            self.assertEqual(stats.json()["data"]["archived"], 1)
            self.assertEqual(stats.json()["data"]["total"], 1)

    async def test_saved_team_expands_to_grouped_native_dag_and_can_cancel(self):
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            agent_ids = []
            for name in ("Coordinator", "Researcher", "Reviewer"):
                response = await client.post(
                    "/xnobrain/api/runtime/v1/agents",
                    json={"display_name": name},
                )
                self.assertEqual(response.status_code, 201, response.text)
                agent_ids.append(response.json()["data"]["id"])
            team = await client.post("/xnobrain/api/runtime/v1/teams", json={
                "name": "Launch team",
                "orchestrator_id": agent_ids[0],
                "members": [
                    {"agent_id": agent_ids[1], "role": "researcher", "allowed_tools": ["web"]},
                    {"agent_id": agent_ids[2], "role": "reviewer", "allowed_tools": ["web"]},
                ],
                "workflow": [
                    {"id": "review", "task": "Review the research", "role": "reviewer", "needs": ["research"]},
                    {"id": "research", "task": "Research the launch", "role": "researcher", "skills": ["news-research"]},
                ],
                "coordinator_prompt": "Plan the stages before work begins.",
                "coordinator_skills": ["team-planning"],
                "synthesis_agent_id": agent_ids[2],
                "synthesis_skills": ["final-writing"],
                "synthesis_instruction": "Create the final cited launch plan.",
            })
            self.assertEqual(team.status_code, 201, team.text)
            self.assertEqual(
                team.json()["data"]["description"],
                "A coordinated team of 3 agents for multi-stage work.",
            )
            team_id = team.json()["data"]["id"]

            created = await client.post(
                "/xnobrain/api/runtime/v1/kanban/boards/default/tasks",
                json={
                    "title": "Prepare launch",
                    "description": "Produce an evidence-backed launch plan.",
                    "status": "todo",
                    "team_id": team_id,
                },
            )
            self.assertEqual(created.status_code, 201, created.text)
            task = created.json()["data"]
            self.assertEqual(task["team"]["name"], "Launch team")
            self.assertEqual(
                [node["step_id"] for node in task["team"]["nodes"]],
                ["__coordination__", "research", "review", "__synthesis__"],
            )
            self.assertEqual(task["team"]["nodes"][1]["needs"], ["__coordination__"])
            self.assertEqual(task["team"]["nodes"][2]["needs"], ["research"])
            self.assertEqual(task["team"]["nodes"][-1]["agent_id"], agent_ids[2])
            self.assertEqual(task["assignees"], [agent_ids[0], *agent_ids[1:]])
            root_id = task["id"]

            listed = await client.get("/xnobrain/api/runtime/v1/kanban/boards/default/tasks")
            self.assertEqual(listed.status_code, 200, listed.text)
            self.assertEqual([item["id"] for item in listed.json()["data"]["tasks"]], [root_id])

            cancelled = await client.post(
                f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{root_id}/team/cancel",
            )
            self.assertEqual(cancelled.status_code, 200, cancelled.text)
            self.assertTrue(cancelled.json()["data"]["team"]["cancelled"])
            self.assertEqual(cancelled.json()["data"]["team"]["status"], "cancelled")
            self.assertEqual(cancelled.json()["data"]["summary"], "Team run cancelled")

            archived = await client.post(
                f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{root_id}/archive",
            )
            self.assertEqual(archived.status_code, 200, archived.text)
            self.assertEqual(archived.json()["data"]["kanban_status"], "archived")
            self.assertEqual(archived.json()["data"]["team"]["status"], "archived")
            visible = await client.get("/xnobrain/api/runtime/v1/kanban/boards/default/tasks")
            self.assertEqual(visible.json()["data"]["tasks"], [])
            history = await client.get(
                "/xnobrain/api/runtime/v1/kanban/boards/default/tasks?include_archived=true",
            )
            self.assertEqual([item["id"] for item in history.json()["data"]["tasks"]], [root_id])

    async def test_compatibility_cron_creation_is_visible_on_default_board(self):
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            agent = await client.post("/xnobrain/api/runtime/v1/agents", json={"name": "Scheduled worker"})
            self.assertEqual(agent.status_code, 201, agent.text)
            agent_id = agent.json()["data"]["id"]
            cron = await client.post("/xnobrain/api/runtime/v1/cron/jobs", json={
                "agent_id": agent_id,
                "name": "Morning review",
                "prompt": "Review the inbox",
                "interval_minutes": 60,
            })
            self.assertEqual(cron.status_code, 201, cron.text)
            job = cron.json()["data"]
            self.assertEqual(job["kanban_board"], "default")
            self.assertTrue(job["kanban_task_id"])
            tasks = await client.get("/xnobrain/api/runtime/v1/kanban/boards/default/tasks")
            self.assertEqual(tasks.status_code, 200, tasks.text)
            self.assertEqual(tasks.json()["data"]["tasks"][0]["title"], "Morning review")
            self.assertEqual(tasks.json()["data"]["tasks"][0]["status"], "scheduled")
            self.assertEqual(tasks.json()["data"]["tasks"][0]["kanban_status"], "scheduled")
            self.assertIsNotNone(tasks.json()["data"]["tasks"][0]["schedule"])

    async def test_deleting_agent_hard_deletes_profile_and_assigned_tasks_on_every_board(self):
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            created_agent = await client.post(
                "/xnobrain/api/runtime/v1/agents",
                json={"display_name": "Disposable assistant"},
            )
            self.assertEqual(created_agent.status_code, 201, created_agent.text)
            agent_id = created_agent.json()["data"]["id"]

            created_board = await client.post(
                "/xnobrain/api/runtime/v1/kanban/boards",
                json={"slug": "project", "name": "Project"},
            )
            self.assertEqual(created_board.status_code, 201, created_board.text)

            for board in ("default", "project"):
                task = await client.post(
                    f"/xnobrain/api/runtime/v1/kanban/boards/{board}/tasks",
                    json={
                        "title": f"{board} assigned task",
                        "description": "This task belongs to the deleted assistant.",
                        "status": "backlog",
                        "assignee": agent_id,
                    },
                )
                self.assertEqual(task.status_code, 201, task.text)

            retained = await client.post(
                "/xnobrain/api/runtime/v1/kanban/boards/default/tasks",
                json={
                    "title": "Unassigned task",
                    "description": "This task must remain.",
                    "status": "backlog",
                },
            )
            self.assertEqual(retained.status_code, 201, retained.text)

            deleted = await client.delete(
                f"/xnobrain/api/runtime/v1/agents/{agent_id}/delete",
            )
            self.assertEqual(deleted.status_code, 200, deleted.text)
            self.assertEqual(
                deleted.json()["data"],
                {
                    "deleted": True,
                    "recoverable": False,
                    "kanban_tasks_deleted": 2,
                },
            )
            self.assertFalse((self.profiles / agent_id).exists())
            self.assertEqual(
                list((Path(self.temp.name) / "trash" / "profiles").iterdir()),
                [],
            )

            default_tasks = await client.get(
                "/xnobrain/api/runtime/v1/kanban/boards/default/tasks?include_archived=true",
            )
            project_tasks = await client.get(
                "/xnobrain/api/runtime/v1/kanban/boards/project/tasks?include_archived=true",
            )
            self.assertEqual(
                [task["title"] for task in default_tasks.json()["data"]["tasks"]],
                ["Unassigned task"],
            )
            self.assertEqual(project_tasks.json()["data"]["tasks"], [])

    async def test_pre_run_assignment_and_clean_transition_conflict(self):
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            researcher = await client.post(
                "/xnobrain/api/runtime/v1/agents",
                json={"name": "researcher"},
            )
            reviewer = await client.post(
                "/xnobrain/api/runtime/v1/agents",
                json={"name": "reviewer"},
            )
            researcher_id = researcher.json()["data"]["id"]
            reviewer_id = reviewer.json()["data"]["id"]
            created = await client.post(
                "/xnobrain/api/runtime/v1/kanban/boards/default/tasks",
                json={"title": "Assignable task", "description": "Assign this before it runs.", "status": "backlog"},
            )
            task_id = created.json()["data"]["id"]
            assigned = await client.post(
                f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{task_id}/assign",
                json={"assignee": researcher_id},
            )
            self.assertEqual(assigned.status_code, 200, assigned.text)
            self.assertEqual(assigned.json()["data"]["assignees"], [researcher_id])
            self.assertEqual(assigned.json()["data"]["workspace_kind"], "dir")
            self.assertIsNone(assigned.json()["data"]["workspace_path"])

            await client.post(
                f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{task_id}/move",
                json={"status": "todo"},
            )
            reassigned = await client.post(
                f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{task_id}/assign",
                json={"assignee": reviewer_id},
            )
            self.assertEqual(reassigned.status_code, 200, reassigned.text)
            self.assertEqual(reassigned.json()["data"]["assignees"], [reviewer_id])
            from xnobrain.integrations import kanban as kanban_adapter
            from hermes_cli import kanban_db
            with kanban_adapter.connection("default") as conn:
                native_task = kanban_db.get_task(conn, task_id)
            self.assertEqual(
                Path(native_task.workspace_path),
                self.profiles / reviewer_id / "workspace",
            )

            promoted = await client.post(
                f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{task_id}/move",
                json={"status": "running"},
            )
            self.assertEqual(promoted.status_code, 200, promoted.text)
            self.assertEqual(promoted.json()["data"]["status"], "ready")
            from xnobrain.integrations import kanban as kanban_adapter
            from hermes_cli import kanban_db
            with kanban_adapter.connection("default") as conn:
                claimed = kanban_db.claim_task(conn, task_id, claimer="test")
            self.assertIsNotNone(claimed)
            conflict = await client.post(
                f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{task_id}/move",
                json={"status": "backlog"},
            )
            self.assertEqual(conflict.status_code, 409, conflict.text)
            self.assertEqual(conflict.json()["message"], "active tasks cannot be returned to Backlog")
            self.assertNotIn("Hermes", conflict.text)

            assignment_conflict = await client.post(
                f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{task_id}/assign",
                json={"assignee": "writer"},
            )
            self.assertEqual(assignment_conflict.status_code, 409, assignment_conflict.text)
            self.assertEqual(assignment_conflict.json()["message"], "A task can only be assigned before it starts")

    async def test_unscheduled_task_moves_between_backlog_and_todo_without_becoming_scheduled(self):
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            created = await client.post(
                "/xnobrain/api/runtime/v1/kanban/boards/default/tasks",
                json={
                    "title": "Manual triage task",
                    "description": "Keep board placement independent from scheduling.",
                    "status": "backlog",
                },
            )
            task_id = created.json()["data"]["id"]
            todo = await client.post(
                f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{task_id}/move",
                json={"status": "todo"},
            )
            self.assertEqual(todo.status_code, 200, todo.text)
            self.assertEqual(todo.json()["data"]["status"], "todo")
            self.assertEqual(todo.json()["data"]["kanban_status"], "todo")
            self.assertIsNone(todo.json()["data"]["schedule"])

            backlog = await client.post(
                f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{task_id}/move",
                json={"status": "backlog"},
            )
            self.assertEqual(backlog.status_code, 200, backlog.text)
            self.assertEqual(backlog.json()["data"]["status"], "triage")
            self.assertEqual(backlog.json()["data"]["kanban_status"], "backlog")

    async def test_disabled_assignee_skill_is_rejected_server_side(self):
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            agent = await client.post("/xnobrain/api/runtime/v1/agents", json={"name": "Policy worker"})
            agent_id = agent.json()["data"]["id"]
            with patch.object(
                self.composition.service.agents,
                "list_skills",
                return_value={"skills": [{"skill_id": "disabled-skill", "installed": True, "enabled": False}]},
            ):
                rejected = await client.post(
                    "/xnobrain/api/runtime/v1/kanban/boards/default/tasks",
                    json={
                        "title": "Impossible skill request",
                        "description": "The API must enforce the assignee's effective skill policy.",
                        "status": "backlog",
                        "assignee": agent_id,
                        "skills": ["disabled-skill"],
                    },
                )
            self.assertEqual(rejected.status_code, 409, rejected.text)
            self.assertEqual(rejected.json()["error"]["code"], "skill_not_enabled")

    async def test_board_event_feed_uses_safe_public_shapes(self):
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            created = await client.post(
                "/xnobrain/api/runtime/v1/kanban/boards/default/tasks",
                json={"title": "Event task", "description": "Generate a safe event.", "status": "backlog"},
            )
            task_id = created.json()["data"]["id"]
            rows = self.composition.service.kanban.board_events("default", after_id=0)
            event = next(item for item in rows if item["task_id"] == task_id and item["kind"] == "created")
            self.assertEqual(event["title"], "Event task")
            self.assertEqual(event["status"], "triage")
            self.assertEqual(event["kanban_status"], "backlog")
            self.assertNotIn("body", event["payload"])

    async def test_backlog_promotion_accepts_dispatcher_ready_race(self):
        from hermes_cli import kanban_db

        def dispatcher_won(conn, task_id, **kwargs):
            with kanban_db.write_txn(conn):
                conn.execute(
                    "UPDATE tasks SET status = 'ready' WHERE id = ? AND status = 'todo'",
                    (task_id,),
                )
            return False, f"task {task_id} is 'ready'; promote only applies to 'todo' or 'blocked'"

        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            created = await client.post(
                "/xnobrain/api/runtime/v1/kanban/boards/default/tasks",
                json={
                    "title": "Racing transition",
                    "description": "The dispatcher may promote this between API operations.",
                    "status": "backlog",
                },
            )
            task_id = created.json()["data"]["id"]
            with patch.object(kanban_db, "promote_task", side_effect=dispatcher_won):
                moved = await client.post(
                    f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{task_id}/move",
                    json={"status": "running"},
                )

        self.assertEqual(moved.status_code, 200, moved.text)
        self.assertEqual(moved.json()["data"]["status"], "ready")
        self.assertEqual(moved.json()["data"]["kanban_status"], "running")

    async def test_board_metadata_never_exposes_hermes_database_path(self):
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            created = await client.post("/xnobrain/api/runtime/v1/kanban/boards", json={"slug": "safe-board", "name": "Safe board"})
            self.assertEqual(created.status_code, 201, created.text)
            self.assertNotIn("db_path", created.json()["data"])
            updated = await client.patch("/xnobrain/api/runtime/v1/kanban/boards/safe-board", json={"description": "No path"})
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertNotIn("db_path", updated.json()["data"])

    async def test_dependencies_events_and_pagination_use_safe_public_shapes(self):
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            first = await client.post("/xnobrain/api/runtime/v1/kanban/boards/default/tasks", json={"title": "Parent", "description": "Finish the parent.", "status": "todo"})
            second = await client.post("/xnobrain/api/runtime/v1/kanban/boards/default/tasks", json={"title": "Child", "description": "Wait for the parent.", "status": "todo"})
            parent_id = first.json()["data"]["id"]
            child_id = second.json()["data"]["id"]
            linked = await client.post(f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{child_id}/links", json={"parent_id": parent_id, "child_id": child_id})
            self.assertEqual(linked.status_code, 201, linked.text)
            self.assertEqual(linked.json()["data"]["parent_id"], parent_id)
            unlinked = await client.request("DELETE", f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{child_id}/links", json={"parent_id": parent_id, "child_id": child_id})
            self.assertEqual(unlinked.status_code, 200, unlinked.text)
            page = await client.get("/xnobrain/api/runtime/v1/kanban/boards/default/tasks?limit=1&offset=0")
            self.assertEqual(page.status_code, 200, page.text)
            self.assertEqual(page.json()["data"]["limit"], 1)
            events = await client.get(f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{child_id}/events")
            self.assertEqual(events.status_code, 200, events.text)
            self.assertNotIn("workspace_path", events.text)

    async def test_board_list_is_lightweight_and_selected_board_has_stats(self):
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            for index in range(3):
                created = await client.post(
                    "/xnobrain/api/runtime/v1/kanban/boards/default/tasks",
                    json={
                        "title": f"Paged task {index}",
                        "description": "Exercise progressive board loading.",
                        "status": "backlog",
                    },
                )
                self.assertEqual(created.status_code, 201, created.text)

            boards = await client.get("/xnobrain/api/runtime/v1/kanban/boards")
            self.assertEqual(boards.status_code, 200, boards.text)
            self.assertNotIn("tasks", boards.json()["data"][0])

            stats = await client.get("/xnobrain/api/runtime/v1/kanban/boards/default/stats")
            self.assertEqual(stats.status_code, 200, stats.text)
            self.assertEqual(stats.json()["data"]["current"], 3)
            self.assertEqual(stats.json()["data"]["by_status"]["backlog"], 3)

            first = await client.get("/xnobrain/api/runtime/v1/kanban/boards/default/tasks?limit=2&offset=0")
            second = await client.get("/xnobrain/api/runtime/v1/kanban/boards/default/tasks?limit=2&offset=2")
            self.assertEqual(first.json()["data"]["total"], 3)
            self.assertEqual(len(first.json()["data"]["tasks"]), 2)
            self.assertEqual(len(second.json()["data"]["tasks"]), 1)

    def test_completed_tasks_allow_archive_transition(self):
        from xnobrain.services.kanban import _allowed_moves

        self.assertEqual(_allowed_moves("done"), ["archived"])

    async def test_pre_run_edit_skills_comments_activity_and_conversation_link(self):
        from hermes_cli import kanban_db

        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            missing_brief = await client.post(
                "/xnobrain/api/runtime/v1/kanban/boards/default/tasks",
                json={"title": "Missing worker brief", "status": "backlog"},
            )
            self.assertEqual(missing_brief.status_code, 422, missing_brief.text)
            agent = await client.post("/xnobrain/api/runtime/v1/agents", json={"name": "Brief writer"})
            self.assertEqual(agent.status_code, 201, agent.text)
            agent_id = agent.json()["data"]["id"]
            inventory = {
                "skills": [
                    {"skill_id": "writing", "installed": True, "enabled": True},
                    {"skill_id": "web-research", "installed": True, "enabled": True},
                ]
            }
            with patch.object(self.composition.service.agents, "list_skills", return_value=inventory):
                created = await client.post(
                    "/xnobrain/api/runtime/v1/kanban/boards/default/tasks",
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

            with patch.object(self.composition.service.agents, "list_skills", return_value=inventory):
                updated = await client.patch(
                    f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{task_id}",
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
            self.assertEqual(task["allowed_kanban_statuses"], ["todo", "running", "archived"])
            self.assertTrue(any(event["kind"] == "edited" for event in task["events"]))

            comment = await client.post(
                f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{task_id}/comments",
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
                f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{task_id}",
            )
            self.assertEqual(detail.status_code, 200, detail.text)
            payload = detail.json()["data"]
            self.assertEqual(payload["comments"][0]["body"], "Use the approved brand voice.")
            self.assertEqual(payload["worker_activity"]["entries"][0]["name"], "browser_navigate")
            self.assertEqual(payload["conversation"]["id"], "20260724_102648_3e2c61")
            self.assertTrue(payload["conversation"]["url"].endswith("/20260724_102648_3e2c61"))
            self.assertNotIn("private prompt", detail.text)
            self.assertNotIn("secret tool output", detail.text)

    async def test_assigned_automation_approvals_heartbeat_filter_and_cancel(self):
        from hermes_cli import kanban_db

        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            agent = await client.post("/xnobrain/api/runtime/v1/agents", json={"name": "Automation worker"})
            self.assertEqual(agent.status_code, 201, agent.text)
            agent_id = agent.json()["data"]["id"]
            created = await client.post(
                "/xnobrain/api/runtime/v1/kanban/boards/default/tasks",
                json={
                    "title": "Autonomous task",
                    "description": "Read and update files without waiting for approval.",
                    "status": "todo",
                    "assignee": agent_id,
                },
            )
            self.assertEqual(created.status_code, 201, created.text)
            task_id = created.json()["data"]["id"]

            profile = self.profiles / agent_id
            config_response = await client.get(
                f"/xnobrain/api/runtime/v1/agents/{agent_id}/detail",
            )
            self.assertEqual(config_response.status_code, 200, config_response.text)
            config = config_response.json()["data"]["config"]
            self.assertEqual(config["approval_mode"], "off")
            self.assertFalse(config["skills_write_approval"])
            self.assertFalse(config["memory_write_approval"])
            self.assertTrue(any((profile / "snapshots" / "config").rglob("*")))

            with kanban_db.connect_closing(board="default") as conn:
                claimed = kanban_db.claim_task(conn, task_id, claimer="test")
                self.assertIsNotNone(claimed)
                self.assertTrue(kanban_db.heartbeat_worker(conn, task_id, note="still working"))

            detail = await client.get(
                f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{task_id}",
            )
            self.assertEqual(detail.status_code, 200, detail.text)
            self.assertFalse(any(
                event["kind"] == "heartbeat"
                for event in detail.json()["data"]["events"]
            ))

            cancelled = await client.post(
                f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{task_id}/cancel",
            )
            self.assertEqual(cancelled.status_code, 200, cancelled.text)
            self.assertEqual(cancelled.json()["data"]["status"], "done")
            self.assertEqual(cancelled.json()["data"]["summary"], "Task cancelled")

    async def test_sqlite_schedule_locks_moves_and_releases_without_claiming(self):
        scheduled_at = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0)
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            agent = await client.post("/xnobrain/api/runtime/v1/agents", json={"name": "Scheduled worker"})
            agent_id = agent.json()["data"]["id"]
            created = await client.post(
                "/xnobrain/api/runtime/v1/kanban/boards/default/tasks",
                json={
                    "title": "Run later",
                    "description": "Execute this at the selected time.",
                    "status": "scheduled",
                    "assignee": agent_id,
                    "schedule": {
                        "recurrence": "once",
                        "scheduled_at": scheduled_at.isoformat(),
                        "timezone": "Etc/UTC",
                    },
                },
            )
            self.assertEqual(created.status_code, 201, created.text)
            task = created.json()["data"]
            task_id = task["id"]
            self.assertEqual(task["status"], "scheduled")
            self.assertEqual(task["kanban_status"], "scheduled")
            self.assertEqual(task["allowed_kanban_statuses"], ["archived"])
            self.assertEqual(task["schedule"]["recurrence"], "once")
            self.assertTrue(task["schedule"]["enabled"])

            blocked_move = await client.post(
                f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{task_id}/move",
                json={"status": "running"},
            )
            self.assertEqual(blocked_move.status_code, 409, blocked_move.text)
            self.assertEqual(
                blocked_move.json()["message"],
                "Scheduled tasks are controlled by their schedule",
            )

            paused = await client.post(
                f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{task_id}/schedule",
                json={"action": "pause"},
            )
            self.assertEqual(paused.status_code, 200, paused.text)
            self.assertFalse(paused.json()["data"]["schedule"]["enabled"])

            started = await client.post(
                f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{task_id}/schedule",
                json={"action": "run_now"},
            )
            self.assertEqual(started.status_code, 200, started.text)
            started_task = started.json()["data"]
            self.assertEqual(started_task["status"], "ready")
            self.assertEqual(started_task["kanban_status"], "running")
            self.assertIsNone(started_task["worker"])
            self.assertFalse(started_task["schedule"]["enabled"])
            self.assertIsNone(started_task["schedule"]["next_run_at"])
            rerun = await client.post(
                f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{task_id}/schedule",
                json={"action": "run_now"},
            )
            self.assertEqual(rerun.status_code, 409, rerun.text)
            self.assertEqual(rerun.json()["message"], "one-time schedule has already run")
            resume = await client.post(
                f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{task_id}/schedule",
                json={"action": "resume"},
            )
            self.assertEqual(resume.status_code, 409, resume.text)
            self.assertEqual(resume.json()["message"], "one-time schedule has already run")

    async def test_recurring_schedule_creates_idempotent_occurrences(self):
        from xnobrain.integrations import kanban as kanban_adapter
        from hermes_cli import kanban_db

        scheduled_at = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0)
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            agent = await client.post("/xnobrain/api/runtime/v1/agents", json={"name": "Repeating worker"})
            agent_id = agent.json()["data"]["id"]
            created = await client.post(
                "/xnobrain/api/runtime/v1/kanban/boards/default/tasks",
                json={
                    "title": "Repeat audit",
                    "description": "Create one occurrence per interval.",
                    "status": "scheduled",
                    "assignee": agent_id,
                    "schedule": {
                        "recurrence": "interval",
                        "scheduled_at": scheduled_at.isoformat(),
                        "timezone": "Etc/UTC",
                        "interval_minutes": 60,
                    },
                },
            )
            self.assertEqual(created.status_code, 201, created.text)
            template_id = created.json()["data"]["id"]
            fired = await client.post(
                f"/xnobrain/api/runtime/v1/kanban/boards/default/tasks/{template_id}/schedule",
                json={"action": "run_now"},
            )
            self.assertEqual(fired.status_code, 200, fired.text)
            self.assertEqual(fired.json()["data"]["status"], "scheduled")
            self.assertEqual(fired.json()["data"]["schedule"]["occurrence_count"], 1)
            with kanban_adapter.connection("default") as conn:
                tasks = kanban_db.list_tasks(conn, include_archived=True)
                occurrences = [
                    task for task in tasks
                    if str(task.id) != template_id
                    and str(task.idempotency_key or "").startswith(f"schedule:{template_id}:")
                ]
            self.assertEqual(len(occurrences), 1)
            self.assertIn(str(occurrences[0].status), {"todo", "ready"})

    async def test_api_created_conversations_use_the_native_session_id_shape(self):
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
            agent = await client.post("/xnobrain/api/runtime/v1/agents", json={"name": "Session worker"})
            agent_id = agent.json()["data"]["id"]
            conversation = await client.post(
                f"/xnobrain/api/runtime/v1/conversations?agent={agent_id}",
                json={"title": "Manual conversation"},
            )
            self.assertEqual(conversation.status_code, 201, conversation.text)
            conversation_id = conversation.json()["data"]["id"]
            self.assertIsNotNone(re.fullmatch(r"\d{8}_\d{6}_[0-9a-f]{6}", conversation_id))
