"""Deployment feature switches fail closed at the Runtime boundary."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI

from xnobrain.feature_flags import (
    FEATURE_AGENT_CUSTOM_PAGE,
    FEATURE_UI_CUSTOMIZATION,
    enabled,
)
from xnobrain.routes.setup import setup_routes


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


if __name__ == "__main__":
    unittest.main()
