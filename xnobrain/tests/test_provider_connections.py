"""Regression coverage for Control-owned provider administration."""

import unittest

from xnobrain.integrations.llm_router import LLMRouterClient
from xnobrain.routes.providers import ROUTES
from xnobrain.services.helpers import MemoryCache
from xnobrain.services.providers import ProvidersServiceMixin


class _Router:
    def __init__(self):
        self.requests: list[tuple[str, str]] = []

    async def list_models(self):
        self.requests.append(("GET", "/models?kind=llm"))
        return {
            "data": [
                {"id": "auto", "provider": "xnobrain"},
                {
                    "id": "openai/gpt-5",
                    "provider": "openai",
                    "reasoning_levels": ["low", "high"],
                    "assignments": [
                        {
                            "id": "llma_org",
                            "owner_type": "organization",
                            "organization_id": "acme",
                        }
                    ],
                },
            ]
        }


class _Service(ProvidersServiceMixin):
    def __init__(self):
        self.router = _Router()
        self._cache = MemoryCache()


class ProviderCatalogTests(unittest.IsolatedAsyncioTestCase):
    def test_runtime_has_no_provider_management_routes(self):
        paths = {route.path for route in ROUTES}

        self.assertTrue(any(path.endswith("/providers") for path in paths))
        self.assertTrue(any(path.endswith("/providers/{provider_id}/models") for path in paths))
        self.assertFalse(any("connections" in path for path in paths))
        self.assertFalse(any(path.endswith("/connect") for path in paths))
        self.assertFalse(any(path.endswith("/disconnect") for path in paths))
        self.assertFalse(any(path.endswith("/test") for path in paths))

    async def test_catalog_uses_only_workload_scoped_models(self):
        service = _Service()

        providers = await service.providers()
        openai = next(item for item in providers if item["id"] == "openai")

        self.assertTrue(openai["connected"])
        self.assertEqual(openai["connection_mode"], "managed")
        self.assertEqual(openai["available_models"], ["openai/gpt-5"])
        self.assertEqual(
            openai["model_assignments"]["openai/gpt-5"][0]["id"],
            "llma_org",
        )
        self.assertEqual(service.router.requests, [("GET", "/models?kind=llm")])
        self.assertTrue(
            next(item for item in providers if item["id"] == "openai-like")["requires_base_url"]
        )

    async def test_catalog_maps_gorouter_owned_models_by_public_prefix(self):
        class Router(LLMRouterClient):
            async def _request(self, *_args, **_kwargs):
                return {"data": [{"id": "ocz/gpt-5.6-luna", "owned_by": "gorouter"}]}

        catalog = await Router().list_models()

        self.assertEqual(catalog["data"][1]["provider"], "opencode")

    async def test_reasoning_catalog_remains_read_only(self):
        service = _Service()

        result = await service.provider_models("openai")

        self.assertEqual(result["models"][1]["id"], "openai/gpt-5")
        self.assertEqual(result["models"][1]["reasoning"], ["low", "high"])
        self.assertEqual(result["models"][1]["assignments"][0]["organization_id"], "acme")
        self.assertEqual(service.router.requests, [("GET", "/models?kind=llm")])


if __name__ == "__main__":
    unittest.main()
