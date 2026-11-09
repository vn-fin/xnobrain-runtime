"""Focused policy and persistence tests for per-profile extensions."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xnobrain.integrations import extension_engine, extension_policy
from xnobrain.repositories import FileRepository
from xnobrain.services.base import ServiceError
from xnobrain.services.extensions import ExtensionsService
from xnobrain.trusted_context import TrustedRequestContext


class FakeAgents:
    def __init__(self, profile: Path):
        self.profile = profile

    def profile_path(self, _agent_id: str) -> Path:
        return self.profile


class ExtensionServiceTests(unittest.TestCase):
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

    def test_catalog_fails_closed_on_unscoped_mcp_registry(self):
        with patch.object(extension_engine, "mcp_profile_isolation_supported", return_value=False):
            catalog = self.service.catalog()

        self.assertEqual(catalog["engine_capabilities"], {"mcp_profile_isolation": False})
        candidates = [
            entry for entry in catalog["entries"] if entry["installability"] == "installable"
        ]
        self.assertTrue(candidates)
        self.assertTrue(
            all(
                entry["capability"]
                == {
                    "installable": False,
                    "reason_code": "mcp_profile_isolation_unavailable",
                }
                for entry in candidates
            )
        )

    def test_catalog_install_rejects_before_writing_config(self):
        with patch.object(extension_engine, "mcp_profile_isolation_supported", return_value=False):
            with self.assertRaises(ServiceError) as context:
                self.service.install_catalog(
                    "research",
                    {"catalog_id": "context7", "recipe_revision": 1, "fields": {}},
                    self.trusted,
                )

        self.assertEqual(context.exception.status, 503)
        self.assertEqual(context.exception.code, "plugins_unavailable")
        self.assertFalse((self.profile / "config.yaml").exists())
        self.assertFalse((self.profile / "mcp.json").exists())

    def test_hook_creation_is_pending_and_does_not_register_shell_hook(self):
        contract = {"valid_events": ["pre_tool_call"], "unsupported": []}
        with patch.object(extension_engine, "hook_events", return_value=contract):
            result = self.service.create_hook(
                "research",
                {
                    "event": "pre_tool_call",
                    "command": "printf safe",
                    "matcher": "^read_.*$",
                    "timeout": 30,
                },
                self.trusted,
            )

        self.assertFalse(result["enabled"])
        self.assertTrue(result["approval_required"])
        state = extension_policy.load_state(self.profile)
        self.assertFalse(state["hooks"][result["id"]]["approval"]["approved"])
        self.assertFalse((self.profile / "config.yaml").exists())

    def test_legacy_config_hook_is_listed_after_migration(self):
        import yaml

        command = "printf legacy"
        (self.profile / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "hooks": {
                        "pre_tool_call": [
                            {"command": command, "matcher": "terminal", "timeout": 15}
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        result = self.service.list_hooks("research", self.trusted)
        self.assertEqual(len(result["hooks"]), 1)
        hook = result["hooks"][0]
        self.assertFalse(hook["enabled"])
        self.assertFalse(hook["approval"]["approved"])
        self.assertEqual(hook["event"], "pre_tool_call")
        self.assertEqual(hook["matcher"], "terminal")
        self.assertEqual(hook["timeout"], 15)
        config = yaml.safe_load((self.profile / "config.yaml").read_text())
        self.assertNotIn("hooks", config)

    def test_hooks_sharing_a_command_do_not_revoke_each_other(self):
        import yaml
        from agent import shell_hooks

        contract = {"valid_events": ["pre_tool_call"], "unsupported": []}
        command = "printf shared-hook"
        hooks = []
        with patch.object(extension_engine, "hook_events", return_value=contract):
            for matcher in ("^read_.*$", "^write_.*$"):
                hook = self.service.create_hook(
                    "research",
                    {
                        "event": "pre_tool_call",
                        "command": command,
                        "matcher": matcher,
                        "timeout": 30,
                    },
                    self.trusted,
                )
                self.service.approve_hook(
                    "research",
                    hook["id"],
                    {
                        "command_digest": hook["command_digest"],
                        "approved": True,
                        "expected_revision": hook["revision"],
                    },
                    self.trusted,
                )
                self.service.set_hook_enabled("research", hook["id"], True, self.trusted)
                hooks.append(hook)

        config = yaml.safe_load((self.profile / "config.yaml").read_text())
        self.assertEqual(
            [entry["matcher"] for entry in config["hooks"]["pre_tool_call"]],
            ["^read_.*$", "^write_.*$"],
        )
        with extension_engine.profile_context(self.profile):
            self.assertIsNotNone(shell_hooks.allowlist_entry_for("pre_tool_call", command))

        self.service.set_hook_enabled("research", hooks[0]["id"], False, self.trusted)
        config = yaml.safe_load((self.profile / "config.yaml").read_text())
        self.assertEqual(
            [entry["matcher"] for entry in config["hooks"]["pre_tool_call"]],
            ["^write_.*$"],
        )
        with extension_engine.profile_context(self.profile):
            self.assertIsNotNone(shell_hooks.allowlist_entry_for("pre_tool_call", command))

        self.service.set_hook_enabled("research", hooks[1]["id"], False, self.trusted)
        config = yaml.safe_load((self.profile / "config.yaml").read_text())
        self.assertNotIn("hooks", config)
        with extension_engine.profile_context(self.profile):
            self.assertIsNone(shell_hooks.allowlist_entry_for("pre_tool_call", command))

    def test_hook_event_and_timeout_are_validated(self):
        contract = {
            "valid_events": ["pre_tool_call"],
            "unsupported": ["transform_api_error_classification"],
        }
        with patch.object(extension_engine, "hook_events", return_value=contract):
            with self.assertRaises(ServiceError) as event_error:
                self.service.create_hook(
                    "research",
                    {"event": "transform_api_error_classification", "command": "true"},
                    self.trusted,
                )
            self.assertEqual(event_error.exception.code, "invalid_hook_event")
            with self.assertRaises(ServiceError) as timeout_error:
                self.service.create_hook(
                    "research",
                    {"event": "pre_tool_call", "command": "true", "timeout": 301},
                    self.trusted,
                )
            self.assertEqual(timeout_error.exception.code, "invalid_hook_timeout")

    def test_composio_endpoint_host_policy(self):
        public_addresses = [(None, None, None, "", ("93.184.216.34", 443))]
        with patch(
            "xnobrain.services.extensions.socket.getaddrinfo",
            return_value=public_addresses,
        ):
            for accepted in (
                "https://connect.composio.dev/mcp",
                "https://backend.composio.dev/v3/mcp/abc",
            ):
                ExtensionsService._validate_catalog_endpoint(
                    accepted, "https_only_public_composio_host"
                )

        for rejected in (
            "https://evilcomposio.dev/mcp",
            "https://composio.dev.evil.com/mcp",
            "https://composio.com/mcp",
            "http://connect.composio.dev/mcp",
        ):
            with self.assertRaises(ServiceError) as error:
                ExtensionsService._validate_catalog_endpoint(
                    rejected, "https_only_public_composio_host"
                )
            self.assertEqual(error.exception.code, "unsafe_endpoint")

    def test_visibility_uses_revision_and_persists(self):
        plugin = self.profile / "plugins" / "readme-helper"
        plugin.mkdir(parents=True)
        (plugin / "plugin.yaml").write_text("key: readme-helper\n", encoding="utf-8")
        extension_policy.save_state(
            self.profile,
            {
                "plugins": {"readme-helper": {"revision": 3}},
                "hooks": {},
                "mcp": {},
                "revision": 1,
            },
        )

        result = self.service.set_plugin_visibility(
            "research",
            "readme-helper",
            {"hidden": True, "expected_revision": 3},
            self.trusted,
        )

        self.assertTrue(result["hidden"])
        self.assertEqual(result["revision"], 4)
        self.assertTrue(
            extension_policy.load_state(self.profile)["plugins"]["readme-helper"]["hidden"]
        )
        with self.assertRaises(ServiceError) as error:
            self.service.set_plugin_visibility(
                "research",
                "readme-helper",
                {"hidden": False, "expected_revision": 3},
                self.trusted,
            )
        self.assertEqual(error.exception.code, "extension_revision_conflict")

    def test_plugin_mcp_rows_expose_auth_header_for_api_key_servers(self):
        rows = ExtensionsService._plugin_mcp_rows(
            self.profile,
            "sample",
            [
                {
                    "name": "docs",
                    "host": "mcp.example.com",
                    "auth_kind": "api_key",
                    "auth_header": "Authorization",
                    "upstream_url": "https://mcp.example.com/mcp",
                }
            ],
            False,
            {"commit": "a" * 40},
        )
        self.assertEqual(rows[0]["auth_header"], "Authorization")
        self.assertEqual(rows[0]["chat_status"], "disabled")

    def test_profile_owner_must_match(self):
        (self.profile / ".community-profile-owner.json").write_text(
            json.dumps({"subject": "other", "tenant_id": "tenant-1", "organization_id": ""}),
            encoding="utf-8",
        )

        with self.assertRaises(ServiceError) as error:
            self.service.list_plugins("research", self.trusted)
        self.assertEqual(error.exception.code, "permission_denied")

    def test_required_plugin_is_protected_only_when_packaged_digest_matches(self):
        plugin_id = "xnobrain-runtime-updates"
        source = extension_policy.required_plugin_root() / plugin_id
        destination = self.profile / "plugins" / plugin_id
        destination.parent.mkdir(parents=True)
        shutil.copytree(source, destination)

        with self.assertRaises(ServiceError) as trusted_error:
            self.service.set_plugin_enabled("research", plugin_id, False, self.trusted)
        self.assertEqual(trusted_error.exception.code, "required_plugin")

        (destination / "__init__.py").write_text("# modified\n", encoding="utf-8")
        with self.assertRaises(ServiceError) as tampered_error:
            self.service.set_plugin_enabled("research", plugin_id, False, self.trusted)
        self.assertEqual(tampered_error.exception.code, "required_plugin_invalid")
