"""Deployment feature switches fail closed at the Runtime boundary."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI

from xnobrain.feature_flags import (
    FEATURE_AGENT_CUSTOM_PAGE,
    FEATURE_AGENT_PORTABILITY,
    FEATURE_UI_CUSTOMIZATION,
    enabled,
)
from xnobrain.repositories.base import StoreError
from xnobrain.routes.setup import setup_routes
from xnobrain.services.portability import PortabilityServiceMixin


class HandlerStub:
    async def dispatch(self, request, body):  # pragma: no cover - route is not called
        raise AssertionError("disabled feature route was called")


class FeatureFlagTests(unittest.TestCase):
    def test_flags_default_off_and_parse_explicit_true(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(enabled(FEATURE_AGENT_CUSTOM_PAGE))
            self.assertFalse(enabled(FEATURE_UI_CUSTOMIZATION))
            self.assertTrue(enabled("FUTURE_STABLE_FEATURE"))
        for value in ("true", "TRUE", "1", "yes", "on"):
            with patch.dict(os.environ, {"FT_ENABLE_AGENT_CUSTOM_PAGE": value}, clear=True):
                self.assertTrue(enabled(FEATURE_AGENT_CUSTOM_PAGE))
        with patch.dict(
            os.environ,
            {"FT_ENABLE_AGENT_CUSTOM_PAGE": "invalid"},
            clear=True,
        ):
            self.assertFalse(enabled(FEATURE_AGENT_CUSTOM_PAGE))

    def test_disabled_routes_are_not_registered(self):
        with patch.dict(
            os.environ,
            {
                "FT_ENABLE_UI_CUSTOMIZATION": "false",
                "FT_ENABLE_AGENT_CUSTOM_PAGE": "false",
            },
            clear=True,
        ):
            app = FastAPI()
            setup_routes(app, HandlerStub())
        paths = {route.path for route in app.routes}
        self.assertNotIn("/xnobrain/api/runtime/v1/agents/{agent_id}/custom-page", paths)
        self.assertNotIn("/xnobrain/api/runtime/v1/ui-assistance", paths)
        self.assertIn("/xnobrain/api/runtime/v1/health", paths)

    def test_enabled_routes_are_registered(self):
        with patch.dict(
            os.environ,
            {
                "FT_ENABLE_UI_CUSTOMIZATION": "true",
                "FT_ENABLE_AGENT_CUSTOM_PAGE": "true",
            },
            clear=True,
        ):
            app = FastAPI()
            setup_routes(app, HandlerStub())
        paths = {route.path for route in app.routes}
        self.assertIn("/xnobrain/api/runtime/v1/agents/{agent_id}/custom-page", paths)
        self.assertIn("/xnobrain/api/runtime/v1/ui-assistance", paths)

    def test_disabled_portability_keeps_example_import_path(self):
        with patch.dict(os.environ, {"FT_ENABLE_AGENT_PORTABILITY": "false"}):
            app = FastAPI()
            setup_routes(app, HandlerStub())
        paths = {route.path for route in app.routes}
        prefix = "/xnobrain/api/runtime/v1/bundles"
        self.assertNotIn(f"{prefix}/export-tasks", paths)
        self.assertNotIn(f"{prefix}/task-exports/{{transfer_id}}/parts/{{part_number}}", paths)
        self.assertNotIn(f"{prefix}/apply", paths)
        self.assertIn(f"{prefix}/import-tasks", paths)
        self.assertIn(f"{prefix}/example-capabilities", paths)
        with patch.dict(os.environ, {"FT_ENABLE_AGENT_PORTABILITY": "invalid"}):
            self.assertFalse(enabled(FEATURE_AGENT_PORTABILITY))

    def test_disabled_portability_rejects_ordinary_upload_and_apply(self):
        service = PortabilityServiceMixin()
        with patch.dict(os.environ, {"FT_ENABLE_AGENT_PORTABILITY": "false"}):
            with self.assertRaises(StoreError) as upload:
                service.start_bundle_upload({"filename": "agent.zip", "size": 1})
            with self.assertRaises(StoreError) as apply:
                service.apply_bundle_upload("existing-upload", {})
        self.assertEqual(upload.exception.status, 404)
        self.assertEqual(apply.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
