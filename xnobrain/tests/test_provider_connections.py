"""Route + service tests for multi-account provider connections (plan 011).

Uses a stateful fake that mimics the NineRouterManager adapter contract
(already-filtered rows, no credential material) so the service/route layer is
exercised without a live 9router.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import yaml

from xnobrain.app import XNOBrainApplication
from xnobrain.integrations import AgentManager, GlobalConfigManager, NineRouterAPIError


_ALLOWED_CONNECTION_KEYS = {
    "id", "provider", "auth_type", "name", "email", "active", "priority",
    "default_model", "test_status", "last_error",
}
_FORBIDDEN = ("api_key", "apiKey", "providerSpecificData", "accessToken", "refreshToken", "sk-secret")


class FakeRouter:
    """Stateful fake matching the adapter's filtered contract (no credentials)."""

    def __init__(self):
        self._rows = [
            {"id": "codex-1", "provider": "codex", "auth_type": "oauth", "name": "work",
             "email": "work@example.com", "active": True, "priority": 0,
             "default_model": "", "test_status": "valid", "last_error": ""},
            {"id": "codex-2", "provider": "codex", "auth_type": "oauth", "name": "home",
             "email": "home@example.com", "active": False, "priority": 1,
             "default_model": "", "test_status": "unknown", "last_error": ""},
        ]
        self.created_bodies: list[dict] = []
        self.ensured_nodes: list[dict] = []
        self.list_connections_calls = 0
        self.list_models_calls = 0
        self.list_connections_failures = 0
        self._models = [
            {
                "id": "cx/gpt-5.6-sol",
                "provider": "codex",
                "reasoning_levels": ["low", "medium", "high", "xhigh", "max", "ultra"],
            },
            {
                "id": "cx/gpt-5.3-codex-spark",
                "provider": "codex",
                "reasoning_levels": [],
            },
        ]
        self.oauth_calls: list[tuple[str, str, str, dict | None]] = []
        self.oauth_queries: list[tuple[str, str, str]] = []
        self.authorized_device_providers: set[str] = set()
        self.imported_cursor_credentials: list[tuple[str, str]] = []
        self.api_key_connection_ids: dict[tuple[str, str], str] = {}

    async def list_connections(self):
        self.list_connections_calls += 1
        if self.list_connections_failures > 0:
            self.list_connections_failures -= 1
            raise NineRouterAPIError("9router is starting")
        return {"connections": [dict(row) for row in self._rows]}

    async def list_models(self):
        self.list_models_calls += 1
        return {"data": [dict(item) for item in self._models]}

    async def upsert_api_key_connection(self, body):
        self.created_bodies.append(dict(body))  # captures the key but never returns it
        fingerprint = hashlib.sha256(body["api_key"].encode("utf-8")).hexdigest()
        identity = (body["provider"], fingerprint)
        connection_id = self.api_key_connection_ids.get(identity)
        if connection_id:
            row = next(item for item in self._rows if item["id"] == connection_id)
            row["active"] = True
            return {"object": "xnobrain.provider_runtime.provider", "connection": dict(row)}
        connection_id = f"api-key-{len(self.api_key_connection_ids) + 1}"
        self.api_key_connection_ids[identity] = connection_id
        row = {
            "id": connection_id, "provider": body["provider"],
            "auth_type": "api-key", "name": f"API key • {fingerprint[:8]}", "email": "",
            "active": True, "priority": 0, "default_model": "",
            "test_status": "unknown", "last_error": "",
        }
        self._rows.append(row)
        return {"object": "xnobrain.provider_runtime.provider", "connection": dict(row)}

    async def ensure_openai_compatible_provider(
        self, provider, *, display_name, base_url, router_prefix=None,
    ):
        self.ensured_nodes.append({
            "provider": provider,
            "display_name": display_name,
            "base_url": base_url,
            "router_prefix": router_prefix,
        })
        return f"openai-compatible-chat-{provider}1"

    async def ensure_opencode_zen_provider(self):
        return await self.ensure_openai_compatible_provider(
            "opencode",
            display_name="OpenCode Zen",
            base_url="https://opencode.ai/zen/v1",
            router_prefix="ocz",
        )

    async def update_connection(self, connection_id, *, active=None, priority=None):
        for row in self._rows:
            if row["id"] == connection_id:
                if active is not None:
                    row["active"] = active
                if priority is not None:
                    row["priority"] = priority
                return {"object": "xnobrain.provider_runtime.provider", "connection": dict(row)}
        return {"updated": True}

    async def delete_connection(self, connection_id):
        self._rows = [row for row in self._rows if row["id"] != connection_id]
        return {"deleted": True}

    async def test_connection(self, connection_id):
        return {"valid": True, "error": ""}

    async def usage_for_connection(self, connection_id):
        return {
            "object": "router.connection_usage", "connection_id": connection_id,
            "available": True, "plan": "plus", "message": "",
            "quotas": [{"name": "session", "used": 20, "total": 100,
                        "remaining_percent": 80, "reset_at": "", "unlimited": False}],
        }

    async def oauth(self, provider, action, *, method, query_string="", body=None):
        self.oauth_calls.append((provider, action, method, dict(body) if body else None))
        self.oauth_queries.append((provider, action, query_string))
        if action == "device-code":
            payload = {
                "device_code": f"{provider}-device-secret",
                "user_code": "ABCD-1234",
                "verification_uri": f"https://login.example/{provider}",
                "interval": 5,
            }
            if provider in {"kiro", "amazon-q"}:
                payload.update({
                    "_clientId": "aws-client",
                    "_clientSecret": "aws-secret",
                    "_region": "us-east-1",
                    "_authMethod": "builder-id",
                })
            return payload
        if action == "poll" and provider in self.authorized_device_providers:
            self._rows.append({
                "id": f"{provider}-1", "provider": provider,
                "auth_type": "oauth", "name": provider, "email": "",
                "active": True, "priority": 0, "default_model": "",
                "test_status": "active", "last_error": "",
            })
            return {"success": True, "connection": {"id": f"{provider}-1"}}
        if action == "poll":
            return {"success": False, "pending": True, "error": "authorization_pending"}
        return {
            "authUrl": f"https://login.example/{provider}",
            "codeVerifier": "verifier",
            "state": "state",
        }

    async def import_cursor_credentials(self, access_token, machine_id=None):
        self.imported_cursor_credentials.append((access_token, machine_id or ""))
        row = {
            "id": "cursor-1", "provider": "cursor", "auth_type": "oauth",
            "name": "Cursor", "email": "", "active": True, "priority": 0,
            "default_model": "", "test_status": "active", "last_error": "",
        }
        self._rows.append(row)
        return {"success": True, "connection": {"id": "cursor-1", "provider": "cursor"}}


class ProviderConnectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        root = base / "root"
        profiles = base / "profiles"
        root.mkdir(parents=True)
        profiles.mkdir(parents=True)
        (root / "config.yaml").write_text(yaml.safe_dump({
            "model": {"provider": "custom:xnobrain", "default": "auto"},
            "providers": {}, "agent": {"reasoning_effort": "medium"},
            "approvals": {"mode": "manual"}, "terminal": {"backend": "local"},
        }), encoding="utf-8")
        self.environment = patch.dict(os.environ, {
            "HERMES_HOME": str(root), "HERMES_ROOT_PROFILE": str(root),
            "HERMES_PROFILES_ROOT": str(profiles), "DATA_DIR": self.temporary.name,
        })
        self.environment.start()
        self.router = FakeRouter()
        app = FastAPI()
        XNOBrainApplication(
            AgentManager(root_profile=root, profiles_root=profiles,
                         legacy_agents_root=base / "legacy"),
            GlobalConfigManager(root_profile=root), self.router,
        ).register(app)
        self.app = app

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def client(self):
        return AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")

    def _assert_connection_shape(self, connection):
        self.assertEqual(set(connection), _ALLOWED_CONNECTION_KEYS)

    def test_openapi_exposes_one_api_key_upsert_without_label(self):
        document = self.app.openapi()
        paths = document["paths"]
        collection = "/xnobrain/api/runtime/v1/providers/{provider_id}/connections"
        item = collection + "/{connection_id}"
        self.assertIn("post", paths[collection])
        self.assertIn("delete", paths[item])
        self.assertIn("post", paths[item + "/test"])
        self.assertNotIn(
            "/xnobrain/api/runtime/v1/providers/{provider_id}/update",
            paths,
        )
        schema = document["components"]["schemas"]["ConnectionUpsert"]
        self.assertEqual(set(schema["properties"]), {"api_key", "base_url"})
        self.assertFalse(schema["additionalProperties"])

    async def test_list_connections_sorted_and_connected(self):
        async with self.client() as client:
            data = (await client.get("/xnobrain/api/runtime/v1/providers/codex/connections")).json()["data"]
        self.assertTrue(data["connected"])
        self.assertEqual([c["id"] for c in data["connections"]], ["codex-1", "codex-2"])
        for connection in data["connections"]:
            self._assert_connection_shape(connection)

    async def test_ownership_guard_404_across_providers(self):
        async with self.client() as client:
            # codex-1 belongs to codex, not openai
            for path in (
                "/xnobrain/api/runtime/v1/providers/openai/connections/codex-1/usage",
                "/xnobrain/api/runtime/v1/providers/openai/connections/codex-1/test",
            ):
                method = client.post if path.endswith("/test") else client.get
                self.assertEqual((await method(path)).status_code, 404)

    async def test_api_key_upsert_never_leaks_key_or_accepts_a_label(self):
        async with self.client() as client:
            response = await client.post(
                "/xnobrain/api/runtime/v1/providers/openai/connections",
                json={"api_key": "sk-secret123"})
            label = await client.post(
                "/xnobrain/api/runtime/v1/providers/openai/connections",
                json={"api_key": "sk-other", "name": "second"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(label.status_code, 422)
        self.assertNotIn("sk-secret123", response.text)
        self._assert_connection_shape(response.json()["data"]["connection"])
        # the key WAS passed through to the router (proving it reached 9router)
        self.assertEqual(self.router.created_bodies[-1]["api_key"], "sk-secret123")

    async def test_same_api_key_updates_one_connection_and_new_key_adds_another(self):
        path = "/xnobrain/api/runtime/v1/providers/openai/connections"
        async with self.client() as client:
            first = await client.post(path, json={"api_key": "sk-same"})
            repeated = await client.post(path, json={"api_key": "sk-same"})
            different = await client.post(path, json={"api_key": "sk-different"})
            listed = await client.get(path)

        self.assertEqual(first.json()["data"]["connection"]["id"], repeated.json()["data"]["connection"]["id"])
        self.assertNotEqual(first.json()["data"]["connection"]["id"], different.json()["data"]["connection"]["id"])
        self.assertEqual(len(listed.json()["data"]["connections"]), 2)

    async def test_oauth_provider_rejects_api_key_add(self):
        async with self.client() as client:
            response = await client.post(
                "/xnobrain/api/runtime/v1/providers/codex/connections", json={"api_key": "x"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "oauth_connect_required")

    async def test_subscription_providers_are_common_first(self):
        async with self.client() as client:
            providers = (await client.get(
                "/xnobrain/api/runtime/v1/providers"
            )).json()["data"]

        subscription_ids = [
            item["id"] for item in providers if item["connection_mode"] != "api-key"
        ]
        self.assertEqual(
            subscription_ids,
            [
                "codex", "claude", "github", "cursor", "grok-cli", "xai-oauth",
                "kimi-coding", "cline", "kilocode", "kiro", "amazon-q",
                "clinepass", "antigravity",
            ],
        )
        labels = {item["id"]: item["display_name"] for item in providers}
        self.assertEqual(labels["github"], "GitHub Copilot")
        self.assertEqual(labels["cursor"], "Cursor")
        self.assertEqual(labels["grok-cli"], "Grok Build")
        self.assertEqual(labels["xai-oauth"], "xAI Grok")
        self.assertEqual(labels["amazon-q"], "Amazon Q Developer")

    async def test_provider_models_and_reasoning_follow_router_metadata(self):
        async with self.client() as client:
            models = await client.get(
                "/xnobrain/api/runtime/v1/providers/codex/models"
            )
            reasoning = await client.get(
                "/xnobrain/api/runtime/v1/providers/codex/models/"
                "cx%2Fgpt-5.6-sol/reasoning"
            )
            unsupported = await client.get(
                "/xnobrain/api/runtime/v1/providers/codex/models/"
                "cx%2Fmissing/reasoning"
            )

        self.assertEqual(models.status_code, 200)
        rows = models.json()["data"]["models"]
        self.assertEqual(
            [item["id"] for item in rows],
            ["auto", "cx/gpt-5.6-sol", "cx/gpt-5.3-codex-spark"],
        )
        self.assertEqual(
            rows[1]["reasoning"],
            ["low", "medium", "high", "xhigh", "max", "ultra"],
        )
        self.assertEqual(rows[1]["default_reasoning"], "medium")
        self.assertEqual(reasoning.status_code, 200)
        self.assertEqual(reasoning.json()["data"], {
            "provider_id": "codex",
            "model": "cx/gpt-5.6-sol",
            "reasoning": ["low", "medium", "high", "xhigh", "max", "ultra"],
            "default_reasoning": "medium",
        })
        self.assertEqual(unsupported.status_code, 404)

    async def test_device_code_subscription_starts_and_completes_on_status_poll(self):
        async with self.client() as client:
            started = await client.post(
                "/xnobrain/api/runtime/v1/providers/github/connect"
            )
            self.router.authorized_device_providers.add("github")
            completed = await client.get(
                "/xnobrain/api/runtime/v1/providers/github/connect"
            )

        info = started.json()["data"]
        self.assertEqual(info["connection_mode"], "device-code")
        self.assertEqual(info["user_code"], "ABCD-1234")
        self.assertEqual(info["poll_interval_seconds"], 5)
        self.assertTrue(completed.json()["data"]["connected"])
        self.assertEqual(
            self.router.oauth_calls[-1],
            (
                "github",
                "poll",
                "POST",
                {"deviceCode": "github-device-secret", "codeVerifier": ""},
            ),
        )

    async def test_grok_build_uses_the_device_code_subscription_flow(self):
        async with self.client() as client:
            started = await client.post(
                "/xnobrain/api/runtime/v1/providers/grok-cli/connect"
            )

        info = started.json()["data"]
        self.assertEqual(info["verification_url"], "https://login.example/grok-cli")
        self.assertEqual(
            self.router.oauth_calls[-1][:3],
            ("grok-cli", "device-code", "GET"),
        )

    async def test_kiro_device_flow_preserves_aws_client_data_for_polling(self):
        async with self.client() as client:
            await client.post("/xnobrain/api/runtime/v1/providers/kiro/connect")
            await client.get("/xnobrain/api/runtime/v1/providers/kiro/connect")

        self.assertEqual(
            self.router.oauth_calls[-1],
            (
                "kiro",
                "poll",
                "POST",
                {
                    "deviceCode": "kiro-device-secret",
                    "codeVerifier": "",
                    "extraData": {
                        "_clientId": "aws-client",
                        "_clientSecret": "aws-secret",
                        "_region": "us-east-1",
                        "_authMethod": "builder-id",
                    },
                },
            ),
        )

    async def test_xai_subscription_uses_fixed_loopback_callback(self):
        async with self.client() as client:
            started = await client.post(
                "/xnobrain/api/runtime/v1/providers/xai-oauth/connect"
            )

        self.assertEqual(started.status_code, 200)
        self.assertEqual(
            self.router.oauth_calls[-1][:3],
            ("xai-oauth", "authorize", "GET"),
        )
        self.assertEqual(
            self.router.oauth_queries[-1],
            (
                "xai-oauth",
                "authorize",
                "redirect_uri=http://127.0.0.1:56121/callback",
            ),
        )

    async def test_cursor_import_accepts_credential_json_without_echoing_it(self):
        credential = '{"accessToken":"cursor-secret","machineId":"machine-1"}'
        async with self.client() as client:
            started = await client.post(
                "/xnobrain/api/runtime/v1/providers/cursor/connect"
            )
            completed = await client.put(
                "/xnobrain/api/runtime/v1/providers/cursor/connect",
                json={"text": credential},
            )

        self.assertEqual(
            started.json()["data"]["required_client_action"],
            "submit_text",
        )
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertNotIn("cursor-secret", completed.text)
        self.assertEqual(
            self.router.imported_cursor_credentials,
            [("cursor-secret", "machine-1")],
        )

    async def test_opencode_go_connect_guides_auth_then_accepts_issued_key(self):
        async with self.client() as client:
            started = await client.post(
                "/xnobrain/api/runtime/v1/providers/opencode-go/connect")
            completed = await client.put(
                "/xnobrain/api/runtime/v1/providers/opencode-go/connect",
                json={"text": "oc-go-secret"},
            )

        self.assertEqual(started.status_code, 200)
        info = started.json()["data"]
        self.assertEqual(info["connection_mode"], "api-key")
        self.assertEqual(info["required_client_action"], "submit_text")
        self.assertEqual(info["login_url"], "https://opencode.ai/auth")
        self.assertNotIn("oc-go-secret", completed.text)
        self.assertEqual(
            self.router.created_bodies[-1],
            {"provider": "opencode-go", "api_key": "oc-go-secret"},
        )

    async def test_opencode_zen_connect_stays_direct_api_key(self):
        async with self.client() as client:
            started = await client.post(
                "/xnobrain/api/runtime/v1/providers/opencode/connect")

        self.assertEqual(started.status_code, 200)
        info = started.json()["data"]
        self.assertEqual(info["connection_mode"], "api-key")
        self.assertNotIn("login_url", info)

    async def test_opencode_zen_requires_a_key_and_has_no_free_default(self):
        async with self.client() as client:
            providers = (await client.get(
                "/xnobrain/api/runtime/v1/providers"
            )).json()["data"]
            zen = next(item for item in providers if item["id"] == "opencode")
            connected = await client.put(
                "/xnobrain/api/runtime/v1/providers/opencode/connect",
                json={"text": "zen-secret"},
            )

        self.assertFalse(zen["connected"])
        self.assertEqual(zen["status"], "disconnected")
        self.assertNotIn("free_models_available", zen)
        self.assertEqual(connected.status_code, 200)
        self.assertEqual(
            self.router.created_bodies[-1],
            {
                "provider": "openai-compatible-chat-opencode1",
                "api_key": "zen-secret",
            },
        )

    async def test_patch_activates_reorders_and_requires_a_field(self):
        async with self.client() as client:
            activated = await client.patch(
                "/xnobrain/api/runtime/v1/providers/codex/connections/codex-2", json={"active": True})
            self.assertTrue(activated.json()["data"]["connection"]["active"])
            self.assertEqual(
                (await client.patch(
                    "/xnobrain/api/runtime/v1/providers/codex/connections/codex-2",
                    json={"priority": 0})).status_code, 200)
            self.assertEqual(
                (await client.patch(
                    "/xnobrain/api/runtime/v1/providers/codex/connections/codex-2",
                    json={})).status_code, 400)

    async def test_delete_one_keeps_provider_connected(self):
        async with self.client() as client:
            deleted = await client.delete(
                "/xnobrain/api/runtime/v1/providers/codex/connections/codex-2")
            self.assertEqual(deleted.status_code, 200)
            self.assertTrue(deleted.json()["data"]["deleted"])
            self.assertTrue(deleted.json()["data"]["connected"])  # codex-1 still active
            remaining = (await client.get(
                "/xnobrain/api/runtime/v1/providers/codex/connections")).json()["data"]
            self.assertEqual([c["id"] for c in remaining["connections"]], ["codex-1"])

    async def test_providers_backward_compat_reports_connection_count(self):
        async with self.client() as client:
            providers = (await client.get("/xnobrain/api/runtime/v1/providers")).json()["data"]
        codex = next(item for item in providers if item["id"] == "codex")
        self.assertTrue(codex["connected"])          # one active of two
        self.assertEqual(codex["connection_count"], 2)

    async def test_providers_are_loaded_once_and_returned_from_memory(self):
        async with self.client() as client:
            first = await client.get("/xnobrain/api/runtime/v1/providers")
            second = await client.get("/xnobrain/api/runtime/v1/providers")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["data"], second.json()["data"])
        self.assertEqual(self.router.list_connections_calls, 1)
        self.assertEqual(self.router.list_models_calls, 1)

    async def test_transient_router_failure_is_not_cached(self):
        self.router.list_connections_failures = 1
        async with self.client() as client:
            unavailable = await client.get("/xnobrain/api/runtime/v1/providers")
            recovered = await client.get("/xnobrain/api/runtime/v1/providers")
            cached = await client.get("/xnobrain/api/runtime/v1/providers")

        self.assertTrue(all(
            item["status"] == "unavailable" for item in unavailable.json()["data"]
        ))
        recovered_codex = next(
            item for item in recovered.json()["data"] if item["id"] == "codex"
        )
        self.assertTrue(recovered_codex["connected"])
        self.assertEqual(recovered.json()["data"], cached.json()["data"])
        self.assertEqual(self.router.list_connections_calls, 2)
        self.assertEqual(self.router.list_models_calls, 1)

    async def test_provider_create_invalidates_the_cached_list(self):
        async with self.client() as client:
            await client.get("/xnobrain/api/runtime/v1/providers")
            created = await client.post(
                "/xnobrain/api/runtime/v1/providers/openai/connections",
                json={"api_key": "sk-secret123"},
            )
            refreshed = await client.get("/xnobrain/api/runtime/v1/providers")

        self.assertEqual(created.status_code, 200)
        openai = next(item for item in refreshed.json()["data"] if item["id"] == "openai")
        self.assertTrue(openai["connected"])
        self.assertEqual(openai["connection_count"], 1)
        self.assertEqual(self.router.list_models_calls, 2)

    async def test_provider_connection_update_and_delete_invalidate_the_cached_list(self):
        async with self.client() as client:
            await client.get("/xnobrain/api/runtime/v1/providers")
            updated = await client.patch(
                "/xnobrain/api/runtime/v1/providers/codex/connections/codex-2",
                json={"active": True},
            )
            after_update = await client.get("/xnobrain/api/runtime/v1/providers")
            deleted = await client.delete(
                "/xnobrain/api/runtime/v1/providers/codex/connections/codex-2",
            )
            after_delete = await client.get("/xnobrain/api/runtime/v1/providers")

        self.assertEqual(updated.status_code, 200)
        self.assertEqual(deleted.status_code, 200)
        codex_after_update = next(
            item for item in after_update.json()["data"] if item["id"] == "codex"
        )
        codex_after_delete = next(
            item for item in after_delete.json()["data"] if item["id"] == "codex"
        )
        self.assertEqual(codex_after_update["connection_count"], 2)
        self.assertEqual(codex_after_delete["connection_count"], 1)
        self.assertEqual(self.router.list_models_calls, 3)

    async def test_lists_xai_openrouter_and_groq_as_key_presets(self):
        async with self.client() as client:
            providers = (await client.get("/xnobrain/api/runtime/v1/providers")).json()["data"]

        presets = {item["id"]: item for item in providers if item["id"] in {
            "xai", "openrouter", "groq",
        }}
        self.assertEqual(set(presets), {"xai", "openrouter", "groq"})
        self.assertEqual(presets["xai"]["base_url"], "https://api.x.ai/v1")
        self.assertEqual(presets["openrouter"]["base_url"], "https://openrouter.ai/api/v1")
        self.assertEqual(presets["groq"]["base_url"], "https://api.groq.com/openai/v1")
        self.assertTrue(all(item["connection_mode"] == "api-key" for item in presets.values()))

    async def test_connections_ui_saves_xai_openrouter_and_groq_through_nine_router_nodes(self):
        expected = {
            "xai": ("xAI", "https://api.x.ai/v1"),
            "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1"),
            "groq": ("Groq", "https://api.groq.com/openai/v1"),
        }
        async with self.client() as client:
            for provider, _ in expected.items():
                response = await client.post(
                    f"/xnobrain/api/runtime/v1/providers/{provider}/connections",
                    json={
                        "api_key": f"{provider}-secret",
                        "base_url": "https://untrusted.example/v1",
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)

        self.assertEqual(
            self.router.ensured_nodes,
            [{
                "provider": provider,
                "display_name": display_name,
                "base_url": base_url,
                "router_prefix": None,
            } for provider, (display_name, base_url) in expected.items()],
        )
        self.assertEqual(
            [body["provider"] for body in self.router.created_bodies[-3:]],
            [f"openai-compatible-chat-{provider}1" for provider in expected],
        )

    async def test_custom_openai_compatible_connection_requires_and_uses_base_url(self):
        async with self.client() as client:
            missing = await client.post(
                "/xnobrain/api/runtime/v1/providers/openai-like/connections",
                json={"api_key": "custom-secret"},
            )
            created = await client.post(
                "/xnobrain/api/runtime/v1/providers/openai-like/connections",
                json={
                    "api_key": "custom-secret",
                    "base_url": "http://localhost:11434/v1",
                },
            )

        self.assertEqual(missing.status_code, 400, missing.text)
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(
            self.router.ensured_nodes[-1]["base_url"],
            "http://localhost:11434/v1",
        )

    async def test_connection_usage(self):
        async with self.client() as client:
            usage = (await client.get(
                "/xnobrain/api/runtime/v1/providers/codex/connections/codex-1/usage")).json()["data"]
        self.assertTrue(usage["available"])
        self.assertEqual(usage["quotas"][0]["remaining_percent"], 80)

    async def test_no_forbidden_material_in_any_response(self):
        async with self.client() as client:
            await client.post("/xnobrain/api/runtime/v1/providers/openai/connections",
                              json={"api_key": "sk-secretXYZ"})
            texts = [
                (await client.get("/xnobrain/api/runtime/v1/providers/codex/connections")).text,
                (await client.get("/xnobrain/api/runtime/v1/providers")).text,
                (await client.get("/xnobrain/api/runtime/v1/providers/codex/connections/codex-1/usage")).text,
            ]
        for text in texts:
            for forbidden in _FORBIDDEN:
                self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
