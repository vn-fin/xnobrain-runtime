"""Private workload identity resolution and owner-bound grant cache binding."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import httpx

from xnobrain.integrations import control_mcp_grants, workload_identity
from xnobrain.integrations.control_mcp_grants import WorkspaceMcpGrantClient
from xnobrain.integrations.workload_identity import (
    WorkloadIdentityError,
    resolve_workload_identity,
)

CONTROL = "https://control.test"
USER = "user-1"
TENANT = "personal-user-1"


def _write_identity(path: Path, *, token="workload-token", **overrides):
    value = {
        "version": 1,
        "workspace_id": "workspace-1",
        "workload_id": "workload-1",
        "user_id": USER,
        "tenant_id": TENANT,
        "token": token,
    }
    value.update(overrides)
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)
    return value


class WorkloadIdentityResolverTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.default_patch = mock.patch.object(
            workload_identity,
            "DEFAULT_IDENTITY_PATH",
            str(self.root / "missing-default.json"),
        )
        self.default_patch.start()
        self.addCleanup(self.default_patch.stop)

    def _resolve(self, env):
        return resolve_workload_identity(
            expected_user_id=USER,
            expected_tenant_id=TENANT,
            environ=env,
        )

    def test_complete_managed_identity_takes_precedence(self):
        path = self.root / "identity.json"
        _write_identity(path, token="file-token", workspace_id="file-workspace")
        identity = self._resolve(
            {
                "RUNTIME_WORKLOAD_IDENTITY_FILE": str(path),
                "RUNTIME_WORKSPACE_ID": "managed-workspace",
                "RUNTIME_WORKLOAD_ID": "managed-workload",
                "RUNTIME_WORKLOAD_TOKEN": "managed-token",
                "RUNTIME_WORKSPACE_OWNER_TENANT_ID": TENANT,
            }
        )
        self.assertEqual(identity.kind, "managed")
        self.assertEqual(identity.workspace_id, "managed-workspace")
        self.assertEqual(identity.workload_id, "managed-workload")
        self.assertEqual(identity.token, "managed-token")
        self.assertEqual(identity.user_id, USER)
        self.assertNotIn("managed-token", repr(identity))

    def test_partial_managed_env_fails_closed_even_with_file(self):
        path = self.root / "identity.json"
        _write_identity(path)
        for env in (
            {"RUNTIME_WORKLOAD_ID": "workload-1"},
            {"RUNTIME_WORKLOAD_TOKEN": "token"},
            {"RUNTIME_WORKSPACE_OWNER_TENANT_ID": TENANT},
            {
                "RUNTIME_WORKSPACE_ID": "workspace-1",
                "RUNTIME_WORKLOAD_ID": "workload-1",
            },
        ):
            with self.subTest(env=env):
                with self.assertRaises(WorkloadIdentityError) as raised:
                    self._resolve({**env, "RUNTIME_WORKLOAD_IDENTITY_FILE": str(path)})
                self.assertEqual(raised.exception.reason, "managed_identity_partial")

    def test_managed_values_reject_control_characters_and_oversize(self):
        base = {
            "RUNTIME_WORKSPACE_ID": "managed-workspace",
            "RUNTIME_WORKLOAD_ID": "managed-workload",
            "RUNTIME_WORKLOAD_TOKEN": "managed-token",
            "RUNTIME_WORKSPACE_OWNER_TENANT_ID": TENANT,
        }
        for field, value in (
            ("RUNTIME_WORKLOAD_TOKEN", "bad\ntoken"),
            ("RUNTIME_WORKLOAD_ID", "bad\x00id"),
            ("RUNTIME_WORKSPACE_ID", "x" * 513),
            ("RUNTIME_WORKSPACE_OWNER_TENANT_ID", "bad tenant"),
        ):
            with self.subTest(field=field):
                with self.assertRaises(WorkloadIdentityError) as raised:
                    self._resolve({**base, field: value})
                self.assertEqual(raised.exception.reason, "managed_identity_malformed")

    def test_legacy_values_reject_control_characters(self):
        for field, value in (
            ("RUNTIME_WORKSPACE_ID", "bad\nworkspace"),
            ("RUNTIME_INTERNAL_SERVICE_TOKEN", "bad\x00token"),
        ):
            with self.subTest(field=field):
                env = {
                    "RUNTIME_WORKSPACE_ID": "legacy-workspace",
                    "RUNTIME_INTERNAL_SERVICE_TOKEN": "legacy-token",
                    field: value,
                }
                with self.assertRaises(WorkloadIdentityError) as raised:
                    self._resolve(env)
                self.assertEqual(raised.exception.reason, "legacy_identity_malformed")

    def test_source_file_matches_owner_and_hides_token(self):
        path = self.root / "identity.json"
        _write_identity(path, token="source-token")
        identity = self._resolve({"RUNTIME_WORKLOAD_IDENTITY_FILE": str(path)})
        self.assertEqual(identity.kind, "source")
        self.assertEqual(identity.token, "source-token")
        self.assertEqual(identity.cache_fingerprint[3:5], (USER, TENANT))
        self.assertNotIn("source-token", repr(identity))

    def test_source_file_owner_mismatch_fails(self):
        path = self.root / "identity.json"
        for overrides in (
            {"user_id": "other-user"},
            {"tenant_id": "other-tenant"},
        ):
            with self.subTest(overrides=overrides):
                _write_identity(path, **overrides)
                with self.assertRaises(WorkloadIdentityError) as raised:
                    self._resolve({"RUNTIME_WORKLOAD_IDENTITY_FILE": str(path)})
                self.assertEqual(raised.exception.reason, "identity_owner_mismatch")

    def test_non_regular_fifo_directory_and_device_fail_promptly(self):
        fifo = self.root / "identity.fifo"
        os.mkfifo(fifo, 0o600)
        script = (
            "import sys\n"
            "from xnobrain.integrations.workload_identity import "
            "WorkloadIdentityError, resolve_workload_identity\n"
            "try:\n"
            "    resolve_workload_identity(expected_user_id='user-1', "
            "expected_tenant_id='personal-user-1')\n"
            "except WorkloadIdentityError as error:\n"
            "    print(error.reason)\n"
            "    raise SystemExit(3)\n"
            "raise SystemExit(0)\n"
        )
        try:
            completed = subprocess.run(
                [sys.executable, "-c", script],
                env={
                    **os.environ,
                    "RUNTIME_WORKLOAD_IDENTITY_FILE": str(fifo),
                },
                capture_output=True,
                text=True,
                timeout=3,
            )
        finally:
            fifo.unlink()
        self.assertEqual(completed.returncode, 3, completed.stderr)
        self.assertIn("identity_not_regular", completed.stdout)

        directory = self.root / "identity-dir"
        directory.mkdir()
        with self.assertRaises(WorkloadIdentityError) as raised:
            self._resolve({"RUNTIME_WORKLOAD_IDENTITY_FILE": str(directory)})
        self.assertEqual(raised.exception.reason, "identity_not_regular")

        with self.assertRaises(WorkloadIdentityError) as raised:
            self._resolve({"RUNTIME_WORKLOAD_IDENTITY_FILE": "/dev/null"})
        self.assertEqual(raised.exception.reason, "identity_not_regular")

    def test_source_file_permissions_symlink_size_and_version_fail(self):
        path = self.root / "identity.json"
        _write_identity(path)
        path.chmod(0o644)
        with self.assertRaises(WorkloadIdentityError) as raised:
            self._resolve({"RUNTIME_WORKLOAD_IDENTITY_FILE": str(path)})
        self.assertEqual(raised.exception.reason, "identity_permissions_invalid")

        target = self.root / "target.json"
        _write_identity(target)
        link = self.root / "link.json"
        link.symlink_to(target)
        with self.assertRaises(WorkloadIdentityError):
            self._resolve({"RUNTIME_WORKLOAD_IDENTITY_FILE": str(link)})

        _write_identity(path, version=2)
        with self.assertRaises(WorkloadIdentityError) as raised:
            self._resolve({"RUNTIME_WORKLOAD_IDENTITY_FILE": str(path)})
        self.assertEqual(raised.exception.reason, "identity_version_invalid")

        path.write_text("x" * (workload_identity.MAX_IDENTITY_BYTES + 1))
        path.chmod(0o600)
        with self.assertRaises(WorkloadIdentityError) as raised:
            self._resolve({"RUNTIME_WORKLOAD_IDENTITY_FILE": str(path)})
        self.assertEqual(raised.exception.reason, "identity_size_invalid")

        path.write_text("{not json")
        path.chmod(0o600)
        with self.assertRaises(WorkloadIdentityError) as raised:
            self._resolve({"RUNTIME_WORKLOAD_IDENTITY_FILE": str(path)})
        self.assertEqual(raised.exception.reason, "identity_malformed")

    def test_explicit_path_must_be_absolute_and_normalized(self):
        for path in ("relative.json", "../workload-identity.json"):
            with self.subTest(path=path):
                with self.assertRaises(WorkloadIdentityError) as raised:
                    self._resolve({"RUNTIME_WORKLOAD_IDENTITY_FILE": path})
                self.assertEqual(raised.exception.reason, "identity_path_invalid")

    def test_explicit_missing_path_never_falls_back_to_legacy(self):
        with self.assertRaises(WorkloadIdentityError) as raised:
            self._resolve(
                {
                    "RUNTIME_WORKLOAD_IDENTITY_FILE": str(self.root / "missing.json"),
                    "RUNTIME_WORKSPACE_ID": "legacy-workspace",
                    "RUNTIME_INTERNAL_SERVICE_TOKEN": "legacy-token",
                }
            )
        self.assertEqual(raised.exception.reason, "identity_unavailable")

    def test_legacy_requires_workspace_id(self):
        identity = self._resolve(
            {
                "RUNTIME_WORKSPACE_ID": "legacy-workspace",
                "RUNTIME_INTERNAL_SERVICE_TOKEN": "legacy-token",
            }
        )
        self.assertEqual(identity.kind, "legacy")
        self.assertEqual(identity.token, "legacy-token")
        with self.assertRaises(WorkloadIdentityError) as raised:
            self._resolve({"RUNTIME_INTERNAL_SERVICE_TOKEN": "shared-static-token"})
        self.assertEqual(raised.exception.reason, "identity_unavailable")


class WorkloadIdentityGrantTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        control_mcp_grants._CACHE.clear()
        self.addCleanup(control_mcp_grants._CACHE.clear)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.default_path = self.root / "default-identity.json"
        self.default_patch = mock.patch.object(
            workload_identity,
            "DEFAULT_IDENTITY_PATH",
            str(self.default_path),
        )
        self.default_patch.start()
        self.addCleanup(self.default_patch.stop)

    def _client(self, responder):
        http = httpx.AsyncClient(transport=httpx.MockTransport(responder), follow_redirects=False)
        self.addAsyncCleanup(http.aclose)
        return WorkspaceMcpGrantClient(http)

    @staticmethod
    def _response(*, grant="grant-one", user_id=USER, tenant_id=TENANT):
        return httpx.Response(
            200,
            json={
                "grant": grant,
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30))
                .isoformat()
                .replace("+00:00", "Z"),
                "user_id": user_id,
                "tenant_id": tenant_id,
            },
        )

    async def test_identity_rotation_invalidates_cached_grant(self):
        path = self.root / "identity.json"
        _write_identity(path, token="token-one")
        seen = []

        def respond(request):
            seen.append(request)
            return self._response(grant=f"grant-{len(seen)}")

        client = self._client(respond)
        env = {
            "RUNTIME_CONTROL_URL": CONTROL,
            "RUNTIME_WORKLOAD_IDENTITY_FILE": str(path),
        }
        with mock.patch.dict(os.environ, env, clear=True):
            first = await client.grant(
                "profile", agent_id="agent-1", platform="zalo", user_id=USER, tenant_id=TENANT
            )
            cached = await client.grant(
                "profile", agent_id="agent-1", platform="zalo", user_id=USER, tenant_id=TENANT
            )
            _write_identity(path, token="token-two")
            rotated = await client.grant(
                "profile", agent_id="agent-1", platform="zalo", user_id=USER, tenant_id=TENANT
            )
            path.unlink()
            deleted = await client.grant(
                "profile", agent_id="agent-1", platform="zalo", user_id=USER, tenant_id=TENANT
            )

        self.assertEqual((first, cached, rotated, deleted), ("grant-1", "grant-1", "grant-2", ""))
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0].headers["Authorization"], "Bearer token-one")
        self.assertEqual(seen[1].headers["Authorization"], "Bearer token-two")
        self.assertEqual(seen[0].headers["X-XNOBrain-Workspace-ID"], "workspace-1")

    async def test_source_to_legacy_failed_refresh_cannot_reuse_source_grant(self):
        _write_identity(self.default_path, token="source-token")
        seen = []

        def respond(_request):
            seen.append(1)
            if len(seen) == 1:
                return self._response(grant="source-grant")
            return httpx.Response(503, json={"code": "grants_not_ready"})

        client = self._client(respond)
        env = {
            "RUNTIME_CONTROL_URL": CONTROL,
            "RUNTIME_WORKSPACE_ID": "workspace-1",
            "RUNTIME_INTERNAL_SERVICE_TOKEN": "legacy-token",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            first = await client.grant(
                "profile",
                agent_id="agent-1",
                platform="zalo",
                user_id=USER,
                tenant_id=TENANT,
            )
            self.default_path.unlink()
            second = await client.grant(
                "profile",
                agent_id="agent-1",
                platform="zalo",
                user_id=USER,
                tenant_id=TENANT,
            )

        self.assertEqual((first, second), ("source-grant", ""))
        self.assertEqual(len(seen), 2)

    async def test_token_rotation_failed_refresh_cannot_reuse_old_grant(self):
        path = self.root / "identity.json"
        _write_identity(path, token="token-one")
        seen = []

        def respond(_request):
            seen.append(1)
            if len(seen) == 1:
                return self._response(grant="old-grant")
            return httpx.Response(503, json={"code": "grants_not_ready"})

        client = self._client(respond)
        env = {
            "RUNTIME_CONTROL_URL": CONTROL,
            "RUNTIME_WORKLOAD_IDENTITY_FILE": str(path),
        }
        with mock.patch.dict(os.environ, env, clear=True):
            first = await client.grant(
                "profile",
                agent_id="agent-1",
                platform="zalo",
                user_id=USER,
                tenant_id=TENANT,
            )
            _write_identity(path, token="token-two")
            second = await client.grant(
                "profile",
                agent_id="agent-1",
                platform="zalo",
                user_id=USER,
                tenant_id=TENANT,
            )

        self.assertEqual((first, second), ("old-grant", ""))
        self.assertEqual(len(seen), 2)

    async def test_malformed_or_owner_mismatch_never_reuses_cache(self):
        path = self.root / "identity.json"
        _write_identity(path, token="token-one")
        seen = []

        def respond(_request):
            seen.append(1)
            return self._response()

        client = self._client(respond)
        env = {
            "RUNTIME_CONTROL_URL": CONTROL,
            "RUNTIME_WORKLOAD_IDENTITY_FILE": str(path),
        }
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                await client.grant(
                    "profile", agent_id="agent-1", platform="zalo", user_id=USER, tenant_id=TENANT
                ),
                "grant-one",
            )
            path.write_text("{bad")
            path.chmod(0o600)
            self.assertEqual(
                await client.grant(
                    "profile", agent_id="agent-1", platform="zalo", user_id=USER, tenant_id=TENANT
                ),
                "",
            )
            _write_identity(path, user_id="other-user")
            self.assertEqual(
                await client.grant(
                    "profile", agent_id="agent-1", platform="zalo", user_id=USER, tenant_id=TENANT
                ),
                "",
            )
        self.assertEqual(len(seen), 1)

    async def test_complete_managed_env_precedes_file_and_legacy(self):
        path = self.root / "identity.json"
        _write_identity(path, token="file-token", workspace_id="file-workspace")
        seen = []

        def respond(request):
            seen.append(request)
            return self._response()

        client = self._client(respond)
        env = {
            "RUNTIME_CONTROL_URL": CONTROL,
            "RUNTIME_WORKLOAD_IDENTITY_FILE": str(path),
            "RUNTIME_WORKSPACE_ID": "managed-workspace",
            "RUNTIME_WORKLOAD_ID": "managed-workload",
            "RUNTIME_WORKLOAD_TOKEN": "managed-token",
            "RUNTIME_WORKSPACE_OWNER_TENANT_ID": TENANT,
            "RUNTIME_INTERNAL_SERVICE_TOKEN": "legacy-token",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                await client.grant(
                    "profile", agent_id="agent-1", platform="zalo", user_id=USER, tenant_id=TENANT
                ),
                "grant-one",
            )
        self.assertEqual(seen[0].headers["Authorization"], "Bearer managed-token")
        self.assertEqual(seen[0].headers["X-XNOBrain-Workspace-ID"], "managed-workspace")

    async def test_partial_managed_env_and_legacy_without_workspace_fail(self):
        path = self.root / "identity.json"
        _write_identity(path)
        seen = []

        def respond(_request):
            seen.append(1)
            return self._response()

        client = self._client(respond)
        for env in (
            {
                "RUNTIME_CONTROL_URL": CONTROL,
                "RUNTIME_WORKLOAD_IDENTITY_FILE": str(path),
                "RUNTIME_WORKLOAD_ID": "partial",
            },
            {
                "RUNTIME_CONTROL_URL": CONTROL,
                "RUNTIME_INTERNAL_SERVICE_TOKEN": "shared-static-token",
            },
        ):
            with self.subTest(env=env):
                with mock.patch.dict(os.environ, env, clear=True):
                    self.assertEqual(
                        await client.grant(
                            "profile",
                            agent_id="agent-1",
                            platform="zalo",
                            user_id=USER,
                            tenant_id=TENANT,
                        ),
                        "",
                    )
        self.assertEqual(seen, [])

    async def test_legacy_workspace_bearer_still_works(self):
        seen = []

        def respond(request):
            seen.append(request)
            return self._response()

        client = self._client(respond)
        env = {
            "RUNTIME_CONTROL_URL": CONTROL,
            "RUNTIME_WORKSPACE_ID": "legacy-workspace",
            "RUNTIME_INTERNAL_SERVICE_TOKEN": "legacy-token",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                await client.grant(
                    "profile", agent_id="agent-1", platform="zalo", user_id=USER, tenant_id=TENANT
                ),
                "grant-one",
            )
        self.assertEqual(seen[0].headers["Authorization"], "Bearer legacy-token")
        self.assertEqual(seen[0].headers["X-XNOBrain-Workspace-ID"], "legacy-workspace")


if __name__ == "__main__":
    unittest.main()
