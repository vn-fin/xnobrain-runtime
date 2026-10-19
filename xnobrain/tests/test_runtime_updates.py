"""FT0002 Runtime consumer lifecycle and narrow tool tests."""

from __future__ import annotations

import asyncio
import gc
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from xnobrain.handlers.operations.runtime_updates import operations
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
        # A sqlite connection's context manager commits but does not close it.
        # Keep no abandoned WAL connection whose later GC changes the manifest.
        with closing(sqlite3.connect(database)) as connection, connection:
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

    async def test_checkpoint_receipt_preserves_full_evidence_and_replay(self):
        # Enough file evidence to exceed the old gateway's 256 KiB limit.
        for index in range(1500):
            name = f"{index:04d}-" + "x" * 160
            (self.data / "durable" / name).write_text("fixture", encoding="utf-8")
        await self.call("drain", request(deadline_seconds=0))
        handler = SimpleNamespace(service=SimpleNamespace(runtime_updates=self.service))
        http_request = SimpleNamespace(headers={"x-xnobrain-update-token": "update-service-token"})
        invoke, _, _ = operations(handler, http_request, request())["runtime_update_checkpoint"]

        receipt = await invoke()
        checkpoint_id = receipt["checkpoint_id"]
        persisted = self.service.repository.checkpoint(checkpoint_id)
        self.assertGreater(len(json.dumps(persisted).encode()), 256 * 1024)
        self.assertLess(len(json.dumps(receipt).encode()), 2048)
        self.assertNotIn("entries", receipt["manifest"])
        self.assertNotIn("sqlite_checkpoints", receipt)
        self.assertIn("data/state.db", persisted["sqlite_checkpoints"])
        self.assertEqual(receipt["manifest"]["digest"], persisted["manifest"]["digest"])
        self.assertEqual(receipt, await invoke())
        self.assertEqual(self.service.repository.checkpoint(checkpoint_id), persisted)
        self.assertEqual(
            self.service.repository.operation("upd_fixture")["steps"]["checkpoint"], persisted
        )
        verified = await self.call("post_verify", request(checkpoint_id=checkpoint_id))
        self.assertTrue(verified["data_preserved"])

    async def test_checkpoint_handles_sqlite_removing_wal_sidecars_during_flush(self):
        database = self.data / "state.db"
        # Leave committed WAL state with no live writer, as after a process exit.
        # Closing the checkpoint connection will remove both WAL and SHM files.
        writer = """
import os
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
connection.execute("UPDATE state SET value='committed in WAL'")
connection.commit()
os._exit(0)
"""
        subprocess.run([sys.executable, "-c", writer, str(database)], check=True, timeout=10)
        sidecars = [Path(str(database) + suffix) for suffix in ("-wal", "-shm")]
        self.assertTrue(all(path.is_file() for path in sidecars))

        await self.call("drain", request(deadline_seconds=0))
        checkpoint = await self.call("checkpoint", request())

        self.assertIn("data/state.db", checkpoint["sqlite_checkpoints"])
        # The final read-only manifest may recreate WAL/SHM coordination files.
        # Their lifecycle must neither lose committed rows nor block checkpoint.
        self.assertTrue(self.service.dispatch_paused)
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            self.assertEqual(
                connection.execute("SELECT value FROM state").fetchall(),
                [("committed in WAL",)],
            )
        replay = await self.call("checkpoint", request())
        self.assertEqual(replay["checkpoint_id"], checkpoint["checkpoint_id"])
        verified = await self.call(
            "post_verify", request(checkpoint_id=checkpoint["checkpoint_id"])
        )
        self.assertTrue(verified["data_preserved"])
        self.assertTrue(self.service.dispatch_paused)
        resumed = await self.call("resume", request())
        self.assertTrue(resumed["resumed"])
        self.assertFalse(self.service.dispatch_paused)

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
        gc.collect()  # Unrelated object collection cannot alter durable fixture files.
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
        with self.assertLogs("xnobrain.services.runtime_updates", level="WARNING") as logs:
            with self.assertRaises(ServiceError) as failed:
                await self.call("post_verify", request(checkpoint_id=checkpoint["checkpoint_id"]))
        self.assertEqual(failed.exception.code, "runtime_update_data_not_preserved")
        record = logs.records[0]
        self.assertEqual(record.event, "runtime_update_verification_failure")
        self.assertEqual((record.data_added, record.data_removed, record.data_changed), (0, 0, 1))
        self.assertTrue(record.installed_identity_matches)
        self.assertNotIn("memory.txt", str(record.__dict__))
        self.assertNotIn("preserve me", str(record.__dict__))
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

    async def test_wrong_installed_identity_has_distinct_verification_error(self):
        await self.call("drain", request(deadline_seconds=0))
        checkpoint = await self.call("checkpoint", request())
        with patch.object(
            self.service, "_installed_identity", return_value={**TARGET, "source_commit": "c" * 40}
        ):
            with self.assertRaises(ServiceError) as failed:
                await self.call("post_verify", request(checkpoint_id=checkpoint["checkpoint_id"]))
        self.assertEqual(failed.exception.code, "runtime_update_identity_mismatch")
        evidence = self.service.repository.operation("upd_fixture")["steps"]["post_verify"]
        self.assertTrue(evidence["data_preserved"])
        self.assertFalse(evidence["installed_identity_matches"])
        self.assertTrue(self.service.dispatch_paused)
        with self.assertRaises(ServiceError) as cannot_resume:
            await self.call("resume", request())
        self.assertEqual(cannot_resume.exception.code, "runtime_update_not_verified")

    async def test_failed_verify_retries_original_checkpoint_then_resumes(self):
        log = self.big_brother / "logs" / "agent.log"
        log.parent.mkdir()
        log.write_bytes(b"before restart\n")
        await self.call("drain", request(deadline_seconds=0))
        checkpoint = await self.call("checkpoint", request())
        data = self.data / "durable" / "memory.txt"
        data.write_text("changed", encoding="utf-8")
        with self.assertRaises(ServiceError):
            await self.call("post_verify", request(checkpoint_id=checkpoint["checkpoint_id"]))
        self.assertTrue(self.service.dispatch_paused)
        data.write_text("preserve me", encoding="utf-8")
        log.write_bytes(b"before restart\nnew startup log\n")
        verified = await self.call(
            "post_verify", request(checkpoint_id=checkpoint["checkpoint_id"])
        )
        self.assertTrue(verified["verified"])
        # Runtime-owned profile logs are outside the v3 durable manifest.
        self.assertEqual(verified["data_verification"]["manifest_version"], 3)
        self.assertEqual(verified["data_verification"]["changes"]["log_appends"], 0)
        self.assertEqual(
            self.service.repository.checkpoint(checkpoint["checkpoint_id"]), checkpoint
        )
        await self.call("resume", request())
        self.assertFalse(self.service.dispatch_paused)

    async def test_auth_fence_layout_and_unsafe_sidecar_fail_closed(self):
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
        (self.data / "unsafe.db").write_bytes(b"")
        (self.data / "unsafe.db-wal").symlink_to("/tmp")
        with (
            self.assertLogs("xnobrain.services.runtime_updates", level="WARNING") as logs,
            self.assertRaises(ServiceError) as unsafe,
        ):
            await self.call("checkpoint", request())
        self.assertEqual(unsafe.exception.code, "runtime_update_unsafe_data")
        self.assertEqual(unsafe.exception.status, 409)
        record = logs.records[0]
        self.assertEqual(record.event, "runtime_update_storage_failure")
        self.assertEqual(record.operation_id, "upd_fixture")
        self.assertEqual(record.fence, 3)
        self.assertEqual(record.phase, "checkpoint")
        self.assertEqual(record.storage_check, "sqlite_sidecar_symlink")
        self.assertEqual(record.storage_root, "data")
        self.assertEqual(record.relative_path, "unsafe.db-wal")
        self.assertNotIn(str(self.data), str(record.__dict__))
        self.assertTrue(self.service.dispatch_paused)
        self.assertNotIn("checkpoint", self.service.repository.operation("upd_fixture")["steps"])

    async def test_checkpoint_logs_invalid_lock_without_its_contents(self):
        await self.call("drain", request(deadline_seconds=0))
        (self.data / ".custom-page-storage.lock").write_text("PRIVATE_LOCK_CONTENT")
        with (
            self.assertLogs("xnobrain.services.runtime_updates", level="WARNING") as logs,
            self.assertRaises(ServiceError) as unsafe,
        ):
            await self.call("checkpoint", request())
        self.assertEqual(unsafe.exception.code, "runtime_update_unsafe_data")
        self.assertEqual(logs.records[0].storage_check, "coordination_file")
        self.assertEqual(logs.records[0].relative_path, ".custom-page-storage.lock")
        self.assertNotIn("PRIVATE_LOCK_CONTENT", str(logs.records[0].__dict__))
        self.assertNotIn("update-service-token", str(logs.records[0].__dict__))
        self.assertTrue(self.service.dispatch_paused)

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
                        # Both adapters expose the same compact receipt. The
                        # service's persisted checkpoint remains private.
                        checkpoint = {
                            "checkpoint_id": "ckp_" + "c" * 64,
                            "operation_id": "upd_fixture",
                            "generation": 3,
                            "created_at": "2026-10-19T00:00:00Z",
                            "manifest": {
                                "digest": "d" * 64,
                                "file_count": 2000,
                                "total_bytes": 10000,
                                "entries": [{"path": "private-file"}] * 2000,
                            },
                            "sqlite_checkpoints": ["private.db"],
                        }
                        targets = {
                            "": TARGET,
                            "v2/": {
                                "kind": "incus_image",
                                "fingerprint": "f" * 64,
                                "build_descriptor_digest": "sha256:" + "a" * 64,
                                "version": "2.1.0",
                                "source_commit": "a" * 40,
                                "data_schema": 1,
                            },
                        }
                        for prefix, target in targets.items():
                            with patch.object(
                                composition.service.runtime_updates,
                                "checkpoint",
                                new_callable=AsyncMock,
                                return_value=checkpoint,
                            ) as create:
                                payload = request(target=target)
                                response = await client.post(
                                    f"/xnobrain/api/runtime/v1/system/update/{prefix}checkpoint",
                                    json=payload,
                                    headers={"x-xnobrain-update-token": "service-token"},
                                )
                                self.assertEqual(response.status_code, 200, response.text)
                                self.assertTrue(response.json()["success"])
                                receipt = response.json()["data"]
                                self.assertEqual(
                                    receipt["checkpoint_id"], checkpoint["checkpoint_id"]
                                )
                                self.assertNotIn("entries", receipt["manifest"])
                                self.assertNotIn("sqlite_checkpoints", receipt)
                                self.assertLess(len(response.content), 2048)
                                create.assert_awaited_once_with(
                                    payload, update_token="service-token"
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
