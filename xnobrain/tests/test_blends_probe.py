"""Phase 0 probe for plan 012 (Model Blends over 9router combos).

Re-proves the combo/settings/model behaviors the plan depends on against a
running pinned 9router. This mutating probe is opt-in so routine test discovery
cannot create combos in a developer's live router. Restores the settings it
touches.

Observed against 9router v0.5.40 (2026-07-25):
  * GET  /api/combos          -> {"combos": [{id,name,kind,models,createdAt,updatedAt}]}
  * POST /api/combos          -> the combo object DIRECTLY (top-level id)
  * PUT  /api/combos/{id}      -> partial (models-only keeps name)
  * DELETE /api/combos/{id}    -> {"success": true}; unknown id -> 404
  * combo shows in /v1/models?kind=llm as {"id": name, "owned_by": "combo"}
  * name "bad name!" -> 400
  * GET  /api/settings         -> object, no "password"; has comboStrategy,
        comboStrategies, comboStickyRoundRobinLimit
  * PATCH /api/settings {"comboStrategies": {...}} replaces the WHOLE map
"""

from __future__ import annotations

import os
import unittest

import aiohttp

from xnobrain.integrations.nine_router import NineRouterAPIError, NineRouterManager

BASE = os.environ.get("NINE_ROUTER_URL", "http://127.0.0.1:20128")
RUN_LIVE_PROBE = os.environ.get("RUN_LIVE_NINE_ROUTER_PROBES") == "1"


async def _router_up() -> bool:
    try:
        timeout = aiohttp.ClientTimeout(total=2)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(BASE + "/api/combos") as response:
                return response.status in (200, 401)
    except Exception:
        return False


class BlendsLiveProbeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if not RUN_LIVE_PROBE:
            self.skipTest("set RUN_LIVE_NINE_ROUTER_PROBES=1 to run mutating live probes")
        if not await _router_up():
            self.skipTest("9router is not running on " + BASE)
        self.manager = NineRouterManager(base_url=BASE)
        self.name = f"b4a-probe-{os.getpid()}"
        # snapshot comboStrategies so we can restore it exactly.
        settings = await self.manager._request("GET", "/api/settings")
        self._prior_strategies = dict(settings.get("comboStrategies") or {}) if isinstance(settings, dict) else {}

    async def _models(self) -> list[str]:
        data = (await self.manager._request("GET", "/v1/models?kind=llm")).get("data", [])
        return [row["id"] for row in data if "/" in str(row.get("id", ""))][:2]

    async def test_combo_lifecycle_and_models_surface(self):
        models = await self._models()
        if len(models) < 1:
            self.skipTest("no real models connected to build a combo")
        created = await self.manager._request("POST", "/api/combos", {"name": self.name, "models": models})
        combo = created.get("combo") or created
        combo_id = str(combo.get("id") or "")
        self.assertTrue(combo_id, "create did not return an id")
        self.addAsyncCleanup(lambda: self.manager._request("DELETE", f"/api/combos/{combo_id}"))

        listed = (await self.manager._request("GET", "/api/combos")).get("combos", [])
        row = next((c for c in listed if c["id"] == combo_id), {})
        self.assertEqual(row.get("name"), self.name)
        self.assertIsInstance(row.get("models"), list)

        models_payload = (await self.manager._request("GET", "/v1/models?kind=llm")).get("data", [])
        blend_row = next((m for m in models_payload if m.get("id") == self.name), None)
        self.assertIsNotNone(blend_row)
        self.assertEqual(blend_row.get("owned_by"), "combo")

        # PUT partial: models-only keeps the name.
        await self.manager._request("PUT", f"/api/combos/{combo_id}", {"models": models[:1]})
        row = next(c for c in (await self.manager._request("GET", "/api/combos"))["combos"] if c["id"] == combo_id)
        self.assertEqual(row["name"], self.name)

    async def test_bad_name_rejected(self):
        with self.assertRaises(NineRouterAPIError):
            await self.manager._request("POST", "/api/combos", {"name": "bad name!", "models": []})

    async def test_settings_whole_map_replace(self):
        async def _restore():
            await self.manager._request("PATCH", "/api/settings", {"comboStrategies": self._prior_strategies})
        self.addAsyncCleanup(_restore)

        await self.manager._request("PATCH", "/api/settings", {"comboStrategies": {"probeA": {"fallbackStrategy": "round-robin"}}})
        await self.manager._request("PATCH", "/api/settings", {"comboStrategies": {"probeB": {"fallbackStrategy": "fusion", "judgeModel": "x"}}})
        settings = await self.manager._request("GET", "/api/settings")
        self.assertNotIn("password", settings)
        strategies = settings.get("comboStrategies") or {}
        self.assertIn("probeB", strategies)
        self.assertNotIn("probeA", strategies)  # whole-map replace


if __name__ == "__main__":
    unittest.main()
