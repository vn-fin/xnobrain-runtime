"""Project persistence, isolation, concurrency and signed HTTP contracts."""

import asyncio
import shutil
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from xnobrain.handlers import APIHandlers
from xnobrain.repositories import FileRepository, StoreError
from xnobrain.repositories.custom_page_locks import acquire, execution_name, release
from xnobrain.routes import setup_routes
from xnobrain.services.agent_projects import AgentProjectsService
from xnobrain.services.agents import AgentsServiceMixin
from xnobrain.trusted_context import TrustedRequestContext, principal_signature


class AgentProjectsTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict("os.environ", {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""})
        environment.start()
        self.addCleanup(environment.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = FileRepository(self.root, self.root / "profiles")
        for agent in ("research", "writer", "big-brother"):
            path = self.files.profiles_root / agent
            path.mkdir()
            (path / "chat.txt").write_text("retained synthetic conversation")
        self.platform = SimpleNamespace(repository=self.files)
        self.service = AgentProjectsService(self.platform)
        self.platform.agent_projects = self.service
        self.owner = TrustedRequestContext("owner", "tenant")

    def create(self, name="Work", revision=0, owner=None):
        return self.service.mutate(
            "create", {"name": name, "expected_revision": revision}, owner or self.owner
        )

    def assign(self, project, revision, agent="research", owner=None):
        return self.service.mutate(
            "assign",
            {"project_id": project, "expected_revision": revision},
            owner or self.owner,
            agent_id=agent,
        )

    def assert_code(self, code, callback):
        with self.assertRaises(StoreError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def test_create_rename_move_ungroup_delete_preserves_profiles_and_restart(self):
        self.assertEqual(
            self.service.read(self.owner), {"revision": 0, "projects": [], "memberships": {}}
        )
        first = self.create("  Product  ")
        a = first["projects"][0]["id"]
        self.assertEqual(first["projects"][0]["name"], "Product")
        second = self.create("Research", 1)
        b = second["projects"][1]["id"]
        self.assign(a, 2)
        moved = self.assign(b, 3)
        self.assertEqual(moved["memberships"], {"research": b})
        restored = AgentProjectsService(self.platform).read(self.owner)
        self.assertEqual(restored, moved)
        renamed = self.service.mutate(
            "rename", {"name": "Reading", "expected_revision": 4}, self.owner, project_id=b
        )
        self.assertEqual(renamed["projects"][1]["name"], "Reading")
        self.assertEqual(self.assign(None, 5)["memberships"], {})
        self.assign(a, 6)
        deleted = self.service.mutate("delete", {"expected_revision": 7}, self.owner, project_id=a)
        self.assertEqual(deleted["memberships"], {})
        self.assertEqual(len(deleted["projects"]), 1)
        self.assertEqual(
            (self.files.profiles_root / "research/chat.txt").read_text(),
            "retained synthetic conversation",
        )

    def test_foreign_user_and_tenant_cannot_read_or_reference_projects(self):
        project = self.create()["projects"][0]["id"]
        for foreign in (
            TrustedRequestContext("other", "tenant"),
            TrustedRequestContext("owner", "other"),
        ):
            self.assertEqual(self.service.read(foreign)["projects"], [])
            for operation in ("assign", "rename", "delete"):
                body = {"expected_revision": 0}
                if operation == "assign":
                    body["project_id"] = project
                if operation == "rename":
                    body["name"] = "Foreign"
                self.assert_code(
                    "agent_project_not_found",
                    lambda operation=operation, body=body, foreign=foreign: self.service.mutate(
                        operation, body, foreign, agent_id="research", project_id=project
                    ),
                )

    def test_missing_identity_mismatched_workspace_and_revoked_context_are_rejected(self):
        for identity, code in (
            (TrustedRequestContext(), "trusted_subject_required"),
            (TrustedRequestContext("owner", "tenant", "org"), "agent_projects_workspace_mismatch"),
            (
                TrustedRequestContext("owner", "tenant", ownership_context={"state": "revoked"}),
                "agent_projects_authority_revoked",
            ),
        ):
            self.assert_code(code, lambda identity=identity: self.service.read(identity))
        self.assertFalse(self.service.repository.path.exists())

    def test_folder_memberships_are_private_to_the_user_and_selected_workspace(self):
        org = TrustedRequestContext("owner", "organization-org_one", "org_one")
        created = self.service.mutate("create", {"name": "Work", "expected_revision": 0}, org)
        project = created["projects"][0]["id"]
        self.service.mutate(
            "assign", {"project_id": project, "expected_revision": 1}, org, agent_id="research"
        )
        self.assertEqual(self.service.read(org)["memberships"], {"research": project})
        for other in (
            self.owner,
            TrustedRequestContext("another", "organization-org_one", "org_one"),
            TrustedRequestContext("owner", "organization-org_two", "org_two"),
        ):
            self.assertEqual(self.service.read(other)["projects"], [])
            self.assert_code(
                "agent_project_not_found",
                lambda other=other: self.service.mutate(
                    "delete", {"expected_revision": 0}, other, project_id=project
                ),
            )

    def test_big_brother_invalid_name_and_browser_authority_are_rejected(self):
        project = self.create()["projects"][0]["id"]
        self.assert_code("protected_agent", lambda: self.assign(project, 1, "big-brother"))
        self.assert_code("agent_not_found", lambda: self.assign(project, 1, "absent"))
        for name in ("   ", "x\nname", "x\x7fname", "a" * 201):
            self.assert_code("agent_projects_invalid_input", lambda name=name: self.create(name, 1))
        self.assert_code(
            "agent_projects_invalid_input",
            lambda: self.service.mutate(
                "create",
                {"name": "X", "expected_revision": 1, "owner_user_id": "foreign"},
                self.owner,
            ),
        )
        with self.assertRaises(StoreError):
            self.assign(project, 1, "../escape")
        self.assert_code(
            "agent_projects_invalid_input",
            lambda: self.service.mutate(
                "assign", {"expected_revision": 1}, self.owner, agent_id="research"
            ),
        )

    def test_revision_race_has_one_winner_and_no_duplicate_assignment(self):
        first = self.create()["projects"][0]["id"]
        second = self.create("Other", 1)["projects"][1]["id"]

        # Repository-level concurrency avoids the intentionally nonblocking profile gate.
        def write(project):
            try:
                return self.service.repository.mutate(
                    ("tenant", "owner"), 2, "assign", agent_id="research", project_id=project
                )["revision"]
            except StoreError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(write, (first, second)))
        self.assertCountEqual(outcomes, [3, "agent_projects_revision_conflict"])
        self.assertEqual(len(self.service.read(self.owner)["memberships"]), 1)

    def test_deleted_profiles_are_reconciled_and_explicit_cleanup_bumps_revision(self):
        project = self.create()["projects"][0]["id"]
        self.assign(project, 1)
        shutil.rmtree(self.files.profiles_root / "research")
        self.assertEqual(self.service.read(self.owner)["memberships"], {})
        self.assign(project, 3, "writer")
        self.service.repository.forget_agent("writer")
        self.assertEqual(self.service.read(self.owner)["revision"], 5)

    def test_running_agent_can_move_and_read_without_exclusive_execution_lock(self):
        project = self.create()["projects"][0]["id"]
        descriptor = acquire(self.root, execution_name("research"), shared=True)
        try:
            self.assign(project, 1)
            self.assertEqual(self.service.read(self.owner)["memberships"], {"research": project})
        finally:
            release(descriptor)

    def test_profile_delete_cleans_membership_and_recovers_from_metadata_failure(self):
        project = self.create()["projects"][0]["id"]
        self.assign(project, 1)
        self.platform._is_big_brother = lambda _: False
        self.platform.kanban = SimpleNamespace(delete_assignee_tasks=Mock(return_value=0))
        self.platform.agents = SimpleNamespace(sync_profiles_registry=Mock())
        self.platform._cache = Mock()
        result = AgentsServiceMixin.delete_agent(self.platform, "research")
        self.assertTrue(result["deleted"])
        self.assertEqual(self.service.read(self.owner)["memberships"], {})
        self.assign(project, 3, "writer")
        with (
            patch.object(
                self.service.repository,
                "forget_agent",
                side_effect=StoreError("unavailable", status=503),
            ),
            self.assertLogs("xnobrain.services.agents", level="WARNING"),
        ):
            self.assertTrue(AgentsServiceMixin.delete_agent(self.platform, "writer")["deleted"])
        self.assertEqual(self.platform.agents.sync_profiles_registry.call_count, 2)
        self.assertEqual(self.platform._cache.invalidate.call_count, 2)
        self.assertEqual(self.service.read(self.owner)["memberships"], {})

    def test_database_enforces_parent_scope_unique_agent_and_system_protection(self):
        project = self.create()["projects"][0]["id"]
        with self.service.repository.database() as db:
            for row in (
                ("other", "owner", "writer", project, "now"),
                ("tenant", "owner", "big-brother", project, "now"),
            ):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("INSERT INTO agent_project_memberships VALUES (?,?,?,?,?)", row)
        self.assign(project, 1)
        with self.service.repository.database() as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO agent_project_memberships VALUES (?,?,?,?,?)",
                    ("tenant", "owner", "research", project, "now"),
                )

    def test_unsafe_storage_and_missing_mount_do_not_create_fallback_database(self):
        outside = self.root / "outside"
        outside.write_text("untouched")
        self.service.repository.path.symlink_to(outside)
        self.assert_code(
            "agent_projects_storage_unavailable", lambda: self.service.read(self.owner)
        )
        self.assertEqual(outside.read_text(), "untouched")
        self.service.repository.path.unlink()
        with patch.object(
            self.files.storage_mount, "check", side_effect=StoreError("missing", status=503)
        ):
            with self.assertRaises(StoreError):
                self.service.read(self.owner)
        self.assertFalse(self.service.repository.path.exists())

    def test_signed_http_envelope_validation_and_unconditional_routes(self):
        async def journey():
            app = FastAPI()
            setup_routes(app, APIHandlers(self.platform))
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                root = "/xnobrain/api/runtime/v1/agent-projects"
                headers = {
                    "x-xnobrain-verified-subject": "owner",
                    "x-xnobrain-verified-tenant": "tenant",
                }
                self.assertEqual((await client.get(root, headers=headers)).status_code, 401)
                headers["x-xnobrain-principal-signature"] = principal_signature(
                    "fixture-only", "owner", "tenant"
                )
                response = await client.post(
                    root, headers=headers, json={"name": "Project", "expected_revision": 0}
                )
                self.assertEqual(response.status_code, 201, response.text)
                self.assertTrue(response.json()["success"])
                self.assertEqual(response.headers["cache-control"], "private, no-store")
                project = response.json()["data"]["projects"][0]["id"]
                response = await client.put(
                    "/xnobrain/api/runtime/v1/agents/research/project",
                    headers=headers,
                    json={"project_id": project, "expected_revision": 1},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["data"]["memberships"], {"research": project})
                invalid = await client.post(
                    root,
                    headers=headers,
                    json={"name": "X", "expected_revision": 2, "tenant_id": "other"},
                )
                self.assertEqual(invalid.status_code, 422)
                renamed = await client.patch(
                    f"{root}/{project}",
                    headers=headers,
                    json={"name": "Renamed", "expected_revision": 2},
                )
                self.assertEqual(renamed.status_code, 200, renamed.text)
                self.assertEqual(renamed.json()["data"]["projects"][0]["name"], "Renamed")
                deleted = await client.request(
                    "DELETE",
                    f"{root}/{project}",
                    headers=headers,
                    json={"expected_revision": 3},
                )
                self.assertEqual(deleted.status_code, 200, deleted.text)
                self.assertEqual(deleted.json()["data"]["memberships"], {})
                self.assertTrue((self.files.profiles_root / "research/chat.txt").is_file())
                with patch.dict("os.environ", {"FT_ENABLE_AGENT_PROJECT_FOLDERS": "false"}):
                    self.assertEqual((await client.get(root, headers=headers)).status_code, 200)
                    restarted = FastAPI()
                    setup_routes(restarted, APIHandlers(self.platform))
                    self.assertTrue(
                        any(getattr(route, "path", "") == root for route in restarted.routes)
                    )

        with patch.dict("os.environ", {"RUNTIME_INTERNAL_SERVICE_TOKEN": "fixture-only"}):
            asyncio.run(journey())
