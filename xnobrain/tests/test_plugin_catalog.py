"""Trusted catalog recipe contract for Control-owned plugin installs."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from xnobrain.integrations import extension_engine
from xnobrain.repositories import FileRepository
from xnobrain.services.base import ServiceError
from xnobrain.services.extensions import ExtensionsService
from xnobrain.trusted_context import (
    TRUSTED_CATALOG_RECIPE_HEADER,
    TRUSTED_CATALOG_RECIPE_SIGNATURE_HEADER,
    TRUSTED_ORGANIZATION_HEADER,
    TRUSTED_SIGNATURE_HEADER,
    TRUSTED_SUBJECT_HEADER,
    TRUSTED_TENANT_HEADER,
    TrustedRequestContext,
    catalog_recipe_signature,
    encode_catalog_recipe,
    from_request,
    principal_signature,
)

TOKEN = "plugin-catalog-test-token"
RECIPE = {
    "id": "context7",
    "kind": "mcp",
    "recipe_revision": 1,
    "installability": "installable",
    "mcp": {
        "transport": "http",
        "endpoint_mode": "static",
        "url": "https://mcp.context7.com/mcp",
        "url_policy": "",
        "auth": {"kind": "api_key", "field": "token", "delivery": "header"},
    },
}
CONNECTION_ID = "8b1a8e93-748b-46b0-8d97-d4d2c7a6b221"


class FakeAgents:
    def __init__(self, profile: Path):
        self.profile = profile

    def profile_path(self, _agent_id: str) -> Path:
        return self.profile


class PluginCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        data = Path(self.temporary.name) / "data"
        profiles = data / "profiles"
        self.profile = profiles / "research"
        self.profile.mkdir(parents=True)
        self.repository = FileRepository(data, profiles)
        self.service = ExtensionsService(self.repository, FakeAgents(self.profile))
        self.trusted = TrustedRequestContext(subject="person-1", tenant_id="tenant-1")

    def tearDown(self):
        self.temporary.cleanup()

    def test_runtime_accepts_only_a_signed_control_recipe(self):
        encoded = encode_catalog_recipe(RECIPE)
        signature = catalog_recipe_signature(TOKEN, "person-1", "tenant-1", "", encoded)
        request = httpx.Request(
            "POST",
            "http://runtime/xnobrain/api/runtime/v1/agents-plugins/research/install-catalog",
            headers={
                TRUSTED_SUBJECT_HEADER: "person-1",
                TRUSTED_TENANT_HEADER: "tenant-1",
                TRUSTED_ORGANIZATION_HEADER: "",
                TRUSTED_SIGNATURE_HEADER: principal_signature(TOKEN, "person-1", "tenant-1", ""),
                TRUSTED_CATALOG_RECIPE_HEADER: encoded,
                TRUSTED_CATALOG_RECIPE_SIGNATURE_HEADER: signature,
            },
        )
        with patch.dict(os.environ, {"RUNTIME_INTERNAL_SERVICE_TOKEN": TOKEN}, clear=True):
            trusted = from_request(request)
            self.assertEqual(trusted.catalog_recipe, RECIPE)

            request.headers[TRUSTED_CATALOG_RECIPE_SIGNATURE_HEADER] = "0" * 64
            self.assertIsNone(from_request(request).catalog_recipe)

    def test_install_catalog_never_reads_a_runtime_catalog_file(self):
        body = {
            "catalog_id": "context7",
            "recipe_revision": 1,
            "connection_id": CONNECTION_ID,
            "fields": {},
        }
        with self.assertRaises(ServiceError) as missing:
            self.service.install_catalog("research", body, self.trusted)
        self.assertEqual(missing.exception.code, "catalog_recipe_unavailable")

        mismatched = TrustedRequestContext(
            subject="person-1", tenant_id="tenant-1", catalog_recipe={**RECIPE, "id": "linear"}
        )
        with self.assertRaises(ServiceError) as invalid:
            self.service.install_catalog("research", body, mismatched)
        self.assertEqual(invalid.exception.code, "invalid_catalog_recipe")

        with (
            patch.object(extension_engine, "mcp_profile_isolation_supported", return_value=True),
            patch.dict(
                os.environ,
                {"RUNTIME_CONTROL_URL": "http://control.internal/"},
                clear=True,
            ),
        ):
            result = self.service.install_catalog(
                "research",
                body,
                TrustedRequestContext(
                    subject="person-1",
                    tenant_id="tenant-1",
                    catalog_recipe=RECIPE,
                ),
            )
        self.assertTrue(result["configured"])
        self.assertEqual(result["catalog_id"], "context7")

    def test_capabilities_report_managed_provider_packages(self):
        capabilities = self.service.capabilities()
        self.assertEqual(capabilities["managed_provider_packages"], {"schema_versions": [1]})
        self.assertNotIn("native_packages", capabilities)


if __name__ == "__main__":
    unittest.main()
