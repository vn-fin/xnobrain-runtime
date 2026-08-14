"""Brain-owned Swagger and OpenAPI route tests."""

import unittest

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from xnobrain.openapi_docs import (
    DOCS_URL,
    OAUTH2_REDIRECT_URL,
    OPENAPI_URL,
    configure_openapi_docs,
)


class OpenAPIDocsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = FastAPI(title="XNOBrain Test")

        @self.app.get("/xnobrain/api/runtime/v1/health")
        async def health():
            return {"status": "ok"}

        configure_openapi_docs(self.app)
        self.client = AsyncClient(
            transport=ASGITransport(app=self.app),
            base_url="http://test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_swagger_ui_and_schema_use_brain_namespace(self):
        docs = await self.client.get(DOCS_URL)
        schema = await self.client.get(OPENAPI_URL)
        redirect = await self.client.get(OAUTH2_REDIRECT_URL)

        self.assertEqual(docs.status_code, 200)
        self.assertIn(OPENAPI_URL, docs.text)
        self.assertEqual(schema.status_code, 200)
        self.assertIn("/xnobrain/api/runtime/v1/health", schema.json()["paths"])
        self.assertEqual(redirect.status_code, 200)

    async def test_root_fastapi_documentation_routes_are_removed(self):
        for path in ("/docs", "/redoc", "/openapi.json"):
            with self.subTest(path=path):
                response = await self.client.get(path)
                self.assertEqual(response.status_code, 404)

    async def test_configuration_is_idempotent(self):
        route_count = len(self.app.router.routes)
        configure_openapi_docs(self.app)
        self.assertEqual(len(self.app.router.routes), route_count)


if __name__ == "__main__":
    unittest.main()
