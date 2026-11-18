"""Discover/install the real optional providers through native plugin lifecycle."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xnobrain.integrations import bundled_plugins, extension_engine, extension_policy
from xnobrain.integrations.hermes_support import AgentAPIError
from xnobrain.repositories import FileRepository
from xnobrain.services.agents import AgentsServiceMixin
from xnobrain.services.base import ServiceError
from xnobrain.services.extensions import ExtensionsService
from xnobrain.tests.test_extension_operations import FakeAgents
from xnobrain.trusted_context import TrustedRequestContext


class BundledProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        data = Path(self.temp.name) / "data"
        self.profile = data / "profiles" / "test"
        self.profile.mkdir(parents=True)
        self.repository = FileRepository(data, data / "profiles")
        self.service = ExtensionsService(self.repository, FakeAgents(self.profile))
        self.trusted = TrustedRequestContext(subject="test", tenant_id="test")

    def operation(self, kind, plugin_id, **extra):
        queued = self.service.create_operation(
            "test",
            dict(kind=kind, plugin_id=plugin_id, idempotency_key=f"{kind}-{plugin_id}", **extra),
            self.trusted,
        )
        operation = self.service.store.claim_next()
        with patch.object(extension_engine, "clone_plugin", side_effect=AssertionError("Network")):
            self.service.worker._process(operation)
        return self.service.get_operation("test", queued["operation_id"], self.trusted)

    def test_discover_native_providers_without_mcp_or_default_skills(self):
        with patch.object(extension_engine, "mcp_profile_isolation_supported", return_value=False):
            capabilities = self.service.capabilities()
        for provider in bundled_plugins.PROVIDERS:
            self.assertIn(provider, capabilities["native_packages"])
        self.assertEqual(capabilities["engine_capabilities"], {"mcp_profile_isolation": False})
        self.assertEqual(AgentsServiceMixin._default_skills([]), [])

    def test_real_install_approve_enable_update_remove_keeps_skills_inside_plugin(self):
        for provider in sorted(bundled_plugins.PROVIDERS):
            with self.subTest(provider=provider):
                result = self.operation("install", provider, source=f"bundled:{provider}")
                self.assertEqual(result["status"], "COMPLETED", result)
                target = self.profile / "plugins" / provider
                self.assertTrue((target / f"skills/{provider}/SKILL.md").is_file())
                self.assertTrue((target / f"skills/{provider}/scripts/{provider}.py").is_file())
                self.assertFalse((self.profile / "skills" / provider).exists())
                self.assertFalse((self.profile / ".env").exists())
                row = next(
                    p
                    for p in extension_engine.plugin_inventory(self.profile)
                    if p["id"] == provider
                )
                self.assertFalse(row["enabled"])
                self.assertEqual(row["provides_skills"], [provider])
                with self.assertRaises(ServiceError) as error:
                    self.service.set_plugin_enabled("test", provider, True, self.trusted)
                self.assertEqual(error.exception.code, "plugin_approval_required")
                record = extension_policy.load_state(self.profile)["plugins"][provider]
                self.service.approve_plugin(
                    "test",
                    provider,
                    {
                        "approved": True,
                        "artifact_digest": record["artifact_digest"],
                        "expected_revision": record["revision"],
                    },
                    self.trusted,
                )
                self.service.set_plugin_enabled("test", provider, True, self.trusted)
                self.service.set_plugin_enabled("test", provider, False, self.trusted)
                updated = self.operation("update", provider)
                self.assertEqual(updated["status"], "COMPLETED", updated)
                self.assertFalse(
                    extension_policy.load_state(self.profile)["plugins"][provider]["approval"][
                        "approved"
                    ]
                )
                self.service.set_provider_connection(
                    "test",
                    provider,
                    {"connection_id": "00000000-0000-4000-8000-000000000001"},
                    self.trusted,
                )
                self.assertEqual(
                    extension_policy.load_state(self.profile)["provider_connections"][provider],
                    {"connection_id": "00000000-0000-4000-8000-000000000001"},
                )
                removed = self.operation("remove", provider)
                self.assertEqual(removed["status"], "COMPLETED", removed)
                self.assertFalse(target.exists())
                self.assertNotIn(
                    provider, extension_policy.load_state(self.profile)["provider_connections"]
                )

    def test_rejects_unknown_mismatched_and_path_overrides(self):
        for plugin_id, source, extra in [
            ("unknown", "bundled:unknown", {}),
            ("sapo", "bundled:sepay", {}),
            ("sepay", "bundled:../../secret", {}),
            ("sepay", "bundled:sepay", {"path": "skills"}),
            ("sepay", "bundled:sepay", {"ref": "main"}),
        ]:
            with self.subTest(source=source, extra=extra), self.assertRaises(ServiceError):
                self.service.create_operation(
                    "test",
                    dict(
                        kind="install",
                        plugin_id=plugin_id,
                        source=source,
                        idempotency_key="invalid",
                        **extra,
                    ),
                    self.trusted,
                )

    def test_rejects_untrusted_identity(self):
        with self.assertRaises(ServiceError) as error:
            self.service.create_operation(
                "test",
                dict(
                    kind="install",
                    plugin_id="sepay",
                    source="bundled:sepay",
                    idempotency_key="untrusted",
                ),
                TrustedRequestContext(),
            )
        self.assertEqual(error.exception.status, 401)

    def test_unavailable_and_symlinked_package_fails_closed(self):
        with patch.object(bundled_plugins, "package_root", return_value=Path(self.temp.name)):
            capabilities = self.service.capabilities()
            self.assertFalse(capabilities["native_packages"]["sepay"])
            (Path(self.temp.name) / "sepay").symlink_to(self.profile, target_is_directory=True)
            with self.assertRaises(AgentAPIError):
                bundled_plugins.package_files("sepay")
