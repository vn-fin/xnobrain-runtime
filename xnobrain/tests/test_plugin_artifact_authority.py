"""Private plugin artifact authority: ownership, digest, approval, and egress."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from xnobrain.handlers.operations.plugin_artifact import (
    operations as authority_operations,
)
from xnobrain.integrations import workload_identity
from xnobrain.integrations.extension_policy import (
    load_state,
    save_state,
    tree_digest,
)
from xnobrain.models.plugin_artifact import PluginArtifactAuthorityRequest
from xnobrain.routes.definition import API_PREFIX
from xnobrain.routes.setup import setup_routes
from xnobrain.services.base import ServiceError
from xnobrain.services.plugin_artifact import PluginArtifactService
from xnobrain.services.source_owner import _ProbeConflict, read_json_file
from xnobrain.trusted_context import (
    TRUSTED_SIGNATURE_HEADER,
    TRUSTED_SUBJECT_HEADER,
    TRUSTED_TENANT_HEADER,
    TrustedRequestContext,
    principal_signature,
)

TOKEN = "plugin-artifact-authority-test-token"
USER = "user-1"
TENANT = "personal-user-1"
AGENT = "agent-1"
PLUGIN = "egress-plugin"
PATH = f"{API_PREFIX}/internal/plugin-artifact/authority"
VENDORED = Path("runtime/hermes-plugins/platforms/zalo_bot")


def _body(**overrides):
    value = {
        "agent_id": AGENT,
        "plugin": PLUGIN,
        "platform": PLUGIN,
        "artifact_digest": "sha256:" + "a" * 64,
    }
    value.update(overrides)
    return value


def _egress(**overrides):
    value = {
        "version": "plugin-egress-v1",
        "rules": [
            {
                "host": "api.example.test",
                "methods": ["post"],
                "path_template": "/v1/{endpoint}",
                "endpoints": ["send", "getMe"],
                "auth": {"kind": "bearer"},
            }
        ],
    }
    value.update(overrides)
    return value


def _marker(profile: Path, **overrides):
    value = {"subject": USER, "tenant_id": TENANT, "organization_id": ""}
    value.update(overrides)
    profile.mkdir(parents=True, exist_ok=True)
    profile.joinpath(".community-profile-owner.json").write_text(
        json.dumps(value), encoding="utf-8"
    )


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


class _Agents:
    def __init__(self, root: Path, profiles: Path, legacy: Path):
        self.root_profile = root
        self.profiles_root = profiles
        self.legacy_agents_root = legacy

    def profile_path(self, raw_name: str) -> Path:
        name = str(raw_name or "").strip()
        if name.casefold() in {"big-brother", "big brother", "default"}:
            if self.root_profile.is_dir():
                return self.root_profile.resolve()
            raise KeyError(name)
        native = self.profiles_root / name
        if native.is_dir() and not native.is_symlink():
            return native.resolve()
        legacy = self.legacy_agents_root / name / ".profile"
        if legacy.is_dir() and not legacy.is_symlink():
            return legacy.resolve()
        raise KeyError(name)


class _AuthorityCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.root = base / "root"
        self.root.mkdir()
        self.profiles = base / "profiles"
        self.profiles.mkdir()
        self.legacy = base / "legacy"
        self.legacy.mkdir()
        self.profile = self.profiles / AGENT
        self.profile.mkdir()
        self.identity_path = base / "workload-identity.json"
        self.default_patch = mock.patch.object(
            workload_identity,
            "DEFAULT_IDENTITY_PATH",
            str(base / "missing-default.json"),
        )
        self.default_patch.start()
        self.addCleanup(self.default_patch.stop)
        self.environment = mock.patch.dict(
            os.environ,
            {
                "RUNTIME_INTERNAL_SERVICE_TOKEN": TOKEN,
                "RUNTIME_WORKLOAD_IDENTITY_FILE": "",
                "RUNTIME_WORKSPACE_ID": "",
                "RUNTIME_WORKLOAD_ID": "",
                "RUNTIME_WORKLOAD_TOKEN": "",
                "RUNTIME_WORKSPACE_OWNER_TENANT_ID": "",
                "RUNTIME_SOURCE_OWNER_USER_ID": "",
                "RUNTIME_SOURCE_OWNER_TENANT_ID": "",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.agents = _Agents(self.root, self.profiles, self.legacy)
        self.service = PluginArtifactService(SimpleNamespace(agents=self.agents))
        self.trusted = TrustedRequestContext(
            subject=USER,
            tenant_id=TENANT,
            ownership_context={"id": "personal", "owner_kind": "personal"},
        )

    def _plugin(self, profile=None, name=PLUGIN, egress=None, kind="standalone"):
        profile = self.profile if profile is None else profile
        path = profile / "plugins" / name
        path.mkdir(parents=True, exist_ok=True)
        manifest = {"name": name, "version": "1.0.0", "kind": kind}
        if egress is not None:
            manifest["egress"] = egress
        path.joinpath("plugin.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
        )
        path.joinpath("__init__.py").write_text("def register(ctx):\n    pass\n", encoding="utf-8")
        return path

    def _approve(
        self,
        profile=None,
        name=PLUGIN,
        *,
        enabled=True,
        status="COMPLETED",
        verdict="safe",
        coverage="complete_text",
        approved=True,
        digest=None,
    ):
        profile = self.profile if profile is None else profile
        path = profile / "plugins" / name
        current = digest or tree_digest(path)
        save_state(
            profile,
            {
                "plugins": {
                    name: {
                        "revision": 1,
                        "source": "user",
                        "artifact_digest": current,
                        "scan": {
                            "status": status,
                            "verdict": verdict,
                            "coverage": coverage,
                            "findings": [],
                        },
                        "approval": {
                            "approved": approved,
                            "artifact_digest": current,
                        },
                    }
                },
                "hooks": {},
                "mcp": {},
                "revision": 1,
            },
        )
        config = profile / "config.yaml"
        value = yaml.safe_load(config.read_text(encoding="utf-8")) if config.is_file() else {}
        value = value if isinstance(value, dict) else {}
        value["plugins"] = {
            "enabled": [name] if enabled else [],
            "disabled": [] if enabled else [name],
        }
        config.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")

    def _call(self, trusted=None, **overrides):
        return self.service.authority(
            _body(**overrides), self.trusted if trusted is None else trusted
        )

    def _owned_digest(self, plugin=PLUGIN, profile=None):
        profile = self.profile if profile is None else profile
        _marker(profile)
        path = self._plugin(profile=profile, name=plugin, egress=_egress())
        self._approve(profile=profile, name=plugin)
        return f"sha256:{tree_digest(path)}"

    def _snapshot(self):
        result = {}
        for path in sorted(self.profiles.rglob("*")):
            if path.is_file() and not path.is_symlink():
                result[str(path.relative_to(self.profiles))] = path.read_bytes()
        return result


class PluginArtifactProofTests(_AuthorityCase):
    def test_request_model_rejects_extra_authority_fields(self):
        for payload in (
            {**_body(), "user_id": USER},
            {**_body(), "tenant_id": TENANT},
            {**_body(), "workspace_id": "workspace-1"},
            {**_body(), "rules": []},
            {**_body(), "artifact_digest": "not-a-digest"},
            {**_body(), "plugin": "Bad Plugin"},
            {k: v for k, v in _body().items() if k != "platform"},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    PluginArtifactAuthorityRequest.model_validate(payload)

    def test_actor_must_be_verified_personal(self):
        with self.assertRaises(ServiceError) as raised:
            self.service.authority(_body(), TrustedRequestContext())
        self.assertEqual(raised.exception.status, 401)
        with self.assertRaises(ServiceError) as raised:
            self.service.authority(
                _body(),
                TrustedRequestContext(
                    subject=USER,
                    tenant_id=TENANT,
                    organization_id="org-1",
                ),
            )
        self.assertEqual(raised.exception.status, 403)
        with self.assertRaises(ServiceError) as raised:
            self.service.authority(
                _body(),
                TrustedRequestContext(
                    subject=USER,
                    tenant_id=TENANT,
                    ownership_context={"owner_kind": "organization"},
                ),
            )
        self.assertEqual(raised.exception.status, 403)
        with self.assertRaises(ServiceError) as raised:
            self.service.authority(
                _body(),
                TrustedRequestContext(
                    subject=USER,
                    tenant_id=TENANT,
                    ownership_context={"owner_kind": "personal", "state": "revoked"},
                ),
            )
        self.assertEqual(raised.exception.status, 403)

    def test_named_agent_requires_exact_owner_marker(self):
        digest = f"sha256:{tree_digest(self._plugin(egress=_egress()))}"
        self._approve()
        self.assertEqual(self._call(artifact_digest=digest), {"status": "not_owned"})
        _marker(self.profile, subject="other-user")
        self.assertEqual(self._call(artifact_digest=digest), {"status": "not_owned"})
        _marker(self.profile, unexpected="field")
        self.assertEqual(self._call(artifact_digest=digest), {"status": "not_owned"})
        _marker(self.profile)
        self.assertEqual(self._call(artifact_digest=digest)["status"], "match")

    def test_missing_named_agent_is_not_owned(self):
        self.assertEqual(self._call(agent_id="missing"), {"status": "not_owned"})

    def test_root_requires_source_or_managed_identity_not_attestation(self):
        digest = self._owned_digest(profile=self.root)
        body = {"agent_id": "big-brother", "artifact_digest": digest}
        with mock.patch.dict(
            os.environ,
            {
                "RUNTIME_SOURCE_OWNER_USER_ID": USER,
                "RUNTIME_SOURCE_OWNER_TENANT_ID": TENANT,
            },
        ):
            self.assertEqual(self._call(**body), {"status": "not_owned"})
        with mock.patch.dict(os.environ, {"RUNTIME_WORKSPACE_ID": "workspace-1"}):
            self.assertEqual(self._call(**body), {"status": "not_owned"})

        _identity(self.identity_path)
        with mock.patch.dict(
            os.environ, {"RUNTIME_WORKLOAD_IDENTITY_FILE": str(self.identity_path)}
        ):
            self.assertEqual(self._call(**body)["status"], "match")

        _identity(self.identity_path, user_id="other-user")
        with mock.patch.dict(
            os.environ, {"RUNTIME_WORKLOAD_IDENTITY_FILE": str(self.identity_path)}
        ):
            self.assertEqual(self._call(**body), {"status": "not_owned"})

        with mock.patch.dict(
            os.environ,
            {
                "RUNTIME_WORKSPACE_ID": "workspace-1",
                "RUNTIME_WORKLOAD_ID": "workload-1",
                "RUNTIME_WORKLOAD_TOKEN": "managed-token",
                "RUNTIME_WORKSPACE_OWNER_TENANT_ID": TENANT,
                "RUNTIME_WORKLOAD_IDENTITY_FILE": str(self.identity_path),
            },
        ):
            self.assertEqual(self._call(**body)["status"], "match")

        with mock.patch.dict(
            os.environ,
            {
                "RUNTIME_WORKSPACE_ID": "workspace-1",
                "RUNTIME_WORKLOAD_ID": "workload-1",
                "RUNTIME_WORKLOAD_TOKEN": "",
                "RUNTIME_WORKSPACE_OWNER_TENANT_ID": TENANT,
            },
        ):
            self.assertEqual(self._call(**body), {"status": "not_owned"})

    def test_standalone_match_returns_canonical_rules(self):
        digest = self._owned_digest()
        rules = _egress(
            rules=[
                {
                    "host": "API.Example.Test",
                    "methods": ["get", "POST"],
                    "path_template": "/v1/{endpoint}",
                    "endpoints": ["send", "getMe"],
                    "auth": {"kind": "bearer"},
                },
                {
                    "host": "files.example.test",
                    "methods": ["get"],
                    "path_template": "/files/{endpoint}",
                    "endpoints": ["download"],
                    "auth": {"kind": "bearer"},
                },
            ]
        )
        self._plugin().joinpath("plugin.yaml").write_text(
            yaml.safe_dump(
                {"name": PLUGIN, "version": "1.0.0", "kind": "standalone", "egress": rules},
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        self._approve()
        digest = f"sha256:{tree_digest(self.profile / 'plugins' / PLUGIN)}"
        result = self._call(artifact_digest=digest)
        self.assertEqual(result["status"], "match")
        self.assertEqual(result["source"], "user")
        self.assertEqual(result["artifact_digest"], digest)
        self.assertEqual(
            result["rules"],
            {
                "version": "plugin-egress-v1",
                "rules": [
                    {
                        "host": "api.example.test",
                        "methods": ["GET", "POST"],
                        "path_template": "/v1/{endpoint}",
                        "endpoints": ["getMe", "send"],
                        "auth": {
                            "kind": "bearer",
                            "header": "Authorization",
                            "prefix": "Bearer ",
                        },
                    },
                    {
                        "host": "files.example.test",
                        "methods": ["GET"],
                        "path_template": "/files/{endpoint}",
                        "endpoints": ["download"],
                        "auth": {
                            "kind": "bearer",
                            "header": "Authorization",
                            "prefix": "Bearer ",
                        },
                    },
                ],
            },
        )

    def test_path_and_header_auth_are_canonical(self):
        _marker(self.profile)
        cases = [
            (
                {
                    "kind": "path",
                },
                "/bot{token}/{endpoint}",
                {"kind": "path"},
            ),
            (
                {
                    "kind": "header",
                    "header": "X-Api-Key",
                    "prefix": "Token ",
                },
                "/v1/{endpoint}",
                {"kind": "header", "header": "X-Api-Key", "prefix": "Token "},
            ),
            (
                {"kind": "bearer", "prefix": "Custom "},
                "/v1/{endpoint}",
                {"kind": "bearer", "header": "Authorization", "prefix": "Custom "},
            ),
        ]
        for auth, template, expected in cases:
            with self.subTest(kind=auth["kind"]):
                self._plugin(
                    egress=_egress(
                        rules=[
                            {
                                "host": "api.example.test",
                                "methods": ["POST"],
                                "path_template": template,
                                "endpoints": ["send", "getMe"],
                                "auth": auth,
                            }
                        ]
                    )
                )
                self._approve()
                digest = f"sha256:{tree_digest(self.profile / 'plugins' / PLUGIN)}"
                result = self._call(artifact_digest=digest)
                self.assertEqual(result["status"], "match")
                self.assertEqual(result["rules"]["rules"][0]["auth"], expected)

    def test_digest_change_and_cache_are_stale(self):
        digest = self._owned_digest()
        self.assertEqual(self._call(artifact_digest=digest)["status"], "match")
        self.profile.joinpath("plugins", PLUGIN, "__init__.py").write_text(
            "def register(ctx):\n    return 1\n", encoding="utf-8"
        )
        self.assertEqual(self._call(artifact_digest=digest), {"status": "stale"})

        self._approve()
        cache = self.profile / "plugins" / PLUGIN / "__pycache__"
        cache.mkdir()
        cache.joinpath("__init__.cpython-311.pyc").write_bytes(b"cache")
        current = f"sha256:{tree_digest(self.profile / 'plugins' / PLUGIN)}"
        self.assertEqual(self._call(artifact_digest=current)["status"], "stale")

    def test_unapproved_disabled_and_bad_scan_fail_closed(self):
        digest = self._owned_digest()
        self.assertEqual(self._call(artifact_digest=digest)["status"], "match")
        self._approve(approved=False)
        self.assertEqual(self._call(artifact_digest=digest), {"status": "not_approved"})
        self._approve(enabled=False)
        self.assertEqual(self._call(artifact_digest=digest), {"status": "not_approved"})
        self._approve(verdict="dangerous")
        self.assertEqual(self._call(artifact_digest=digest), {"status": "not_approved"})
        self._approve(status="BLOCKED")
        self.assertEqual(self._call(artifact_digest=digest), {"status": "not_approved"})
        (self.profile / ".xnobrain" / "extensions" / "state.json").unlink()
        self.assertEqual(self._call(artifact_digest=digest), {"status": "not_approved"})

    def test_complete_caution_scan_is_admitted(self):
        digest = self._owned_digest()
        self._approve(verdict="caution")
        self.assertEqual(self._call(artifact_digest=digest)["status"], "match")

    def test_stale_inventory_digest_denied_even_when_approval_matches(self):
        digest = self._owned_digest()
        state = load_state(self.profile)
        state["plugins"][PLUGIN]["artifact_digest"] = "a" * 64
        save_state(self.profile, state)
        self.assertEqual(self._call(artifact_digest=digest), {"status": "stale"})

    def test_pending_recovery_and_forbidden_entry_fail_closed(self):
        digest = self._owned_digest()
        marker = self.profile / ".xnobrain" / "extensions" / "publication.json"
        marker.write_text("{}", encoding="utf-8")
        before = self._snapshot()
        self.assertEqual(self._call(artifact_digest=digest), {"status": "unavailable"})
        self.assertEqual(self._snapshot(), before)
        self.assertTrue(marker.exists())

        marker.unlink()
        recovery = self.profile / ".xnobrain" / "extensions" / "mcp-recovery.json"
        recovery.write_text("{}", encoding="utf-8")
        self.assertEqual(self._call(artifact_digest=digest), {"status": "unavailable"})
        self.assertTrue(recovery.exists())

        recovery.unlink()
        config = yaml.safe_load((self.profile / "config.yaml").read_text(encoding="utf-8"))
        config["plugins"]["entries"] = {PLUGIN: {"allow_tool_override": True}}
        (self.profile / "config.yaml").write_text(
            yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
        )
        self.assertEqual(self._call(artifact_digest=digest), {"status": "not_approved"})

    def test_unsupported_plugin_shapes(self):
        digest = self._owned_digest()
        path = self.profile / "plugins" / PLUGIN
        path.joinpath("plugin.yaml").write_text(
            yaml.safe_dump({"name": PLUGIN, "kind": "standalone"}), encoding="utf-8"
        )
        self._approve()
        digest = f"sha256:{tree_digest(path)}"
        self.assertEqual(self._call(artifact_digest=digest), {"status": "unsupported"})

        self._plugin(name="xnobrain-runtime-updates")
        self._approve(name="xnobrain-runtime-updates")
        system_digest = (
            f"sha256:{tree_digest(self.profile / 'plugins' / 'xnobrain-runtime-updates')}"
        )
        self.assertEqual(
            self._call(
                plugin="xnobrain-runtime-updates",
                platform="xnobrain-runtime-updates",
                artifact_digest=system_digest,
            ),
            {"status": "unsupported"},
        )

        self.assertEqual(
            self._call(plugin="zalo_bot", platform="zalo", artifact_digest=digest),
            {"status": "unsupported"},
        )

    def test_malformed_egress_is_unsupported(self):
        cases = {
            "extra_top": _egress(extra=True),
            "extra_rule": _egress(
                rules=[
                    {
                        "host": "api.example.test",
                        "methods": ["GET"],
                        "path_template": "/v1/{endpoint}",
                        "endpoints": ["send"],
                        "auth": {"kind": "bearer"},
                        "extra": True,
                    }
                ]
            ),
            "version": _egress(version="plugin-egress-v2"),
            "no_rules": _egress(rules=[]),
            "too_many_rules": _egress(
                rules=[
                    {
                        "host": "api.example.test",
                        "methods": ["GET"],
                        "path_template": "/v1/{endpoint}",
                        "endpoints": [f"send{i}"],
                        "auth": {"kind": "bearer"},
                    }
                    for i in range(9)
                ]
            ),
            "wildcard_host": _egress(
                rules=[
                    {
                        "host": "*.example.test",
                        "methods": ["GET"],
                        "path_template": "/v1/{endpoint}",
                        "endpoints": ["send"],
                        "auth": {"kind": "bearer"},
                    }
                ]
            ),
            "ip_host": _egress(
                rules=[
                    {
                        "host": "203.0.113.10",
                        "methods": ["GET"],
                        "path_template": "/v1/{endpoint}",
                        "endpoints": ["send"],
                        "auth": {"kind": "bearer"},
                    }
                ]
            ),
            "localhost": _egress(
                rules=[
                    {
                        "host": "localhost",
                        "methods": ["GET"],
                        "path_template": "/v1/{endpoint}",
                        "endpoints": ["send"],
                        "auth": {"kind": "bearer"},
                    }
                ]
            ),
            "bad_method": _egress(
                rules=[
                    {
                        "host": "api.example.test",
                        "methods": ["PUT"],
                        "path_template": "/v1/{endpoint}",
                        "endpoints": ["send"],
                        "auth": {"kind": "bearer"},
                    }
                ]
            ),
            "duplicate_method": _egress(
                rules=[
                    {
                        "host": "api.example.test",
                        "methods": ["GET", "get"],
                        "path_template": "/v1/{endpoint}",
                        "endpoints": ["send"],
                        "auth": {"kind": "bearer"},
                    }
                ]
            ),
            "relative_path": _egress(
                rules=[
                    {
                        "host": "api.example.test",
                        "methods": ["GET"],
                        "path_template": "v1/{endpoint}",
                        "endpoints": ["send"],
                        "auth": {"kind": "bearer"},
                    }
                ]
            ),
            "dot_segment": _egress(
                rules=[
                    {
                        "host": "api.example.test",
                        "methods": ["GET"],
                        "path_template": "/v1/../{endpoint}",
                        "endpoints": ["send"],
                        "auth": {"kind": "bearer"},
                    }
                ]
            ),
            "query": _egress(
                rules=[
                    {
                        "host": "api.example.test",
                        "methods": ["GET"],
                        "path_template": "/v1/{endpoint}?x=1",
                        "endpoints": ["send"],
                        "auth": {"kind": "bearer"},
                    }
                ]
            ),
            "unknown_placeholder": _egress(
                rules=[
                    {
                        "host": "api.example.test",
                        "methods": ["GET"],
                        "path_template": "/v1/{other}",
                        "endpoints": ["send"],
                        "auth": {"kind": "bearer"},
                    }
                ]
            ),
            "no_endpoint_placeholder": _egress(
                rules=[
                    {
                        "host": "api.example.test",
                        "methods": ["GET"],
                        "path_template": "/v1",
                        "endpoints": ["send"],
                        "auth": {"kind": "bearer"},
                    }
                ]
            ),
            "duplicate_endpoint": _egress(
                rules=[
                    {
                        "host": "api.example.test",
                        "methods": ["GET"],
                        "path_template": "/v1/{endpoint}",
                        "endpoints": ["send", "send"],
                        "auth": {"kind": "bearer"},
                    }
                ]
            ),
            "cross_rule_endpoint": _egress(
                rules=[
                    {
                        "host": "api.example.test",
                        "methods": ["GET"],
                        "path_template": "/v1/{endpoint}",
                        "endpoints": ["send"],
                        "auth": {"kind": "bearer"},
                    },
                    {
                        "host": "other.example.test",
                        "methods": ["GET"],
                        "path_template": "/v1/{endpoint}",
                        "endpoints": ["send"],
                        "auth": {"kind": "bearer"},
                    },
                ]
            ),
            "too_many_endpoints": _egress(
                rules=[
                    {
                        "host": "api.example.test",
                        "methods": ["GET"],
                        "path_template": "/v1/{endpoint}",
                        "endpoints": [f"send{i}" for i in range(17)],
                        "auth": {"kind": "bearer"},
                    }
                ]
            ),
            "unknown_auth": _egress(
                rules=[
                    {
                        "host": "api.example.test",
                        "methods": ["GET"],
                        "path_template": "/v1/{endpoint}",
                        "endpoints": ["send"],
                        "auth": {"kind": "cookie"},
                    }
                ]
            ),
            "path_without_token": _egress(
                rules=[
                    {
                        "host": "api.example.test",
                        "methods": ["GET"],
                        "path_template": "/v1/{endpoint}",
                        "endpoints": ["send"],
                        "auth": {"kind": "path"},
                    }
                ]
            ),
            "path_with_header": _egress(
                rules=[
                    {
                        "host": "api.example.test",
                        "methods": ["GET"],
                        "path_template": "/bot{token}/{endpoint}",
                        "endpoints": ["send"],
                        "auth": {"kind": "path", "header": "X-Key"},
                    }
                ]
            ),
            "bearer_with_token": _egress(
                rules=[
                    {
                        "host": "api.example.test",
                        "methods": ["GET"],
                        "path_template": "/bot{token}/{endpoint}",
                        "endpoints": ["send"],
                        "auth": {"kind": "bearer"},
                    }
                ]
            ),
            "custom_bearer_header": _egress(
                rules=[
                    {
                        "host": "api.example.test",
                        "methods": ["GET"],
                        "path_template": "/v1/{endpoint}",
                        "endpoints": ["send"],
                        "auth": {"kind": "bearer", "header": "X-Key"},
                    }
                ]
            ),
            "forbidden_header": _egress(
                rules=[
                    {
                        "host": "api.example.test",
                        "methods": ["GET"],
                        "path_template": "/v1/{endpoint}",
                        "endpoints": ["send"],
                        "auth": {"kind": "header", "header": "Cookie"},
                    }
                ]
            ),
            "mixed_auth": _egress(
                rules=[
                    {
                        "host": "api.example.test",
                        "methods": ["GET"],
                        "path_template": "/v1/{endpoint}",
                        "endpoints": ["send"],
                        "auth": {"kind": "bearer"},
                    },
                    {
                        "host": "other.example.test",
                        "methods": ["GET"],
                        "path_template": "/v1/{endpoint}",
                        "endpoints": ["other"],
                        "auth": {"kind": "header", "header": "X-Key"},
                    },
                ]
            ),
        }
        _marker(self.profile)
        for name, egress in cases.items():
            with self.subTest(name=name):
                self._plugin(egress=egress)
                self._approve()
                digest = f"sha256:{tree_digest(self.profile / 'plugins' / PLUGIN)}"
                self.assertEqual(self._call(artifact_digest=digest), {"status": "unsupported"})

    def test_builtin_zalo_bot_uses_actual_bundled_path(self):
        _marker(self.profile)
        bundled = Path(self.temporary.name) / "bundled" / "zalo_bot"
        shutil.copytree(
            VENDORED,
            bundled,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        digest = f"sha256:{tree_digest(bundled)}"
        with mock.patch(
            "xnobrain.services.plugin_artifact.bundled_platform_path",
            return_value=bundled,
        ):
            result = self._call(plugin="zalo_bot", platform="zalo_bot", artifact_digest=digest)
            self.assertEqual(result["status"], "match")
            self.assertEqual(result["source"], "bundled_platform")
            self.assertEqual(result["artifact_digest"], digest)
            self.assertNotIn("rules", result)
            self.assertEqual(
                self._call(
                    plugin="zalo_bot", platform="zalo_bot", artifact_digest="sha256:" + "a" * 64
                ),
                {"status": "stale"},
            )
            bundled.joinpath("extra.py").write_text("VALUE = 1\n", encoding="utf-8")
            self.assertEqual(
                self._call(plugin="zalo_bot", platform="zalo_bot", artifact_digest=digest),
                {"status": "stale"},
            )

    def test_builtin_missing_or_mismatched_manifest_is_unavailable(self):
        _marker(self.profile)
        with mock.patch(
            "xnobrain.services.plugin_artifact.bundled_platform_path",
            return_value=None,
        ):
            self.assertEqual(
                self._call(
                    plugin="zalo_bot", platform="zalo_bot", artifact_digest="sha256:" + "a" * 64
                ),
                {"status": "unavailable"},
            )
        bundled = Path(self.temporary.name) / "bundled-bad" / "zalo_bot"
        bundled.mkdir(parents=True)
        bundled.joinpath("plugin.yaml").write_text(
            yaml.safe_dump({"name": "other", "kind": "platform"}), encoding="utf-8"
        )
        bundled.joinpath("__init__.py").write_text("", encoding="utf-8")
        digest = f"sha256:{tree_digest(bundled)}"
        with mock.patch(
            "xnobrain.services.plugin_artifact.bundled_platform_path",
            return_value=bundled,
        ):
            self.assertEqual(
                self._call(plugin="zalo_bot", platform="zalo_bot", artifact_digest=digest),
                {"status": "unavailable"},
            )

    def test_authority_never_executes_or_writes(self):
        _marker(self.profile)
        path = self._plugin(egress=_egress())
        path.joinpath("__init__.py").write_text(
            "from pathlib import Path\n"
            "SENTINEL = Path(__file__).parent / 'imported'\n"
            "SENTINEL.write_text('imported')\n"
            "def register(ctx):\n"
            "    (Path(__file__).parent / 'registered').write_text('registered')\n",
            encoding="utf-8",
        )
        self._approve()
        digest = f"sha256:{tree_digest(path)}"
        before = self._snapshot()
        result = self._call(artifact_digest=digest)
        self.assertEqual(result["status"], "match")
        self.assertFalse((path / "imported").exists())
        self.assertFalse((path / "registered").exists())
        self.assertEqual(self._snapshot(), before)

    def test_response_contains_no_secret_owner_or_path(self):
        _marker(self.profile)
        path = self._plugin(egress=_egress())
        path.joinpath("secret.txt").write_text("super-secret-value", encoding="utf-8")
        self._approve()
        digest = f"sha256:{tree_digest(path)}"
        result = self._call(artifact_digest=digest)
        rendered = json.dumps(result)
        self.assertNotIn("super-secret-value", rendered)
        self.assertNotIn(str(self.profile), rendered)
        self.assertNotIn(USER, rendered)
        self.assertNotIn(TENANT, rendered)
        self.assertNotIn("secret-workload-token", rendered)


class _Handler:
    def __init__(self, service):
        self.service = SimpleNamespace(plugin_artifact=service)

    async def dispatch(self, request, body):
        operation, message, status = authority_operations(self, request, body)[
            "plugin_artifact_authority"
        ]
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


class PluginArtifactRouteTests(_AuthorityCase):
    def _app(self):
        app = FastAPI()
        setup_routes(app, _Handler(self.service))
        return app

    async def test_route_requires_signed_actor_and_is_hidden(self):
        _marker(self.profile)
        path = self._plugin(egress=_egress())
        self._approve()
        digest = f"sha256:{tree_digest(path)}"
        body = _body(artifact_digest=digest)
        headers = {
            TRUSTED_SUBJECT_HEADER: USER,
            TRUSTED_TENANT_HEADER: TENANT,
            TRUSTED_SIGNATURE_HEADER: principal_signature(TOKEN, USER, TENANT, ""),
        }
        app = self._app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            unsigned = await client.post(PATH, json=body)
            signed = await client.post(PATH, json=body, headers=headers)
            extra = await client.post(
                PATH, json={**body, "workspace_id": "workspace-1"}, headers=headers
            )

        self.assertEqual(unsigned.status_code, 401)
        self.assertEqual(signed.status_code, 200)
        self.assertEqual(signed.json()["data"]["status"], "match")
        self.assertEqual(extra.status_code, 422)
        self.assertNotIn("/internal/plugin-artifact/authority", app.openapi()["paths"])

    def test_owner_marker_reader_is_bounded_and_no_follow(self):
        _marker(self.profile)
        outside = Path(self.temporary.name) / "outside.json"
        outside.write_text(json.dumps({"subject": USER}), encoding="utf-8")
        self.profile.joinpath(".community-profile-owner.json").unlink()
        self.profile.joinpath(".community-profile-owner.json").symlink_to(outside)
        with self.assertRaises(_ProbeConflict):
            read_json_file(self.profile / ".community-profile-owner.json")


if __name__ == "__main__":
    unittest.main()
