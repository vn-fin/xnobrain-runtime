"""Managed provider tools preserve profile and connection authority."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx

from xnobrain.integrations import managed_providers as providers


class ManagedProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.profile = Path(self.temp.name) / "agent"
        self.profile.mkdir()
        manifest = self.profile / "plugins" / "kiotviet" / "plugin.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("provides_tools: [kiotviet_read, kiotviet_write]\n")
        self.connection = str(uuid4())
        self.calls = []
        self.response = {"success": True, "data": {"data": [{"id": 1}]}}
        self.status = 200
        self.stack = []
        for target, value in [
            ("hermes_constants.get_hermes_home", self.profile),
            (
                "xnobrain.integrations.managed_providers.extension_policy.read_config",
                {"plugins": {"enabled": ["kiotviet"]}},
            ),
            (
                "xnobrain.integrations.managed_providers.extension_policy.validate_profile_extensions",
                None,
            ),
            (
                "xnobrain.integrations.managed_providers.extension_policy.load_state",
                {"provider_connections": {"kiotviet": {"connection_id": self.connection}}},
            ),
            ("xnobrain.integrations.managed_providers.control_grants.get", "test-grant"),
            ("xnobrain.integrations.managed_providers._profile_scope", "profile-scope"),
        ]:
            p = patch(target, return_value=value)
            self.stack.append(p.start())
            self.addCleanup(p.stop)
        p = patch.dict(
            "os.environ",
            {
                "RUNTIME_CONTROL_URL": "https://control.internal",
                "HERMES_ROOT_PROFILE": str(self.profile.parent / "root"),
            },
        )
        p.start()
        self.addCleanup(p.stop)
        client_type = httpx.Client

        def client_factory(**kwargs):
            return client_type(transport=httpx.MockTransport(self.handle), **kwargs)

        p = patch.object(providers.httpx, "Client", side_effect=client_factory)
        p.start()
        self.addCleanup(p.stop)

    def handle(self, request):
        self.calls.append(request)
        return httpx.Response(self.status, json=self.response)

    def run_tool(self, **extra):
        return json.loads(
            providers.execute("kiotviet", {"resource": "products", **extra}, self.profile)
        )

    def test_owned_read_sends_only_mapping_and_grant(self):
        result = self.run_tool(limit=5)
        self.assertTrue(result["success"])
        self.assertEqual(result["coverage"], "single_page")
        request = self.calls[0]
        self.assertEqual(request.headers["Authorization"], "Bearer test-grant")
        self.assertEqual(
            request.url.path, f"/xnobrain/api/control/internal/v1/providers/{self.connection}/read"
        )
        self.assertEqual(
            json.loads(request.content),
            {"resource": "products", "limit": 5, "provider": "kiotviet", "agent_id": "agent"},
        )

    def test_legacy_root_profile_sends_public_big_brother_id(self):
        with patch.dict("os.environ", {"HERMES_ROOT_PROFILE": str(self.profile)}):
            self.assertTrue(self.run_tool()["success"])
        self.assertEqual(json.loads(self.calls[0].content)["agent_id"], "big-brother")

    def test_named_profile_does_not_inherit_root_identity_from_active_home(self):
        with patch.dict("os.environ", {"HERMES_HOME": str(self.profile)}):
            self.assertTrue(self.run_tool()["success"])
        self.assertEqual(json.loads(self.calls[0].content)["agent_id"], "agent")

    def test_same_basename_elsewhere_is_not_big_brother(self):
        with patch.dict(
            "os.environ",
            {"HERMES_ROOT_PROFILE": str(self.profile.parent / "other" / "agent")},
        ):
            self.assertTrue(self.run_tool()["success"])
        self.assertEqual(json.loads(self.calls[0].content)["agent_id"], "agent")

    def test_wrong_profile_disabled_unapproved_and_disconnected_do_not_call(self):
        self.stack[0].return_value = self.profile.parent / "other"
        self.assertFalse(self.run_tool()["success"])
        self.stack[0].return_value = self.profile
        self.stack[1].return_value = {"plugins": {"enabled": [], "disabled": ["kiotviet"]}}
        self.assertFalse(self.run_tool()["success"])
        self.stack[1].return_value = {"plugins": {"enabled": ["kiotviet"]}}
        self.stack[2].side_effect = RuntimeError("unapproved")
        self.assertFalse(self.run_tool()["success"])
        self.stack[2].side_effect = None
        self.stack[3].return_value = {}
        self.assertFalse(self.run_tool()["success"])
        self.assertEqual(self.calls, [])

    def test_arguments_cannot_override_authority_or_urls(self):
        for extra in [
            {"agent_id": "other"},
            {"connection_id": str(uuid4())},
            {"url": "https://evil.test"},
            {"resource": "delete"},
            {"access_token": "bad"},
        ]:
            self.assertFalse(self.run_tool(**extra)["success"])
        self.stack[4].return_value = ""
        self.assertFalse(self.run_tool()["success"])
        self.assertEqual(self.calls, [])

    def test_errors_do_not_echo_upstream_content(self):
        self.status = 403
        self.response = {
            "success": False,
            "data": {"code": "provider_forbidden"},
            "message": "secret-echo",
        }
        result = self.run_tool()
        self.assertEqual(result["error"]["code"], "provider_forbidden")
        self.assertNotIn("secret-echo", json.dumps(result))
        self.response = {"success": False, "data": {"code": "secret-echo"}}
        self.assertEqual(self.run_tool()["error"]["code"], "provider_unavailable")

    def test_registered_read_and_write_modes_and_operation_arguments(self):
        from unittest.mock import Mock

        ctx = Mock()
        providers.register(ctx, "kiotviet")
        entries = {call.kwargs["name"]: call.kwargs for call in ctx.register_tool.call_args_list}
        self.assertEqual(set(entries), {"kiotviet_read", "kiotviet_write"})
        read = entries["kiotviet_read"]
        write = entries["kiotviet_write"]
        self.assertEqual(len(read["schema"]["parameters"]["properties"]["operation"]["enum"]), 48)
        self.assertEqual(len(write["schema"]["parameters"]["properties"]["operation"]["enum"]), 38)
        for name, entry in entries.items():
            args = (
                {"operation": "products.list", "query": {"name": "Tea"}}
                if name.endswith("read")
                else {"operation": "products.create", "body": {"name": "Tea"}}
            )
            result = json.loads(entry["handler"](args))
            self.assertTrue(result["success"])
            request = self.calls[-1]
            self.assertTrue(request.url.path.endswith("/execute"))
            self.assertEqual(json.loads(request.content)["mode"], name.split("_")[-1])
        count = len(self.calls)
        for args in (
            {"operation": "products.create"},
            {"operation": "products.list", "resource": "products"},
            {"operation": "products.list", "headers": {}},
            {"operation": "products.list", "agent_id": "other"},
        ):
            self.assertFalse(json.loads(read["handler"](args))["success"])
        self.assertFalse(json.loads(write["handler"]({"operation": "products.list"}))["success"])
        self.assertEqual(len(self.calls), count)

    def test_write_inherits_profile_approval_and_connection_guards(self):
        args = {"operation": "products.create", "body": {"name": "Tea"}}
        self.stack[0].return_value = self.profile.parent / "other"
        self.assertFalse(
            json.loads(providers.execute("kiotviet", args, self.profile, mode="write"))["success"]
        )
        self.stack[0].return_value = self.profile
        self.stack[2].side_effect = RuntimeError("unapproved")
        self.assertFalse(
            json.loads(providers.execute("kiotviet", args, self.profile, mode="write"))["success"]
        )
        self.assertEqual(self.calls, [])

    def test_ambiguous_write_response_is_not_replayed(self):
        self.status = 502
        self.response = {"success": False, "data": {"code": "provider_write_outcome_unknown"}}
        result = json.loads(
            providers.execute(
                "kiotviet",
                {"operation": "products.create", "body": {"name": "Tea"}},
                self.profile,
                mode="write",
            )
        )
        self.assertEqual(result["error"]["code"], "provider_write_outcome_unknown")
        self.assertEqual(len(self.calls), 1)

    def test_old_approved_package_does_not_gain_writes_without_update(self):
        from unittest.mock import Mock

        (self.profile / "plugins" / "kiotviet" / "plugin.yaml").write_text(
            "provides_tools: [kiotviet_read]\n"
        )
        ctx = Mock()
        providers.register(ctx, "kiotviet")
        self.assertEqual(
            [call.kwargs["name"] for call in ctx.register_tool.call_args_list], ["kiotviet_read"]
        )
        result = json.loads(
            providers.execute(
                "kiotviet",
                {"operation": "products.create", "body": {"name": "Tea"}},
                self.profile,
                mode="write",
            )
        )
        self.assertEqual(result["error"]["code"], "provider_plugin_update_required")
        self.assertEqual(self.calls, [])
