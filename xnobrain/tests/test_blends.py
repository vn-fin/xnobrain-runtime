"""Runtime-owned blend persistence and direct-router execution tests."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import unittest.mock

from xnobrain.integrations.llm_router import LLMRouterClient
from xnobrain.services.blends import BlendService


class _Router(LLMRouterClient):
    def __init__(self, data_dir: Path):
        super().__init__(base_url="https://router.invalid", data_dir=data_dir)
        self.requests: list[tuple[str, str, dict | None]] = []

    async def list_models(self, *, ensure_auto: bool = True):
        self.requests.append(("GET", "/models?kind=llm", None))
        return {
            "data": [
                {"id": "auto", "provider": "xnobrain", "name": "Auto"},
                {"id": "openai/fast", "provider": "openai", "name": "Fast"},
                {"id": "anthropic/deep", "provider": "anthropic", "name": "Deep"},
            ]
        }

    async def _request(self, method: str, path: str, body=None):
        self.requests.append((method, path, body))
        if path == "/chat/completions":
            return {"choices": [{"message": {"content": "difficult"}}]}
        raise AssertionError(f"unexpected router request: {method} {path}")


class LocalBlendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name)
        self.router = _Router(self.data_dir)
        self.service = BlendService(self.router)

    async def test_create_update_delete_persist_locally_without_management_calls(self):
        created = await self.service.create_blend({
            "name": "duo",
            "models": ["openai/fast", "anthropic/deep"],
            "strategy": "round-robin",
            "sticky_limit": 3,
        })

        path = self.data_dir / "blends.json"
        stored = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(stored["combos"][0]["name"], "duo")
        self.assertEqual(created["strategy"], "round-robin")

        updated = await self.service.update_blend(created["id"], {"name": "duo-v2"})
        self.assertEqual(updated["name"], "duo-v2")
        deleted = await self.service.delete_blend(created["id"])
        self.assertTrue(deleted["deleted"])
        self.assertEqual((await self.service.list_blends())["blends"], [])
        self.assertFalse(any(path.startswith("/api/") for _, path, _ in self.router.requests))

    async def test_auto_randomizes_all_connected_models(self):
        with unittest.mock.patch("xnobrain.integrations.blends.random.SystemRandom.shuffle", side_effect=lambda rows: rows.reverse()):
            candidates = await self.router.auto_model_candidates()
        self.assertEqual(candidates, ["anthropic/deep", "openai/fast"])

    async def test_provider_auto_stays_on_the_selected_provider(self):
        candidates = await self.router.auto_model_candidates("openai")
        self.assertEqual(candidates, ["openai/fast"])

    async def test_auto_route_exposes_remaining_models_as_fallbacks(self):
        with unittest.mock.patch("xnobrain.integrations.blends.random.SystemRandom.shuffle", side_effect=lambda rows: rows.reverse()):
            route = await self.router.resolve_blend_route("auto", "hello")
        self.assertEqual(route["model"], "anthropic/deep")
        self.assertEqual(route["candidates"], ["anthropic/deep", "openai/fast"])

    async def test_round_robin_is_resolved_in_runtime(self):
        created = await self.service.create_blend({
            "name": "rotate",
            "models": ["openai/fast", "anthropic/deep"],
            "strategy": "round-robin",
        })

        first = await self.router.resolve_blend_route(created["name"], "hello")
        second = await self.router.resolve_blend_route(created["name"], "hello")

        self.assertEqual(first["model"], "openai/fast")
        self.assertEqual(second["model"], "anthropic/deep")
        self.assertFalse(any(path.startswith("/api/") for _, path, _ in self.router.requests))

    async def test_smart_route_uses_only_inference_endpoint(self):
        created = await self.service.create_blend({
            "name": "smart",
            "models": ["openai/fast", "anthropic/deep"],
            "strategy": "smart-route",
            "smart_route": {
                "quick": [{"model": "openai/fast", "reasoning": "low"}],
                "normal": [{"model": "openai/fast", "reasoning": "low"}],
                "difficult": [{"model": "anthropic/deep", "reasoning": "high"}],
            },
        })

        route = await self.router.resolve_blend_route(
            created["name"], "Design a secure distributed architecture"
        )

        self.assertEqual(route["model"], "anthropic/deep")
        self.assertIn(("POST", "/chat/completions"), [request[:2] for request in self.router.requests])
        self.assertFalse(any(path.startswith("/api/") for _, path, _ in self.router.requests))


if __name__ == "__main__":
    unittest.main()
