"""SQL tool validation and per-run capability isolation."""

import asyncio
import json
import os
import unittest
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from xnobrain.integrations import xno_data_tools as tools
from xnobrain.trusted_context import TrustedRequestContext


class XNODataTests(unittest.IsolatedAsyncioTestCase):
    def test_sql_contract_rejects_credentials_and_old_arguments(self):
        self.assertEqual(
            tools.DataRequest(sql="SELECT 1").model_dump(exclude_none=True), {"sql": "SELECT 1"}
        )
        for args in (
            {"operation": "query"},
            {"sql": "SELECT 1", "token": "secret"},
            {"sql": "SELECT 1", "limit": 1001},
        ):
            with self.assertRaises(ValidationError):
                tools.DataRequest.model_validate(args)
        self.assertNotIn("secret", repr(TrustedRequestContext(xno_data_grant="secret")))

    async def test_binding_removed_at_end_and_foreign_session_denied(self):
        environment = {"FT_ENABLE_XNO_DATA": "true", "RUNTIME_CONTROL_URL": "http://control"}
        with patch.dict(os.environ, environment), patch.object(tools, "register_tools"):
            with tools.bind_run("session", "run", "secret"):
                self.assertIn("session", tools._BINDINGS)
                result = await asyncio.to_thread(
                    tools.call, {"sql": "SELECT 1"}, session_id="foreign"
                )
                self.assertEqual(json.loads(result)["error"]["code"], "xno_data_authority_required")
            self.assertNotIn("session", tools._BINDINGS)

    async def test_http_payload_and_error_redaction(self):
        real_client = httpx.AsyncClient
        status = 200

        def handler(request):
            self.assertEqual(request.headers["Authorization"], "Bearer capability")
            self.assertEqual(
                json.loads(request.content),
                {"sql": "SELECT 1", "session_id": "session", "run_id": "run"},
            )
            if status != 200:
                return httpx.Response(status, text="private upstream credential")
            return httpx.Response(200, json={"success": True, "data": {"row_count": 1}})

        def client(**kwargs):
            return real_client(transport=httpx.MockTransport(handler), **kwargs)

        with (
            patch.dict(os.environ, {"RUNTIME_CONTROL_URL": "http://control"}),
            patch.object(tools.httpx, "AsyncClient", client),
        ):
            result = await tools._execute("capability", "session", "run", {"sql": "SELECT 1"})
            self.assertTrue(json.loads(result)["success"])
            status = 401
            result = await tools._execute("capability", "session", "run", {"sql": "SELECT 1"})
            self.assertNotIn("private upstream", result)
            self.assertEqual(json.loads(result)["error"]["code"], "xno_data_auth_expired")
