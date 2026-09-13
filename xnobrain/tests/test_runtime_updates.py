"""FT0002 Runtime consumer lifecycle and narrow tool tests."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from xnobrain.models.runtime_updates import ReleaseSelector, RuntimeUpdatePreflight
from xnobrain.repositories.base import RepositoryBase
from xnobrain.services.base import ServiceError
from xnobrain.services.runtime_updates import RuntimeUpdateService

TARGET = {
    "version": "2.1.0",
    "source_commit": "a" * 40,
    "runtime_digest": "sha256:" + "b" * 64,
    "data_schema": 1,
}


def request(**values):
    return {
        "operation_id": "upd_fixture",
        "generation": 3,
        "target": TARGET,
        **values,
    }


class FakeConnector:
    def __init__(self):
        self.paused = False

    async def pause_dispatch(self):
        self.paused = True

    def resume_dispatch(self):
        self.paused = False

    async def cancel_active_commands(self):
        return None


class RuntimeUpdateLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.data = root / "data"
        self.profiles = root / "profiles"
        self.big_brother = root / "big-brother"
        self.big_brother.mkdir()
        self.profiles.mkdir()
        (self.data / "durable").mkdir(parents=True)
        (self.data / "durable" / "memory.txt").write_text("preserve me", encoding="utf-8")
        database = self.data / "state.db"
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE state(value TEXT)")
            connection.execute("INSERT INTO state VALUES ('preserve me')")
        repository = RepositoryBase(self.data, self.profiles)
        self.connector = FakeConnector()
        self.platform = SimpleNamespace(
            repository=repository,
            config=SimpleNamespace(root_profile=self.big_brother),
            organization_connector=self.connector,
            cron=SimpleNamespace(active_execution_count=0),
            conversation_runs=SimpleNamespace(_active={}, shutdown=AsyncMock()),
            team_runs=SimpleNamespace(_active={}, shutdown=AsyncMock()),
            kanban=SimpleNamespace(
                active_agent_ids=Mock(return_value=set()),
                cancel_active_tasks_for_update=Mock(return_value=0),
            ),
        )
        self.environment = patch.dict(
            os.environ,
            {
                "RUNTIME_UPDATE_SERVICE_TOKEN": "update-service-token",
                "RUNTIME_UPDATE_DATA_PATH": str(self.data),
                "XNOBRAIN_VERSION": TARGET["version"],
                "XNOBRAIN_SOURCE_COMMIT": TARGET["source_commit"],
                "XNOBRAIN_RUNTIME_DIGEST": TARGET["runtime_digest"],
                "RUNTIME_DATA_SCHEMA": "1",
            },
            clear=False,
        )
        self.environment.start()
        self.service = RuntimeUpdateService(self.platform)

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    async def call(self, method, body):
        return await getattr(self.service, method)(body, update_token="update-service-token")

    async def test_full_lifecycle_persists_manifest_and_resumes_only_after_verify(self):
        with patch.object(
            self.service.storage,
            "layout",
            return_value={
                "data_path": str(self.data),
                "separate_mount": False,
                "mount_point": "/",
                "filesystem": "ext4",
            },
        ):
            preflight = await self.call(
                "preflight",
                request(
                    expected_layout="root_only",
                    data_path=str(self.data),
                    required_free_bytes=1,
                ),
            )
        self.assertTrue(preflight["ready"])
        drained = await self.call("drain", request(deadline_seconds=0))
        self.assertTrue(drained["dispatch_paused"])
        with self.assertRaises(ServiceError) as blocked:
            self.service.require_dispatch()
        self.assertEqual(blocked.exception.code, "runtime_update_maintenance")

        checkpoint = await self.call("checkpoint", request())
        replay = await self.call("checkpoint", request())
        self.assertEqual(replay["checkpoint_id"], checkpoint["checkpoint_id"])
        self.assertIn("data/state.db", checkpoint["sqlite_checkpoints"])
        self.assertGreaterEqual(checkpoint["manifest"]["file_count"], 2)

        ready = await self.call("readiness", request(checkpoint_id=checkpoint["checkpoint_id"]))
        self.assertTrue(ready["ready"])
        verified = await self.call(
            "post_verify", request(checkpoint_id=checkpoint["checkpoint_id"])
        )
        self.assertTrue(verified["verified"])
        resumed = await self.call("resume", request())
        self.assertTrue(resumed["resumed"])
        self.assertFalse(self.service.dispatch_paused)
        self.assertFalse(self.service.repository.maintenance())

    async def test_changed_data_fails_post_verify_and_recovery_remains_forward_only(self):
        await self.call("drain", request(deadline_seconds=0))
        checkpoint = await self.call("checkpoint", request())
        (self.data / "durable" / "memory.txt").write_text("changed", encoding="utf-8")
        with self.assertRaises(ServiceError) as failed:
            await self.call("post_verify", request(checkpoint_id=checkpoint["checkpoint_id"]))
        self.assertEqual(failed.exception.code, "runtime_update_post_verify_failed")
        recovery = await self.call(
            "recover", request(action="needs_operator", reason_code="data_mismatch")
        )
        self.assertEqual(recovery["state"], "needs_operator")
        self.assertFalse(recovery["version_rollback_allowed"])
        self.assertNotIn("rollback", recovery["allowed_actions"])
        with self.assertRaises(ServiceError) as older:
            await self.call(
                "recover",
                request(
                    action="approve_newer_target",
                    reason_code="candidate_failed",
                    newer_target={**TARGET, "version": "2.0.0", "source_commit": "c" * 40},
                ),
            )
        self.assertEqual(older.exception.code, "runtime_update_recovery_target_invalid")
        with self.assertRaises(ServiceError) as cannot_resume:
            await self.call("resume", request())
        self.assertEqual(cannot_resume.exception.code, "runtime_update_not_verified")

    async def test_auth_fence_layout_and_unsafe_symlink_fail_closed(self):
        with self.assertRaises(ServiceError) as unauthorized:
            await self.service.preflight(
                request(expected_layout="root_only", data_path=str(self.data))
            )
        self.assertEqual(unauthorized.exception.code, "runtime_update_unauthorized")

        with (
            patch.object(
                self.service.storage,
                "layout",
                return_value={
                    "data_path": str(self.data),
                    "separate_mount": True,
                    "mount_point": str(self.data),
                    "filesystem": "ext4",
                },
            ),
            self.assertRaises(ServiceError) as changed_layout,
        ):
            await self.call(
                "preflight",
                request(expected_layout="root_only", data_path=str(self.data)),
            )
        self.assertEqual(changed_layout.exception.code, "runtime_update_fence_conflict")

        await self.call("drain", request(deadline_seconds=0))
        (self.data / "unsafe").symlink_to("/tmp")
        with self.assertRaises(ServiceError) as unsafe:
            await self.call("checkpoint", request())
        self.assertEqual(unsafe.exception.code, "runtime_update_unsafe_data")

    async def test_stale_generation_and_target_retarget_are_rejected(self):
        await self.call("drain", request(deadline_seconds=0))
        stale = request(deadline_seconds=0)
        stale["generation"] = 2
        with self.assertRaises(ServiceError) as generation:
            await self.call("drain", stale)
        self.assertEqual(generation.exception.code, "runtime_update_stale_generation")

        changed = request(deadline_seconds=0)
        changed["target"] = {**TARGET, "version": "2.2.0"}
        with self.assertRaises(ServiceError) as target:
            await self.call("drain", changed)
        self.assertEqual(target.exception.code, "runtime_update_fence_conflict")

    async def test_drain_waits_then_can_cancel_runtime_owned_work(self):
        sleeper = asyncio.create_task(asyncio.sleep(60))
        self.platform.conversation_runs._active = {
            "run": SimpleNamespace(task=sleeper),
        }

        async def shutdown():
            sleeper.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await sleeper
            self.platform.conversation_runs._active.clear()

        self.platform.conversation_runs.shutdown = shutdown
        result = await self.call(
            "drain",
            request(deadline_seconds=0, cancel_active_at_deadline=True),
        )
        self.assertTrue(result["cancelled_at_deadline"])
        self.assertEqual(result["active"]["total"], 0)


class RuntimeUpdateAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_private_route_requires_service_identity_and_forbids_shell(self):
        from xnobrain.app import XNOBrainApplication
        from xnobrain.integrations import AgentManager, GlobalConfigManager

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            profiles = Path(temporary) / "profiles"
            data = Path(temporary) / "data"
            root.mkdir()
            profiles.mkdir()
            (root / "config.yaml").write_text("{}\n", encoding="utf-8")
            environment = {
                "HERMES_HOME": str(root),
                "HERMES_ROOT_PROFILE": str(root),
                "HERMES_PROFILES_ROOT": str(profiles),
                "DATA_DIR": str(data),
                "RUNTIME_UPDATE_SERVICE_TOKEN": "service-token",
                "RUNTIME_UPDATE_DATA_PATH": str(data),
                "RUNTIME_INCLUDE_PACKAGED_SKILLS": "false",
            }
            with patch.dict(os.environ, environment, clear=False):
                app = FastAPI()
                composition = XNOBrainApplication(
                    AgentManager(
                        root_profile=root,
                        profiles_root=profiles,
                        legacy_agents_root=Path(temporary) / "legacy",
                    ),
                    GlobalConfigManager(root_profile=root),
                    SimpleNamespace(),
                )
                composition.register(app)
                with patch.object(
                    composition.service.runtime_updates.storage,
                    "layout",
                    return_value={
                        "data_path": str(data),
                        "separate_mount": False,
                        "mount_point": "/",
                        "filesystem": "ext4",
                    },
                ):
                    body = request(
                        expected_layout="root_only",
                        data_path=str(data),
                        required_free_bytes=0,
                    )
                    async with AsyncClient(
                        transport=ASGITransport(app=app), base_url="http://test"
                    ) as client:
                        denied = await client.post(
                            "/xnobrain/api/runtime/v1/system/update/preflight",
                            json=body,
                        )
                        accepted = await client.post(
                            "/xnobrain/api/runtime/v1/system/update/preflight",
                            json=body,
                            headers={"x-xnobrain-update-token": "service-token"},
                        )
                        invalid = await client.post(
                            "/xnobrain/api/runtime/v1/system/update/preflight",
                            json={**body, "shell_script": "uname -a"},
                            headers={"x-xnobrain-update-token": "service-token"},
                        )
            self.assertEqual(denied.status_code, 401, denied.text)
            self.assertEqual(denied.json()["error"]["code"], "runtime_update_unauthorized")
            self.assertEqual(accepted.status_code, 200, accepted.text)
            self.assertTrue(accepted.json()["data"]["ready"])
            self.assertEqual(invalid.status_code, 422, invalid.text)


class RuntimeUpdateContractTests(unittest.TestCase):
    def test_contracts_reject_unknown_layout_shell_and_arbitrary_selector(self):
        with self.assertRaises(ValidationError):
            RuntimeUpdatePreflight.model_validate(
                request(
                    expected_layout="guessed",
                    data_path="/opt/data",
                    shell_script="rm -rf /",
                )
            )
        with self.assertRaises(ValidationError):
            ReleaseSelector(type="release_branch_latest", tag="main")
        with self.assertRaises(ValidationError):
            ReleaseSelector(type="release_tag", tag="release;curl evil")


class RuntimeUpdateToolTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        path = (
            Path(__file__).resolve().parents[2]
            / "runtime"
            / "required-plugins"
            / "xnobrain-runtime-updates"
            / "__init__.py"
        )
        spec = importlib.util.spec_from_file_location("xnobrain_runtime_update_tools", path)
        cls.module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(cls.module)

    async def test_tools_have_narrow_schemas_and_use_fixed_control_paths(self):
        registrations = []
        context = SimpleNamespace(register_tool=lambda **kwargs: registrations.append(kwargs))
        self.module.register(context)
        self.assertEqual(
            {item["name"] for item in registrations},
            {
                "workspace_update_check",
                "workspace_update_plan",
                "workspace_update_request",
                "workspace_update_status",
            },
        )
        encoded = json.dumps(registrations, default=str)
        for forbidden in ("shell", "command", "remote_url", "image_url", "commit"):
            self.assertNotIn(f'"{forbidden}"', encoded)

        with tempfile.NamedTemporaryFile("w", delete=False) as token_file:
            token_file.write("service-token")
        self.addCleanup(lambda: Path(token_file.name).unlink(missing_ok=True))
        seen = []

        async def handler(request: httpx.Request):
            seen.append(request)
            return httpx.Response(
                200,
                json={"success": True, "data": {"operation_id": "upd_safe"}},
            )

        original_client = httpx.AsyncClient

        def client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return original_client(*args, **kwargs)

        with (
            patch.dict(
                os.environ,
                {
                    "RUNTIME_CONTROL_URL": "https://control.example",
                    "RUNTIME_WORKSPACE_UPDATE_TOKEN_FILE": token_file.name,
                },
            ),
            patch.object(self.module.httpx, "AsyncClient", side_effect=client),
        ):
            result = await self.module.workspace_update_plan(
                {"selector_type": "release_branch_latest"}
            )
            await self.module.workspace_update_request(
                {
                    "plan_id": "plan_safe",
                    "plan_hash": "hash_safe",
                    "approval_id": "approval_safe",
                    "idempotency_key": "once_safe",
                }
            )
        self.assertTrue(json.loads(result)["success"])
        self.assertEqual(
            seen[0].url.path, "/xnobrain/api/control/v1/workspace/current/update-plans"
        )
        self.assertEqual(
            json.loads(seen[0].content)["selector"],
            {"type": "release_branch_latest"},
        )
        self.assertEqual(seen[1].headers["authorization"], "Bearer service-token")
        self.assertNotIn("shell", json.loads(seen[1].content))
