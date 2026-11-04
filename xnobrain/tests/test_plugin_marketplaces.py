"""Marketplace parsing, ownership, persistence, and route contract tests."""

from __future__ import annotations

import json
import os
import tempfile
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
from xnobrain.services.extensions import ExtensionsService
from xnobrain.trusted_context import (
    TRUSTED_ORGANIZATION_HEADER,
    TRUSTED_SIGNATURE_HEADER,
    TRUSTED_SUBJECT_HEADER,
    TRUSTED_TENANT_HEADER,
    principal_signature,
)

TOKEN = "marketplace-test-token"
ROOT = "/xnobrain/api/runtime/v1"


class FakeAgents:
    def __init__(self, profile):
        self.profile = profile

    def profile_path(self, agent_id):
        if agent_id != "research":
            raise ValueError("unknown profile")
        return self.profile


class MarketplaceHandlers(APIHandlers):
    def _operation(self, name, request, body):
        return extension_operations(self, request, body)[name]


class PluginMarketplaceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.data = Path(self.temporary.name) / "data"
        self.profile = self.data / "profiles" / "research"
        self.profile.mkdir(parents=True)
        self.repository = FileRepository(self.data, self.data / "profiles")
        self.service = ExtensionsService(self.repository, FakeAgents(self.profile))
        self.app = FastAPI()
        self.env = patch.dict(
            os.environ,
            {"RUNTIME_INTERNAL_SERVICE_TOKEN": TOKEN, "FT_ENABLE_AGENT_EXTENSIONS": "true"},
            clear=True,
        )
        self.env.start()
        setup_routes(self.app, MarketplaceHandlers(SimpleNamespace(extensions=self.service)))
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://runtime"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.env.stop()
        self.temporary.cleanup()

    @staticmethod
    def headers(subject="person", tenant="tenant", organization=""):
        return {
            TRUSTED_SUBJECT_HEADER: subject,
            TRUSTED_TENANT_HEADER: tenant,
            TRUSTED_ORGANIZATION_HEADER: organization,
            TRUSTED_SIGNATURE_HEADER: principal_signature(TOKEN, subject, tenant, organization),
        }

    def clone_for(self, manifest_path, manifest):
        def clone(source, ref, stage, *, artifact=True):
            # A marketplace repo is not a plugin; per-plugin artifact limits must not apply.
            assert artifact is False
            stage.mkdir(parents=True)
            target = stage / manifest_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(manifest), encoding="utf-8")
            (stage / "plugins" / "sample").mkdir(parents=True, exist_ok=True)
            return "a" * 40

        return clone

    async def test_marketplace_sources_entries_skips_duplicate_delete_and_persistence(self):
        manifest = {
            "name": "Community",
            "plugins": [
                {"name": "local-one", "description": "Local", "source": "./plugins/sample"},
                {
                    "name": "github-one",
                    "source": {"source": "github", "repo": "acme/thing", "ref": "main"},
                },
                {"name": "npm-one", "source": {"source": "npm", "package": "x"}},
                {"name": "bad/name", "source": "./plugins/sample"},
            ],
        }
        with patch.object(
            extension_engine,
            "clone_plugin",
            self.clone_for(".claude-plugin/marketplace.json", manifest),
        ):
            added = await self.client.post(
                f"{ROOT}/plugins/marketplaces",
                headers=self.headers(),
                json={"source": "acme/plugins"},
            )
        self.assertEqual(added.status_code, 201, added.text)
        marketplace = added.json()["data"]
        self.assertEqual(marketplace["entries"][0]["ref"], "a" * 40)
        self.assertEqual(marketplace["entries"][0]["path"], "plugins/sample")
        self.assertEqual(marketplace["entries"][1]["source"], "https://github.com/acme/thing.git")
        self.assertEqual(marketplace["skipped"][0]["reason"], "unsupported_source")
        self.assertEqual(marketplace["skipped"][1]["reason"], "invalid_name")
        duplicate = await self.client.post(
            f"{ROOT}/plugins/marketplaces",
            headers=self.headers(),
            json={"source": "https://github.com/acme/plugins.git"},
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json()["error"]["code"], "marketplace_exists")
        new_service = ExtensionsService(self.repository, FakeAgents(self.profile))
        self.assertEqual(
            new_service.list_marketplaces(
                SimpleNamespace(
                    subject="person", tenant_id="tenant", organization_id="", ownership_context=None
                )
            )["marketplaces"],
            [marketplace],
        )
        deleted = await self.client.delete(
            f"{ROOT}/plugins/marketplaces/{marketplace['id']}", headers=self.headers()
        )
        self.assertEqual(deleted.json()["data"], {"deleted": True})
        self.assertEqual(
            self.service.list_marketplaces(
                SimpleNamespace(
                    subject="person", tenant_id="tenant", organization_id="", ownership_context=None
                )
            )["marketplaces"],
            [],
        )
        self.assertEqual(self.service.store.list("research", "tenant\0person"), [])

    async def test_agents_manifest_and_missing_manifest_and_identity(self):
        manifest = {
            "plugins": [
                {
                    "name": "codex",
                    "source": {"source": "local", "path": "./skills/codex"},
                    "metadata": {"pluginRoot": "./root"},
                }
            ]
        }
        with patch.object(
            extension_engine,
            "clone_plugin",
            self.clone_for(".agents/plugins/marketplace.json", manifest),
        ):
            added = await self.client.post(
                f"{ROOT}/plugins/marketplaces",
                headers=self.headers(),
                json={"source": "acme/codex#main"},
            )
        self.assertEqual(added.status_code, 201, added.text)
        entry = added.json()["data"]["entries"][0]
        self.assertEqual(entry["path"], "root/skills/codex")
        with patch.object(
            extension_engine,
            "clone_plugin",
            lambda _s, _r, stage, **_kwargs: (stage.mkdir(parents=True), "b" * 40)[1],
        ):
            missing = await self.client.post(
                f"{ROOT}/plugins/marketplaces",
                headers=self.headers(),
                json={"source": "acme/missing"},
            )
        self.assertEqual(missing.status_code, 422)
        self.assertEqual(missing.json()["error"]["code"], "marketplace_manifest_missing")
        identity = await self.client.get(f"{ROOT}/plugins/marketplaces")
        self.assertEqual(identity.status_code, 401)
        organization = await self.client.get(
            f"{ROOT}/plugins/marketplaces", headers=self.headers(organization="org")
        )
        self.assertEqual(organization.status_code, 403)
        self.assertEqual(organization.json()["error"]["code"], "permission_denied")


if __name__ == "__main__":
    unittest.main()
