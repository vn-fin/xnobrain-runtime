"""Phase 0 probe for plan 011 (multi-account provider connections).

Verifies the live 9router endpoints and response shapes this plan depends on.
Skips cleanly when no 9router answers on :20128 (the rest of the suite fakes the
router; this is the only test that talks to a real one). ``test_pins_agree``
never skips.

Observed against provider runtime v0.5.55 (2026-08-17):
  * GET  /api/providers            -> {"connections": [ {id, provider, authType,
        name, email, priority, isActive, testStatus, lastError, ...,
        providerSpecificData} ]}     (no defaultModel; creds under
        providerSpecificData)
  * POST /api/providers            -> creates a row; accepts a fake api key
  * PUT  /api/providers/{id}        -> partial {"isActive": bool} / {"priority": int}
        accepted; returns {"connection": {...}}. NOTE: 9router re-normalizes
        priority (sent 5, stored 1) -> read-after-write is mandatory.
  * POST /api/providers/{id}/test   -> {"valid": bool, "error": str, "refreshed": bool}
  * GET  /api/usage/{id}            -> api-key conn: {"message": ...}, no quotas
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import unittest

import aiohttp

from xnobrain.integrations.nine_router import NineRouterManager

BASE = os.environ.get("NINE_ROUTER_URL", "http://127.0.0.1:20128")
_REPO = Path(__file__).resolve().parents[2]


async def _router_up() -> bool:
    try:
        timeout = aiohttp.ClientTimeout(total=2)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(BASE + "/api/providers") as response:
                return response.status in (200, 401)
    except Exception:
        return False


class NineRouterPinTests(unittest.TestCase):
    def test_pins_agree(self):
        """All three pinned 9router versions normalize to the same value."""
        dockerfile = (_REPO / "Dockerfile.backend").read_text(encoding="utf-8")
        installer = (_REPO / "scripts" / "install-linux.sh").read_text(encoding="utf-8")
        versions = set()
        for pattern in (
            r"ARG NINE_ROUTER_VERSION=v?([0-9]+\.[0-9]+\.[0-9]+)",
            r"ARG NINE_ROUTER_NPM_VERSION=v?([0-9]+\.[0-9]+\.[0-9]+)",
        ):
            match = re.search(pattern, dockerfile)
            self.assertIsNotNone(match, f"missing pin: {pattern}")
            versions.add(match.group(1))
        match = re.search(
            r"XNOBRAIN_NINE_ROUTER_VERSION:-v?([0-9]+\.[0-9]+\.[0-9]+)", installer)
        self.assertIsNotNone(match, "missing installer pin")
        versions.add(match.group(1))
        self.assertEqual(len(versions), 1, f"pins disagree: {versions}")

    def test_installer_bootstraps_pip_before_using_it(self):
        """A reusable Hermes venv may not include pip."""
        installer = (_REPO / "scripts" / "install-linux.sh").read_text(encoding="utf-8")
        bootstrap = 'if ! "$project_python" -m pip --version >/dev/null 2>&1; then'
        self.assertIn(bootstrap, installer)
        self.assertIn('"$project_python" -m ensurepip --upgrade', installer)
        self.assertLess(
            installer.index(bootstrap),
            installer.index('"$project_python" -m pip install -r "$project_dir/requirements.txt"'),
        )

    def test_launchers_accept_router_token_without_trailing_newline(self):
        expected = {
            "scripts/dev.sh": (
                'NINE_ROUTER_API_KEY="$(< "$router_data_dir/auth/cli-token")"'
            ),
            "runtime/container-entrypoint.sh": (
                'NINE_ROUTER_API_KEY="$(< "$NINE_ROUTER_DATA_DIR/auth/cli-token")"'
            ),
        }
        for relative_path, assignment in expected.items():
            launcher = (_REPO / relative_path).read_text(encoding="utf-8")
            self.assertIn(assignment, launcher)
            self.assertNotIn("IFS= read -r NINE_ROUTER_API_KEY", launcher)

    def test_runtime_packages_the_local_honcho_client_and_services(self):
        dockerfile = (_REPO / "Dockerfile.backend").read_text(encoding="utf-8")
        compose = (_REPO / "docker-compose.yaml").read_text(encoding="utf-8")
        environment_path = _REPO / ".env.example"
        if not environment_path.is_file():
            environment_path = _REPO.parent / ".env.example"
        environment = environment_path.read_text(encoding="utf-8")

        self.assertIn("'honcho-ai==2.2.0'", dockerfile)
        self.assertRegex(environment, r"(?m)^HONCHO_MEMORY_ENABLE=(true|false)$")
        for service in (
            "honcho-api:",
            "honcho-deriver:",
            "honcho-database:",
            "honcho-redis:",
        ):
            self.assertIn(service, compose)
        self.assertIn("HONCHO_BASE_URL: http://honcho-api:8000", compose)
        self.assertIn("TELEMETRY_ENABLED: \"false\"", compose)


class NineRouterLiveProbeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if not await _router_up():
            self.skipTest("9router is not running on " + BASE)
        self.manager = NineRouterManager(base_url=BASE)

    async def _create_throwaway(self) -> str:
        created = await self.manager._request(
            "POST", "/api/providers",
            {"provider": "openai", "apiKey": "sk-plan011-probe", "name": "plan011-probe"},
        )
        connection = created.get("connection") or created
        connection_id = str(connection.get("id") or "")
        self.assertTrue(connection_id, "create did not return an id")

        async def _cleanup():
            try:
                await self.manager._request("DELETE", f"/api/providers/{connection_id}")
            except Exception:
                pass

        self.addAsyncCleanup(_cleanup)
        return connection_id

    async def _list(self) -> dict[str, dict]:
        payload = await self.manager._request("GET", "/api/providers")
        return {str(item.get("id")): item for item in payload.get("connections", [])}

    async def test_list_connections_shape(self):
        rows = await self._list()
        for item in rows.values():
            for key in ("id", "provider", "authType", "isActive"):
                self.assertIn(key, item)

    async def test_connection_lifecycle_put_isactive_and_priority(self):
        connection_id = await self._create_throwaway()
        r1 = await self.manager._request(
            "PUT", f"/api/providers/{connection_id}", {"isActive": False})
        self.assertIn("connection", r1)
        self.assertFalse((await self._list())[connection_id].get("isActive"))
        # 9router re-normalizes priority; assert it is applied, not that it equals 5.
        await self.manager._request(
            "PUT", f"/api/providers/{connection_id}", {"priority": 5})
        self.assertIsNotNone((await self._list())[connection_id].get("priority"))

    async def test_test_endpoint_shape(self):
        connection_id = await self._create_throwaway()
        result = await self.manager._request(
            "POST", f"/api/providers/{connection_id}/test", {})
        self.assertIn("valid", result)
        self.assertFalse(result.get("valid"))  # dummy key must not validate

    async def test_usage_for_connection_shape(self):
        connection_id = await self._create_throwaway()
        payload = await self.manager._request("GET", f"/api/usage/{connection_id}")
        self.assertIsInstance(payload, dict)  # quotas may be absent for api-key conns


if __name__ == "__main__":
    unittest.main()
