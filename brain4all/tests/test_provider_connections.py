"""Route + service tests for multi-account provider connections (plan 011).

Uses a stateful fake that mimics the NineRouterManager adapter contract
(already-filtered rows, no credential material) so the service/route layer is
exercised without a live 9router.
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import yaml

from brain4all.app import Brain4AllApplication
from brain4all.integrations import AgentManager, GlobalConfigManager


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

    async def list_connections(self):
        return {"connections": [dict(row) for row in self._rows]}

    async def list_models(self):
        return {"data": []}

    async def create_api_key_connection(self, body):
        self.created_bodies.append(dict(body))  # captures the key but never returns it
        row = {
            "id": f"openai-{len(self._rows)}", "provider": body["provider"],
            "auth_type": "api-key", "name": body.get("name") or "", "email": "",
            "active": True, "priority": 0, "default_model": body.get("default_model") or "",
            "test_status": "unknown", "last_error": "",
        }
        self._rows.append(row)
        return {"object": "nine_router.provider", "connection": dict(row)}

    async def update_connection(self, connection_id, *, active=None, priority=None):
        for row in self._rows:
            if row["id"] == connection_id:
                if active is not None:
                    row["active"] = active
                if priority is not None:
                    row["priority"] = priority
                return {"object": "nine_router.provider", "connection": dict(row)}
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


class ProviderConnectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        root = base / "root"
        profiles = base / "profiles"
        root.mkdir(parents=True)
        profiles.mkdir(parents=True)
        (root / "config.yaml").write_text(yaml.safe_dump({
            "model": {"provider": "custom:nine-router", "default": "auto"},
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
        Brain4AllApplication(
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

    async def test_list_connections_sorted_and_connected(self):
        async with self.client() as client:
            data = (await client.get("/agent-gateway/v1/providers/codex/connections")).json()["data"]
        self.assertTrue(data["connected"])
        self.assertEqual([c["id"] for c in data["connections"]], ["codex-1", "codex-2"])
        for connection in data["connections"]:
            self._assert_connection_shape(connection)

    async def test_ownership_guard_404_across_providers(self):
        async with self.client() as client:
            # codex-1 belongs to codex, not openai
            for path in (
                "/agent-gateway/v1/providers/openai/connections/codex-1/usage",
                "/agent-gateway/v1/providers/openai/connections/codex-1/test",
            ):
                method = client.post if path.endswith("/test") else client.get
                self.assertEqual((await method(path)).status_code, 404)

    async def test_add_api_key_account_never_leaks_key(self):
        async with self.client() as client:
            response = await client.post(
                "/agent-gateway/v1/providers/openai/connections",
                json={"api_key": "sk-secret123", "name": "second"})
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("sk-secret123", response.text)
        self._assert_connection_shape(response.json()["data"]["connection"])
        # the key WAS passed through to the router (proving it reached 9router)
        self.assertEqual(self.router.created_bodies[-1]["api_key"], "sk-secret123")

    async def test_oauth_provider_rejects_api_key_add(self):
        async with self.client() as client:
            response = await client.post(
                "/agent-gateway/v1/providers/codex/connections", json={"api_key": "x"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "oauth_connect_required")

    async def test_patch_activates_reorders_and_requires_a_field(self):
        async with self.client() as client:
            activated = await client.patch(
                "/agent-gateway/v1/providers/codex/connections/codex-2", json={"active": True})
            self.assertTrue(activated.json()["data"]["connection"]["active"])
            self.assertEqual(
                (await client.patch(
                    "/agent-gateway/v1/providers/codex/connections/codex-2",
                    json={"priority": 0})).status_code, 200)
            self.assertEqual(
                (await client.patch(
                    "/agent-gateway/v1/providers/codex/connections/codex-2",
                    json={})).status_code, 400)

    async def test_delete_one_keeps_provider_connected(self):
        async with self.client() as client:
            deleted = await client.delete(
                "/agent-gateway/v1/providers/codex/connections/codex-2")
            self.assertEqual(deleted.status_code, 200)
            self.assertTrue(deleted.json()["data"]["deleted"])
            self.assertTrue(deleted.json()["data"]["connected"])  # codex-1 still active
            remaining = (await client.get(
                "/agent-gateway/v1/providers/codex/connections")).json()["data"]
            self.assertEqual([c["id"] for c in remaining["connections"]], ["codex-1"])

    async def test_providers_backward_compat_reports_connection_count(self):
        async with self.client() as client:
            providers = (await client.get("/agent-gateway/v1/providers")).json()["data"]
        codex = next(item for item in providers if item["id"] == "codex")
        self.assertTrue(codex["connected"])          # one active of two
        self.assertEqual(codex["connection_count"], 2)

    async def test_connection_usage(self):
        async with self.client() as client:
            usage = (await client.get(
                "/agent-gateway/v1/providers/codex/connections/codex-1/usage")).json()["data"]
        self.assertTrue(usage["available"])
        self.assertEqual(usage["quotas"][0]["remaining_percent"], 80)

    async def test_no_forbidden_material_in_any_response(self):
        async with self.client() as client:
            await client.post("/agent-gateway/v1/providers/openai/connections",
                              json={"api_key": "sk-secretXYZ"})
            texts = [
                (await client.get("/agent-gateway/v1/providers/codex/connections")).text,
                (await client.get("/agent-gateway/v1/providers")).text,
                (await client.get("/agent-gateway/v1/providers/codex/connections/codex-1/usage")).text,
            ]
        for text in texts:
            for forbidden in _FORBIDDEN:
                self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
