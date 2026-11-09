"""Private source-owner probe: actor, proofs, conflicts, and status-only output."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from xnobrain.handlers.operations.source_owner import operations as probe_operations
from xnobrain.integrations import workload_identity
from xnobrain.models.source_owner import SourceOwnerProbeRequest
from xnobrain.repositories.files import FileRepository
from xnobrain.routes.definition import API_PREFIX
from xnobrain.routes.setup import setup_routes
from xnobrain.services.base import ServiceError
from xnobrain.services.source_owner import SourceOwnerService
from xnobrain.trusted_context import (
    TRUSTED_SIGNATURE_HEADER,
    TRUSTED_SUBJECT_HEADER,
    TRUSTED_TENANT_HEADER,
    TrustedRequestContext,
    principal_signature,
)

TOKEN = "source-owner-probe-test-token"
USER = "user-1"
TENANT = "personal-user-1"
PATH = f"{API_PREFIX}/internal/source-owner/probe"


def _body(**overrides):
    value = {
        "environment": "dev",
        "runtime_source_key": "source-key-1",
    }
    value.update(overrides)
    return value


def _identity(path: Path, **overrides):
    value = {
        "version": 1,
        "workspace_id": "workspace-1",
        "workload_id": "workload-1",
        "user_id": USER,
        "tenant_id": TENANT,
        "token": "secret-workload-token",
    }
    value.update(overrides)
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)


def _marker(profile: Path, **overrides):
    value = {"subject": USER, "tenant_id": TENANT, "organization_id": ""}
    value.update(overrides)
    profile.mkdir(parents=True, exist_ok=True)
    profile.joinpath(".community-profile-owner.json").write_text(
        json.dumps(value), encoding="utf-8"
    )


def _channel_owner(profile: Path, **overrides):
    value = {
        "user_id": USER,
        "tenant_id": TENANT,
        "organization_id": "",
        "agent_id": profile.name,
        "bound_at": 1,
    }
    value.update(overrides)
    directory = profile / ".xnobrain" / "channels"
    directory.mkdir(parents=True, exist_ok=True)
    directory.joinpath("owner.json").write_text(json.dumps(value), encoding="utf-8")


class _ProbeCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.data = root / "data"
        self.profiles = root / "agent"
        self.root_profile = self.profiles / "big-brother"
        self.root_profile.mkdir(parents=True)
        self.legacy_root = root / "legacy-agents"
        self.identity_path = root / "workload-identity.json"
        self.default_patch = mock.patch.object(
            workload_identity,
            "DEFAULT_IDENTITY_PATH",
            str(root / "missing-default.json"),
        )
        self.default_patch.start()
        self.addCleanup(self.default_patch.stop)
        self.environment = mock.patch.dict(
            os.environ,
            {
                "RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": "",
                "RUNTIME_INTERNAL_SERVICE_TOKEN": TOKEN,
                "RUNTIME_WORKLOAD_IDENTITY_FILE": "",
                "RUNTIME_SOURCE_OWNER_USER_ID": "",
                "RUNTIME_SOURCE_OWNER_TENANT_ID": "",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.repository = FileRepository(self.data, self.profiles, root_profile=self.root_profile)
        self.platform = SimpleNamespace(
            repository=self.repository,
            config=SimpleNamespace(root_profile=self.root_profile),
            agents=SimpleNamespace(
                root_profile=self.root_profile,
                profiles_root=self.profiles,
                legacy_agents_root=self.legacy_root,
            ),
        )
        self.service = SourceOwnerService(self.platform)
        self.trusted = TrustedRequestContext(
            subject=USER,
            tenant_id=TENANT,
            ownership_context={"id": "personal", "owner_kind": "personal"},
        )

    def _probe(self, trusted=None):
        return self.service.probe(_body(), self.trusted if trusted is None else trusted)

    def _snapshot(self):
        result = {}
        for path in sorted(self.profiles.rglob("*")):
            if path.is_file() and not path.is_symlink():
                result[str(path.relative_to(self.profiles))] = path.read_bytes()
        return result


class SourceOwnerProofTests(_ProbeCase):
    async def test_identity_file_exact_match_is_status_only(self):
        _identity(self.identity_path)
        with mock.patch.dict(
            os.environ, {"RUNTIME_WORKLOAD_IDENTITY_FILE": str(self.identity_path)}
        ):
            result = self.service.probe(_body(), self.trusted)
        self.assertEqual(result, {"status": "match"})
        self.assertNotIn("secret-workload-token", json.dumps(result))

        _marker(self.profiles / "agent-1", subject="other-user")
        with mock.patch.dict(
            os.environ, {"RUNTIME_WORKLOAD_IDENTITY_FILE": str(self.identity_path)}
        ):
            self.assertEqual(self.service.probe(_body(), self.trusted), {"status": "mismatch"})

    async def test_identity_file_owner_mismatch_and_malformed_fail_closed(self):
        _identity(self.identity_path, user_id="other-user")
        with mock.patch.dict(
            os.environ, {"RUNTIME_WORKLOAD_IDENTITY_FILE": str(self.identity_path)}
        ):
            self.assertEqual(self.service.probe(_body(), self.trusted), {"status": "mismatch"})
        self.identity_path.write_text("{bad")
        self.identity_path.chmod(0o600)
        with mock.patch.dict(
            os.environ, {"RUNTIME_WORKLOAD_IDENTITY_FILE": str(self.identity_path)}
        ):
            self.assertEqual(self.service.probe(_body(), self.trusted), {"status": "mismatch"})

    async def test_attestation_is_minimal_bootstrap_and_exact(self):
        with mock.patch.dict(
            os.environ,
            {
                "RUNTIME_SOURCE_OWNER_USER_ID": USER,
                "RUNTIME_SOURCE_OWNER_TENANT_ID": TENANT,
            },
        ):
            self.assertEqual(self.service.probe(_body(), self.trusted), {"status": "match"})
        with mock.patch.dict(
            os.environ,
            {
                "RUNTIME_SOURCE_OWNER_USER_ID": "other-user",
                "RUNTIME_SOURCE_OWNER_TENANT_ID": TENANT,
            },
        ):
            self.assertEqual(self.service.probe(_body(), self.trusted), {"status": "mismatch"})
        with mock.patch.dict(os.environ, {"RUNTIME_SOURCE_OWNER_USER_ID": USER}):
            self.assertEqual(
                self.service.probe(_body(), self.trusted),
                {"status": "legacy_unproven"},
            )

    async def test_matching_marker_alone_is_not_proof(self):
        _marker(self.root_profile)
        self.assertEqual(
            self.service.probe(_body(), self.trusted),
            {"status": "legacy_unproven"},
        )
        with mock.patch.dict(
            os.environ,
            {
                "RUNTIME_SOURCE_OWNER_USER_ID": USER,
                "RUNTIME_SOURCE_OWNER_TENANT_ID": TENANT,
            },
        ):
            self.assertEqual(self.service.probe(_body(), self.trusted), {"status": "match"})

    async def test_mixed_malformed_and_channel_marker_conflicts_fail_closed(self):
        profile = self.profiles / "agent-1"
        _marker(profile)
        other = self.profiles / "agent-2"
        _marker(other, subject="other-user")
        with mock.patch.dict(
            os.environ,
            {
                "RUNTIME_SOURCE_OWNER_USER_ID": USER,
                "RUNTIME_SOURCE_OWNER_TENANT_ID": TENANT,
            },
        ):
            self.assertEqual(self.service.probe(_body(), self.trusted), {"status": "mismatch"})

            other.joinpath(".community-profile-owner.json").write_text("{bad")
            self.assertEqual(self.service.probe(_body(), self.trusted), {"status": "mismatch"})

            _marker(other, subject=USER, tenant_id=TENANT)
            _channel_owner(other, user_id="other-user")
            self.assertEqual(self.service.probe(_body(), self.trusted), {"status": "mismatch"})

    async def test_symlink_profile_marker_and_unknown_legacy(self):
        outside = Path(self.temporary.name) / "outside"
        _marker(outside)
        link = self.profiles / "linked-agent"
        link.symlink_to(outside, target_is_directory=True)
        with mock.patch.dict(
            os.environ,
            {
                "RUNTIME_SOURCE_OWNER_USER_ID": USER,
                "RUNTIME_SOURCE_OWNER_TENANT_ID": TENANT,
            },
        ):
            self.assertEqual(self.service.probe(_body(), self.trusted), {"status": "mismatch"})
        link.unlink()
        self.assertEqual(
            self.service.probe(_body(), self.trusted),
            {"status": "legacy_unproven"},
        )

        profile = self.profiles / "agent-1"
        _marker(profile)
        outside_owner = Path(self.temporary.name) / "outside-owner.json"
        outside_owner.write_text(
            json.dumps(
                {
                    "user_id": USER,
                    "tenant_id": TENANT,
                    "organization_id": "",
                    "agent_id": "agent-1",
                    "bound_at": 1,
                }
            ),
            encoding="utf-8",
        )
        channel_dir = profile / ".xnobrain" / "channels"
        channel_dir.mkdir(parents=True)
        channel_dir.joinpath("owner.json").symlink_to(outside_owner)
        with mock.patch.dict(
            os.environ,
            {
                "RUNTIME_SOURCE_OWNER_USER_ID": USER,
                "RUNTIME_SOURCE_OWNER_TENANT_ID": TENANT,
            },
        ):
            self.assertEqual(self.service.probe(_body(), self.trusted), {"status": "mismatch"})

    async def test_explicit_future_identity_path_allows_only_exact_attestation(self):
        future = Path(self.temporary.name) / "future" / "workload-identity.json"
        future.parent.mkdir()
        with mock.patch.dict(
            os.environ,
            {"RUNTIME_WORKLOAD_IDENTITY_FILE": str(future)},
        ):
            self.assertEqual(
                self.service.probe(_body(), self.trusted),
                {"status": "legacy_unproven"},
            )
        with mock.patch.dict(
            os.environ,
            {
                "RUNTIME_WORKLOAD_IDENTITY_FILE": str(future),
                "RUNTIME_SOURCE_OWNER_USER_ID": USER,
                "RUNTIME_SOURCE_OWNER_TENANT_ID": TENANT,
            },
        ):
            self.assertEqual(self.service.probe(_body(), self.trusted), {"status": "match"})
        self.assertFalse(future.exists())

    async def test_custom_root_uses_logical_big_brother_binding(self):
        root = self.profiles / "custom-root"
        root.mkdir()
        _channel_owner(root, agent_id="big-brother")
        platform = SimpleNamespace(
            repository=self.repository,
            config=SimpleNamespace(root_profile=root),
            agents=SimpleNamespace(
                root_profile=root,
                profiles_root=self.profiles,
                legacy_agents_root=self.legacy_root,
            ),
        )
        service = SourceOwnerService(platform)
        with mock.patch.dict(
            os.environ,
            {
                "RUNTIME_SOURCE_OWNER_USER_ID": USER,
                "RUNTIME_SOURCE_OWNER_TENANT_ID": TENANT,
            },
        ):
            self.assertEqual(service.probe(_body(), self.trusted), {"status": "match"})
            _channel_owner(root, agent_id="custom-root")
            self.assertEqual(service.probe(_body(), self.trusted), {"status": "mismatch"})

    async def test_legacy_profile_records_and_symlinks_are_included(self):
        legacy = self.legacy_root / "legacy-one" / ".profile"
        legacy.mkdir(parents=True)
        _marker(legacy)
        _channel_owner(legacy, agent_id="legacy-one")
        with mock.patch.dict(
            os.environ,
            {
                "RUNTIME_SOURCE_OWNER_USER_ID": USER,
                "RUNTIME_SOURCE_OWNER_TENANT_ID": TENANT,
            },
        ):
            self.assertEqual(self.service.probe(_body(), self.trusted), {"status": "match"})
            _marker(legacy, subject="other-user")
            self.assertEqual(self.service.probe(_body(), self.trusted), {"status": "mismatch"})

        shutil.rmtree(legacy)
        legacy.symlink_to(self.profiles / "outside-profile", target_is_directory=True)
        self.assertEqual(self.service.probe(_body(), self.trusted), {"status": "mismatch"})

    async def test_profile_scan_is_bounded_before_sorting(self):
        for index in range(512):
            (self.profiles / f"agent-{index:03d}").mkdir()
        with mock.patch.dict(
            os.environ,
            {
                "RUNTIME_SOURCE_OWNER_USER_ID": USER,
                "RUNTIME_SOURCE_OWNER_TENANT_ID": TENANT,
            },
        ):
            self.assertEqual(self.service.probe(_body(), self.trusted), {"status": "mismatch"})

    async def test_probe_never_writes_state(self):
        _identity(self.identity_path)
        before = self._snapshot()
        with mock.patch.dict(
            os.environ, {"RUNTIME_WORKLOAD_IDENTITY_FILE": str(self.identity_path)}
        ):
            self.service.probe(_body(), self.trusted)
        self.assertEqual(self._snapshot(), before)

    async def test_unsigned_or_organization_actor_is_rejected(self):
        with self.assertRaises(ServiceError) as raised:
            self.service.probe(_body(), TrustedRequestContext())
        self.assertEqual(raised.exception.status, 401)
        with self.assertRaises(ServiceError) as raised:
            self.service.probe(
                _body(),
                TrustedRequestContext(
                    subject=USER,
                    tenant_id=TENANT,
                    organization_id="org-1",
                ),
            )
        self.assertEqual(raised.exception.status, 403)


class _ProbeHandler:
    def __init__(self, service):
        self.service = SimpleNamespace(source_owner=service)

    async def dispatch(self, request, body):
        operation, message, status = probe_operations(self, request, body)["source_owner_probe"]
        try:
            data = operation()
        except ServiceError as error:
            return JSONResponse(
                {
                    "success": False,
                    "message": str(error),
                    "error": {"code": error.code},
                    "status_code": error.status,
                },
                status_code=error.status,
            )
        return JSONResponse(
            {"success": True, "data": data, "message": message, "status_code": status},
            status_code=status,
        )


class SourceOwnerRouteTests(_ProbeCase):
    def _app(self):
        app = FastAPI()
        setup_routes(app, _ProbeHandler(self.service))
        return app

    async def test_route_requires_signed_actor_and_returns_status_only(self):
        _identity(self.identity_path)
        app = self._app()
        env = {"RUNTIME_WORKLOAD_IDENTITY_FILE": str(self.identity_path)}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with mock.patch.dict(os.environ, env):
                forged = await client.post(PATH, json=_body())
                signed = await client.post(
                    PATH,
                    json=_body(),
                    headers={
                        TRUSTED_SUBJECT_HEADER: USER,
                        TRUSTED_TENANT_HEADER: TENANT,
                        TRUSTED_SIGNATURE_HEADER: principal_signature(TOKEN, USER, TENANT, ""),
                    },
                )
                extra = await client.post(
                    PATH,
                    json=_body(user_id=USER),
                )

        self.assertEqual(forged.status_code, 401)
        self.assertEqual(signed.status_code, 200)
        self.assertEqual(signed.json()["data"], {"status": "match"})
        self.assertNotIn("secret-workload-token", signed.text)
        self.assertNotIn("big-brother", signed.text)
        self.assertEqual(extra.status_code, 422)

    async def test_future_explicit_identity_route_uses_attestation_without_writes(self):
        future = Path(self.temporary.name) / "future" / "workload-identity.json"
        future.parent.mkdir()
        headers = {
            TRUSTED_SUBJECT_HEADER: USER,
            TRUSTED_TENANT_HEADER: TENANT,
            TRUSTED_SIGNATURE_HEADER: principal_signature(TOKEN, USER, TENANT, ""),
        }
        app = self._app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with mock.patch.dict(
                os.environ,
                {"RUNTIME_WORKLOAD_IDENTITY_FILE": str(future)},
            ):
                no_attestation = await client.post(PATH, json=_body(), headers=headers)
            with mock.patch.dict(
                os.environ,
                {
                    "RUNTIME_WORKLOAD_IDENTITY_FILE": str(future),
                    "RUNTIME_SOURCE_OWNER_USER_ID": USER,
                    "RUNTIME_SOURCE_OWNER_TENANT_ID": TENANT,
                },
            ):
                attestation = await client.post(PATH, json=_body(), headers=headers)

        self.assertEqual(no_attestation.json()["data"], {"status": "legacy_unproven"})
        self.assertEqual(attestation.json()["data"], {"status": "match"})
        self.assertFalse(future.exists())

    def test_probe_route_is_absent_from_public_openapi(self):
        app = self._app()
        self.assertNotIn("/internal/source-owner/probe", app.openapi()["paths"])

    def test_body_rejects_extra_authority_fields(self):
        for payload in (
            {"environment": "dev", "runtime_source_key": "key", "user_id": USER},
            {"environment": "dev", "runtime_source_key": "key", "tenant_id": TENANT},
            {"environment": "dev", "runtime_source_key": "bad key"},
            {"environment": "bad env", "runtime_source_key": "key"},
            {"environment": "dev"},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    SourceOwnerProbeRequest.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
