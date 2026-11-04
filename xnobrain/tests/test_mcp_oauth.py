"""Service-level tests for catalog MCP OAuth lifecycle."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import yaml
from tools.mcp_dashboard_oauth import DashboardOAuthFlow

from xnobrain.integrations import extension_engine, extension_policy
from xnobrain.repositories import FileRepository
from xnobrain.services.base import ServiceError
from xnobrain.services.extensions import ExtensionsService
from xnobrain.trusted_context import TrustedRequestContext

_PUBLIC_DNS = patch(
    "xnobrain.services.extensions.socket.getaddrinfo",
    return_value=[(2, 1, 6, "", ("1.1.1.1", 443))],
)


class _Agents:
    def __init__(self, profile: Path):
        self.profile = profile

    def profile_path(self, _agent_id: str) -> Path:
        return self.profile


class MCPOAuthTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.profile = root / "profiles" / "research"
        self.profile.mkdir(parents=True)
        self.service = ExtensionsService(
            FileRepository(root, root / "profiles"), _Agents(self.profile)
        )
        self.trusted = TrustedRequestContext(subject="person-1", tenant_id="tenant-1")
        self.body = {"catalog_id": "linear", "recipe_revision": 2, "fields": {}}

    def tearDown(self):
        self.temporary.cleanup()

    def test_start_requires_redirect_uri(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ServiceError) as raised:
                self.service.oauth_start("research", self.body, self.trusted)
        self.assertEqual(
            (raised.exception.status, raised.exception.code), (503, "oauth_unavailable")
        )

    def test_start_returns_flow_id_and_authorization_url(self):
        def fake_probe(flow, _url, _redirect_uri):
            asyncio.run(flow.publish_authorization_url("https://auth.example/x?state=s1"))

        with (
            patch.dict(
                "os.environ", {"RUNTIME_MCP_OAUTH_REDIRECT_URI": "https://runtime.example/callback"}
            ),
            patch.object(self.service, "_oauth_probe", side_effect=fake_probe),
        ):
            result = self.service.oauth_start("research", self.body, self.trusted)
        self.assertEqual(result["authorization_url"], "https://auth.example/x?state=s1")
        self.assertTrue(result["flow_id"])

    def _create_flow(self, *, ttl=600):
        from xnobrain.services import mcp_oauth

        flow = DashboardOAuthFlow(
            flow_id=mcp_oauth.new_flow_id(),
            server_name="xnobrain-linear",
            profile="research",
            hermes_home=str(self.profile),
            redirect_uri="https://runtime.example/callback",
        )
        mcp_oauth.create_flow(flow, "research", self.trusted, "linear", str(self.profile))
        if ttl != 600:
            with mcp_oauth._LOCK:
                mcp_oauth._FLOWS[flow.flow_id].expires_at = ttl
        return flow

    def test_failed_reauth_restores_previous_tokens(self):
        tokens = self.profile / "mcp-tokens"
        tokens.mkdir()
        (tokens / "xnobrain-linear.json").write_text('{"access_token":"old"}', encoding="utf-8")

        class Manager:
            def remove(self, name, *, hermes_home):
                for path in tokens.glob(f"{name}*"):
                    path.unlink()

            def restore_entry(self, *_args, **_kwargs):
                pass

            def get_or_build_provider(self, name, *_args):
                # A denied attempt leaves a fresh DCR client file behind.
                (tokens / f"{name}.client.json").write_text("{}", encoding="utf-8")
                raise RuntimeError("denied")

        flow = DashboardOAuthFlow(
            flow_id="f",
            server_name="xnobrain-linear",
            profile="research",
            hermes_home=str(self.profile),
            redirect_uri="https://runtime.example/callback",
        )
        with patch("tools.mcp_oauth_manager.get_manager", return_value=Manager()):
            with self.assertRaises(RuntimeError):
                self.service._oauth_probe(flow, "https://mcp.linear.app/mcp", "https://r/cb")
        self.assertEqual(
            (tokens / "xnobrain-linear.json").read_text(encoding="utf-8"), '{"access_token":"old"}'
        )
        self.assertFalse((tokens / "xnobrain-linear.client.json").exists())

    def test_callback_from_another_subject_is_not_found(self):
        from xnobrain.services import mcp_oauth

        flow = self._create_flow()
        other = TrustedRequestContext(subject="person-2", tenant_id="tenant-1")
        with self.assertRaises(ServiceError) as raised:
            self.service.oauth_callback(
                "research", {"flow_id": flow.flow_id, "code": "code", "state": "state"}, other
            )
        self.assertEqual(raised.exception.code, "oauth_flow_not_found")
        mcp_oauth.remove_flow(flow.flow_id)

    def test_callback_rejects_wrong_state(self):
        flow = self._create_flow()
        with self.assertRaises(ServiceError) as raised:
            self.service.oauth_callback(
                "research",
                {"flow_id": flow.flow_id, "code": "code", "state": "wrong"},
                self.trusted,
            )
        self.assertEqual(
            (raised.exception.status, raised.exception.code), (400, "oauth_state_mismatch")
        )

    def test_expired_flow_is_not_found(self):
        from xnobrain.services import mcp_oauth

        flow = self._create_flow(ttl=0)
        with self.assertRaises(ServiceError) as raised:
            self.service.oauth_callback(
                "research",
                {"flow_id": flow.flow_id, "code": "code", "state": "state"},
                self.trusted,
            )
        self.assertEqual(raised.exception.code, "oauth_flow_not_found")
        mcp_oauth.remove_flow(flow.flow_id)

    @_PUBLIC_DNS
    def test_callback_success_persists_oauth_server_and_refreshes(self, _dns):
        from xnobrain.services import mcp_oauth

        flow = self._create_flow()
        state = "callback-state"

        def approve(**_kwargs):
            flow.mark_approved()
            flow.mark_worker_done()

        with (
            patch.object(flow, "deliver_callback", side_effect=approve),
            patch.object(extension_engine, "refresh_profile_mcp") as refresh,
        ):
            result = self.service.oauth_callback(
                "research", {"flow_id": flow.flow_id, "code": "code", "state": state}, self.trusted
            )
        self.assertEqual(result, {"status": "connected", "catalog_id": "linear"})
        config = self.service._read_profile_config(self.profile)
        server = config[extension_policy.MANAGED_MCP_KEY]["xnobrain-linear"]
        self.assertEqual(server["auth"], "oauth")
        self.assertEqual(server["xnobrain_oauth_name"], "xnobrain-linear")
        refresh.assert_called_once_with(self.profile)
        self.assertIsNone(mcp_oauth.get_flow(flow.flow_id, "research", self.trusted))

    @_PUBLIC_DNS
    def test_disconnect_removes_hermes_oauth_provider(self, _dns):
        self.service._write_catalog_oauth_server(
            "research", self.profile, self.service._catalog_entry(self.body, self.trusted)
        )
        manager = type("Manager", (), {"remove": Mock()})()
        with (
            patch("tools.mcp_oauth_manager.get_manager", return_value=manager),
            patch.object(extension_engine, "mcp_disconnect"),
        ):
            self.service.disconnect_catalog("research", "linear", self.trusted)
        manager.remove.assert_called_once_with("xnobrain-linear", hermes_home=str(self.profile))

    def test_plugin_mcp_shadow_tracks_installed_disk_plugins(self):
        plugin = self.profile / "plugins" / "portable"
        plugin.mkdir(parents=True)
        (plugin / "plugin.json").write_text('{"name":"portable"}', encoding="utf-8")
        (plugin / "mcp.json").write_text(
            '{"mcpServers":{"docs":{"url":"https://docs.example/mcp"}}}', encoding="utf-8"
        )
        name = extension_engine.portable_plugin_mcp_names(self.profile, "portable")[0]
        config = {"mcp_servers": {name: {"url": "https://user.example/mcp"}}}
        # Existing user-owned entry wins; another plugin name gets a shadow.
        second_name = name.rsplit("__", 1)[0] + "__other"
        (plugin / "mcp.json").write_text(
            '{"mcpServers":{"docs":{"url":"https://docs.example/mcp"},'
            '"other":{"url":"https://other.example/mcp"}}}',
            encoding="utf-8",
        )
        (plugin / "plugin.json").write_text('{"name":"portable"}', encoding="utf-8")
        (self.profile / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        digest = extension_policy.tree_digest(plugin)
        extension_policy.save_state(
            self.profile,
            {
                "plugins": {
                    "portable": {
                        "scan": {"status": "COMPLETED", "verdict": "safe"},
                        "approval": {"approved": True, "artifact_digest": digest},
                    }
                },
                "hooks": {},
                "mcp": {},
                "revision": 1,
            },
        )
        with patch.object(extension_engine, "plugin_set_enabled", return_value={"enabled": True}):
            self.service.set_plugin_enabled("research", "portable", True, self.trusted)
        stored = self.service._read_profile_config(self.profile)["mcp_servers"]
        self.assertEqual(stored[name]["url"], "https://user.example/mcp")
        self.assertTrue(stored[second_name]["xnobrain_plugin_shadow"])
        self.assertEqual(
            set(extension_engine._custom_mcp_servers({"mcp_servers": stored}, {})), {name}
        )
        from xnobrain.integrations.mcp import MCPIntegrationMixin

        class Adapter(MCPIntegrationMixin):
            def _agent_name(self, value):
                return str(value)

            def _require_profile(self, _name):
                return self.profile

            def _read_config(self, _profile):
                return self.config

        adapter = Adapter()
        adapter.profile = self.profile
        adapter.config = {"mcp_servers": stored}
        self.assertNotIn(second_name, adapter.get_mcp("research")["servers"])
        with patch.object(extension_engine, "_write_config") as write_config:
            self.service._sync_plugin_mcp_shadows(self.profile)
        write_config.assert_not_called()
        with patch.object(extension_engine, "plugin_set_enabled", return_value={"enabled": False}):
            self.service.set_plugin_enabled("research", "portable", False, self.trusted)
        self.assertTrue(
            self.service._read_profile_config(self.profile)["mcp_servers"][second_name][
                "xnobrain_plugin_shadow"
            ]
        )
        import shutil

        shutil.rmtree(plugin)
        self.service._sync_plugin_mcp_shadows(self.profile)
        self.assertEqual(
            set(self.service._read_profile_config(self.profile)["mcp_servers"]), {name}
        )

    def test_mcp_edit_of_plugin_shadow_is_rejected(self):
        config_path = self.profile / "config.yaml"
        config_path.write_text(
            "mcp_servers:\n  plugin__docs:\n    enabled: false\n    xnobrain_plugin_shadow: true\n",
            encoding="utf-8",
        )
        from xnobrain.services.mcp import MCPService

        mcp = MCPService(
            self.service.repository,
            _MCPAgentAdapter(self.profile),
            lambda _agent_id: self.profile,
            lambda *_args: {},
        )
        with self.assertRaises(ServiceError) as raised:
            mcp.update("research", {"servers": {}})
        self.assertEqual(
            (raised.exception.status, raised.exception.code), (422, "mcp_server_managed")
        )


class _MCPAgentAdapter:
    def __init__(self, profile: Path):
        self.profile = profile

    def get_mcp(self, _agent_id):
        return {"servers": {}}

    def update_mcp(self, _agent_id, servers):
        return {"servers": servers}


if __name__ == "__main__":
    unittest.main()
