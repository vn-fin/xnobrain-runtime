import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from xnobrain.app import XNOBrainApplication
from xnobrain.integrations import AgentManager, GlobalConfigManager
from xnobrain.models.marketplace import MarketplaceExportPackage
from xnobrain.repositories.files import FileRepository
from xnobrain.services.base import ServiceError
from xnobrain.services.marketplace import (
    MAX_EXPORT_FILE_BYTES,
    MarketplaceService,
)


class MarketplaceRequestValidationTests(unittest.TestCase):
    def test_unknown_authority_fields_are_rejected(self):
        from pydantic import ValidationError

        from xnobrain.models.marketplace import MarketplaceInstallRequest, MarketplaceUpdateRequest

        for model, body in (
            (MarketplaceInstallRequest, {"package": {}}),
            (MarketplaceUpdateRequest, {"package": {}, "local_profile_id": "market-synthetic"}),
        ):
            with self.assertRaises(ValidationError):
                model.model_validate({**body, "approved_by": "synthetic"})

    def test_profile_path_input_is_rejected(self):
        from pydantic import ValidationError

        from xnobrain.models.marketplace import MarketplaceUninstallRequest

        for value in ("", "../outside", "/absolute", "a/b"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                MarketplaceUninstallRequest(local_profile_id=value)


class Agents:
    def sync_profiles_registry(self):
        pass


class MarketplaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = FileRepository(Path(self.tmp.name) / "data", Path(self.tmp.name) / "profiles")
        self.s = MarketplaceService(self.repo, Agents())

    def tearDown(self):
        self.tmp.cleanup()

    def package(self):
        p = {
            "id": "inst_123456789",
            "status": "pending",
            "update_policy": "pinned",
            "definition": {
                "soul": "You are safe.",
                "public_config": {"display_name": "Safe", "api_key": "forbidden"},
                "skills": {"writing": "# Writing"},
                "assets": {"guide.txt": "Guide"},
            },
            "requested_permissions": [],
            "compatibility": {},
            "license": "MIT",
        }
        p["digest"] = self.s.digest(p)
        return p

    def test_failed_install_publish_cleans_stage_and_allows_retry(self):
        package = self.package()
        before = set(self.repo.profiles_root.iterdir())
        original_replace = os.replace

        def fail_profile_publish(source, destination):
            if Path(source).is_dir():
                raise OSError("synthetic profile publish failure")
            return original_replace(source, destination)

        with patch("xnobrain.services.marketplace.os.replace", side_effect=fail_profile_publish):
            with self.assertRaises(OSError):
                self.s.install(package)
        self.assertEqual(set(self.repo.profiles_root.iterdir()), before)
        result = self.s.install(package)
        self.assertTrue(self.repo.profile_path(result["local_profile_id"]).is_dir())

    def test_install_validates_colliding_skill_and_asset_independently(self):
        for invalid in (None, {"unexpected": "object"}, "x" * 1_000_001):
            with self.subTest(value_type=type(invalid).__name__):
                package = self.package()
                package["definition"]["skills"] = {"same": invalid}
                package["definition"]["assets"] = {"same": "Valid asset"}
                package["digest"] = self.s.digest(package)
                before = set(self.repo.profiles_root.iterdir())
                with self.assertRaises(ServiceError) as error:
                    self.s.install(package)
                self.assertEqual(error.exception.code, "package_rejected")
                self.assertEqual(set(self.repo.profiles_root.iterdir()), before)

    def test_install_rejects_non_mapping_package_fields(self):
        for field in ("definition", "skills", "assets", "public_config"):
            for value in ([], [["key", "value"]], "text", 42):
                with self.subTest(field=field, value_type=type(value).__name__):
                    package = self.package()
                    if field == "definition":
                        package[field] = value
                    else:
                        package["definition"][field] = value
                    package["digest"] = self.s.digest(package)
                    with self.assertRaises(ServiceError) as error:
                        self.s.install(package)
                    self.assertEqual(error.exception.code, "package_rejected")

    def test_install_rejects_invalid_installation_ids(self):
        for value in (None, "", "../foreign", "inst_../foreign", 123):
            with self.subTest(value=value):
                package = self.package()
                package["id"] = value
                with self.assertRaises(ServiceError) as error:
                    self.s.install(package)
                self.assertEqual(error.exception.code, "package_rejected")

    def test_distinct_long_installation_ids_do_not_collide(self):
        package = self.package()
        package["id"] = "inst_" + "a" * 24 + "first"
        first = self.s.install(package)
        package["id"] = "inst_" + "a" * 24 + "second"
        second = self.s.install(package)
        self.assertNotEqual(first["local_profile_id"], second["local_profile_id"])
        self.assertTrue(self.repo.profile_path(first["local_profile_id"]).is_dir())
        self.assertTrue(self.repo.profile_path(second["local_profile_id"]).is_dir())

    def test_legacy_truncated_installation_is_not_duplicated(self):
        package = self.package()
        package["id"] = "inst_" + "b" * 32
        installed = self.s.install(package)
        original = self.repo.profile_path(installed["local_profile_id"])
        legacy = self.repo.profile_path("market-" + "b" * 24)
        original.rename(legacy)
        with self.assertRaises(ServiceError) as error:
            self.s.install(package)
        self.assertEqual(error.exception.code, "installation_exists")
        self.assertTrue(legacy.is_dir())
        self.assertFalse(original.exists())

    def test_install_rejects_aggregate_file_count_before_staging(self):
        package = self.package()
        package["definition"]["assets"] = {f"asset-{index}": "x" for index in range(203)}
        package["digest"] = self.s.digest(package)
        before = set(self.repo.profiles_root.iterdir())
        with self.assertRaises(ServiceError) as error:
            self.s.install(package)
        self.assertEqual(error.exception.status, 413)
        self.assertEqual(set(self.repo.profiles_root.iterdir()), before)

    def test_install_aggregate_limit_counts_utf8_bytes(self):
        package = self.package()
        package["definition"]["assets"] = {"unicode.txt": "ệ" * 100}
        package["digest"] = self.s.digest(package)
        before = set(self.repo.profiles_root.iterdir())
        with patch("xnobrain.services.marketplace.MAX_EXPORT_TOTAL_BYTES", 200):
            with self.assertRaises(ServiceError) as error:
                self.s.install(package)
        self.assertEqual(error.exception.status, 413)
        self.assertEqual(set(self.repo.profiles_root.iterdir()), before)

    def test_install_file_limit_counts_utf8_bytes(self):
        package = self.package()
        package["definition"]["assets"] = {"unicode.txt": "ệ" * 100}
        package["digest"] = self.s.digest(package)
        with patch("xnobrain.services.marketplace.MAX_EXPORT_FILE_BYTES", 200):
            with self.assertRaises(ServiceError) as error:
                self.s.install(package)
        self.assertEqual(error.exception.code, "package_rejected")

    def test_soul_limit_counts_bytes_for_install_and_update(self):
        package = self.package()
        installed = self.s.install(package)
        package["definition"]["soul"] = "ệ" * 40_000
        package["digest"] = self.s.digest(package)
        with self.assertRaises(ServiceError) as install_error:
            self.s.install(package)
        self.assertEqual(install_error.exception.code, "package_rejected")
        with self.assertRaises(ServiceError) as update_error:
            self.s.update({**package, "status": "updating"}, installed["local_profile_id"])
        self.assertEqual(update_error.exception.code, "package_rejected")
        self.assertEqual(
            (self.repo.profile_path(installed["local_profile_id"]) / "SOUL.md").read_text(),
            "You are safe.",
        )

    def test_legacy_install_detection_rejects_symlinked_config(self):
        package = self.package()
        package["id"] = "inst_" + "c" * 32
        legacy = self.repo.profile_path("market-" + "c" * 24)
        legacy.mkdir()
        external = Path(self.tmp.name) / "outside.yaml"
        external.write_text("synthetic: private\n")
        (legacy / "config.yaml").symlink_to(external)
        with self.assertRaises(ServiceError) as error:
            self.s.install(package)
        self.assertEqual(error.exception.code, "installation_profile_conflict")
        self.assertEqual(external.read_text(), "synthetic: private\n")
        self.assertFalse(self.repo.profile_path("market-" + "c" * 32).exists())

    def test_install_rejects_recognizable_credentials_in_assets(self):
        package = self.package()
        package["definition"]["assets"]["credential.txt"] = "sk-" + "x" * 24
        package["digest"] = self.s.digest(package)
        with self.assertRaises(ServiceError) as error:
            self.s.install(package)
        self.assertEqual(error.exception.code, "package_rejected")
        self.assertNotIn("x" * 24, str(error.exception))

    def test_install_rejects_category_limits_below_total_limit(self):
        for field in ("skills", "assets"):
            with self.subTest(field=field):
                package = self.package()
                package["definition"][field] = {f"item-{index}": "Synthetic" for index in range(101)}
                package["digest"] = self.s.digest(package)
                before = set(self.repo.profiles_root.iterdir())
                with self.assertRaises(ServiceError) as error:
                    self.s.install(package)
                self.assertEqual(error.exception.status, 413)
                self.assertEqual(set(self.repo.profiles_root.iterdir()), before)

    def test_install_creates_isolated_empty_customer_state(self):
        out = self.s.install(self.package())
        profile = self.repo.profile_path(out["local_profile_id"])
        self.assertTrue((profile / "SOUL.md").is_file())
        self.assertFalse((profile / "memories" / "MEMORY.md").exists())
        self.assertNotIn("api_key", (profile / "config.yaml").read_text())

    def test_digest_mismatch_fails_before_profile_publish(self):
        p = self.package()
        p["digest"] = "sha256:" + "0" * 64
        with self.assertRaises(ServiceError):
            self.s.install(p)
        self.assertEqual(list(self.repo.profiles_root.iterdir()), [])

    def test_path_like_skill_name_rejected(self):
        p = self.package()
        p["definition"]["skills"] = {"../escape": "x"}
        p["digest"] = self.s.digest(p)
        with self.assertRaises(ServiceError):
            self.s.install(p)

    def test_update_rejects_another_installation_before_writes(self):
        package = self.package()
        installed = self.s.install(package)
        profile = self.repo.profile_path(installed["local_profile_id"])
        before = {path: path.read_bytes() for path in profile.rglob("*") if path.is_file()}
        package["id"] = "inst_foreign"
        package["definition"]["soul"] = "Must not overwrite"
        package["digest"] = self.s.digest(package)
        with self.assertRaises(ServiceError) as error:
            self.s.update({**package, "status": "updating"}, installed["local_profile_id"])
        self.assertEqual(error.exception.code, "installation_profile_conflict")
        self.assertEqual(
            before, {path: path.read_bytes() for path in profile.rglob("*") if path.is_file()}
        )

    def test_update_config_failure_restores_definition_files(self):
        package = self.package()
        installed = self.s.install(package)
        profile = self.repo.profile_path(installed["local_profile_id"])
        before = {path: path.read_bytes() for path in profile.rglob("*") if path.is_file()}
        package["definition"]["soul"] = "Synthetic updated soul"
        package["digest"] = self.s.digest(package)
        with patch.object(self.repo, "atomic_yaml", side_effect=OSError("synthetic")):
            with self.assertRaises(OSError):
                self.s.update({**package, "status": "updating"}, installed["local_profile_id"])
        self.assertEqual(
            before, {path: path.read_bytes() for path in profile.rglob("*") if path.is_file()}
        )

    def test_update_rejects_empty_definition_before_writes(self):
        package = self.package()
        installed = self.s.install(package)
        profile = self.repo.profile_path(installed["local_profile_id"])
        before = (profile / "SOUL.md").read_bytes()
        package["definition"]["soul"] = " "
        package["digest"] = self.s.digest(package)
        with self.assertRaises(ServiceError) as error:
            self.s.update({**package, "status": "updating"}, installed["local_profile_id"])
        self.assertEqual(error.exception.code, "package_rejected")
        self.assertEqual((profile / "SOUL.md").read_bytes(), before)

    def test_update_rejects_malformed_config_without_mutation(self):
        package = self.package()
        installed = self.s.install(package)
        profile = self.repo.profile_path(installed["local_profile_id"])
        before = {path: path.read_bytes() for path in profile.rglob("*") if path.is_file()}
        package["definition"]["public_config"] = [["model", "synthetic"]]
        package["digest"] = self.s.digest(package)
        with self.assertRaises(ServiceError) as error:
            self.s.update({**package, "status": "updating"}, installed["local_profile_id"])
        self.assertEqual(error.exception.code, "package_rejected")
        self.assertEqual(
            before, {path: path.read_bytes() for path in profile.rglob("*") if path.is_file()}
        )

    def test_update_rejects_symlinked_definition_files(self):
        for name in ("SOUL.md", "config.yaml"):
            with self.subTest(name=name):
                package = self.package()
                package["id"] = "inst_" + name.replace(".", "_")
                installed = self.s.install(package)
                profile = self.repo.profile_path(installed["local_profile_id"])
                outside = Path(self.tmp.name) / ("outside-" + name)
                outside.write_text("Synthetic external data")
                target = profile / name
                target.unlink()
                target.symlink_to(outside)
                with self.assertRaises(ServiceError) as error:
                    self.s.update({**package, "status": "updating"}, installed["local_profile_id"])
                self.assertEqual(error.exception.code, "installation_profile_conflict")
                self.assertTrue(target.is_symlink())
                self.assertEqual(outside.read_text(), "Synthetic external data")

    def test_update_rejects_invalid_persisted_config(self):
        package = self.package()
        installed = self.s.install(package)
        profile = self.repo.profile_path(installed["local_profile_id"])
        config = profile / "config.yaml"
        config.write_text("- synthetic-list-entry\n")
        original_soul = (profile / "SOUL.md").read_bytes()
        with self.assertRaises(ServiceError) as error:
            self.s.update({**package, "status": "updating"}, installed["local_profile_id"])
        self.assertEqual(error.exception.code, "installation_profile_conflict")
        self.assertEqual((profile / "SOUL.md").read_bytes(), original_soul)
        self.assertEqual(config.read_text(), "- synthetic-list-entry\n")

    def test_update_corrupt_yaml_returns_safe_conflict(self):
        package = self.package()
        installed = self.s.install(package)
        profile = self.repo.profile_path(installed["local_profile_id"])
        config = profile / "config.yaml"
        content = "synthetic_private_marker: [\n"
        config.write_text(content)
        with self.assertRaises(ServiceError) as error:
            self.s.update({**package, "status": "updating"}, installed["local_profile_id"])
        self.assertEqual(error.exception.code, "installation_profile_conflict")
        self.assertNotIn("synthetic_private_marker", str(error.exception))
        self.assertEqual(config.read_text(), content)

    def test_update_requires_updating_lifecycle(self):
        package = self.package()
        installed = self.s.install(package)
        profile = self.repo.profile_path(installed["local_profile_id"])
        before = {path: path.read_bytes() for path in profile.rglob("*") if path.is_file()}
        for status in (None, "pending", "installed", "revoked", "uninstalled"):
            with self.subTest(status=status), self.assertRaises(ServiceError) as error:
                self.s.update({**package, "status": status}, installed["local_profile_id"])
            self.assertEqual(error.exception.code, "installation_unavailable")
        self.assertEqual(
            before, {path: path.read_bytes() for path in profile.rglob("*") if path.is_file()}
        )

    def test_update_reports_failed_rollback_without_content(self):
        package = self.package()
        installed = self.s.install(package)
        original_write = self.repo.atomic_write
        calls = 0

        def fail_recovery(path, payload, **kwargs):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise OSError("synthetic private failure detail")
            return original_write(path, payload, **kwargs)

        package["definition"]["soul"] = "Synthetic replacement"
        package["digest"] = self.s.digest(package)
        with patch.object(self.repo, "atomic_write", side_effect=fail_recovery):
            with patch.object(self.repo, "atomic_yaml", side_effect=OSError("synthetic")):
                with self.assertRaises(ServiceError) as error:
                    self.s.update({**package, "status": "updating"}, installed["local_profile_id"])
        self.assertEqual(error.exception.code, "marketplace_update_recovery_required")
        self.assertNotIn("private failure", str(error.exception))

    def test_update_digest_mismatch_never_reaches_writes(self):
        package = self.package()
        installed = self.s.install(package)
        package["definition"]["soul"] = "Tampered synthetic content"
        with patch.object(self.repo, "atomic_write") as write:
            with patch.object(self.repo, "atomic_yaml") as write_yaml:
                with self.assertRaises(ServiceError) as error:
                    self.s.update({**package, "status": "updating"}, installed["local_profile_id"])
                write.assert_not_called()
                write_yaml.assert_not_called()
        self.assertEqual(error.exception.code, "package_digest_mismatch")
        self.assertEqual(
            (self.repo.profile_path(installed["local_profile_id"]) / "SOUL.md").read_text(),
            "You are safe.",
        )

    def test_update_credential_text_rejected_before_writes(self):
        package = self.package()
        installed = self.s.install(package)
        package["definition"]["soul"] = "sk-" + "x" * 24
        package["digest"] = self.s.digest(package)
        with patch.object(self.repo, "atomic_write") as write:
            with patch.object(self.repo, "atomic_yaml") as write_yaml:
                with self.assertRaises(ServiceError) as error:
                    self.s.update({**package, "status": "updating"}, installed["local_profile_id"])
                write.assert_not_called()
                write_yaml.assert_not_called()
        self.assertEqual(error.exception.code, "package_rejected")
        self.assertNotIn("x" * 24, str(error.exception))

    def test_update_rejects_invalid_id_even_when_local_marker_matches(self):
        package = self.package()
        installed = self.s.install(package)
        profile = self.repo.profile_path(installed["local_profile_id"])
        config = yaml.safe_load((profile / "config.yaml").read_text())
        config["xnobrain"]["marketplace_installation_id"] = True
        self.repo.atomic_yaml(profile / "config.yaml", config)
        package["id"] = True
        with self.assertRaises(ServiceError) as error:
            self.s.update({**package, "status": "updating"}, installed["local_profile_id"])
        self.assertEqual(error.exception.code, "package_rejected")

    def test_update_preserves_local_configuration_outside_publisher_fields(self):
        package = self.package()
        installed = self.s.install(package)
        profile = self.repo.profile_path(installed["local_profile_id"])
        config_path = profile / "config.yaml"
        config = yaml.safe_load(config_path.read_text())
        config["approvals"] = {"mode": "manual"}
        config["terminal"] = {"timeout": 42}
        self.repo.atomic_yaml(config_path, config)
        package["definition"]["public_config"] = {"display_name": "Updated", "approvals": {"mode": "off"}}
        package["digest"] = self.s.digest(package)
        self.s.update({**package, "status": "updating"}, installed["local_profile_id"])
        updated = yaml.safe_load(config_path.read_text())
        self.assertEqual(updated["approvals"], {"mode": "manual"})
        self.assertEqual(updated["terminal"], {"timeout": 42})
        self.assertEqual(updated["display_name"], "Updated")

    def test_update_preserves_customer_memory_and_workspace(self):
        p = self.package()
        out = self.s.install(p)
        profile = self.repo.profile_path(out["local_profile_id"])
        (profile / "memories" / "MEMORY.md").write_text("private")
        (profile / "workspace" / "mine.txt").write_text("mine")
        p["definition"]["soul"] = "Updated"
        p["digest"] = self.s.digest(p)
        self.s.update({**p, "status": "updating"}, out["local_profile_id"])
        self.assertEqual((profile / "memories" / "MEMORY.md").read_text(), "private")
        self.assertEqual((profile / "workspace" / "mine.txt").read_text(), "mine")

    def test_uninstall_does_not_remove_ordinary_profile(self):
        profile = self.repo.profile_path("ordinary")
        profile.mkdir()
        self.repo.atomic_yaml(profile / "config.yaml", {"display_name": "Ordinary"})
        (profile / "SOUL.md").write_text("Synthetic local agent")
        with self.assertRaises(ServiceError) as error:
            self.s.uninstall("ordinary")
        self.assertEqual(error.exception.code, "installation_profile_conflict")
        self.assertTrue(profile.is_dir())
        self.assertEqual((profile / "SOUL.md").read_text(), "Synthetic local agent")

    def test_uninstall_move_failure_preserves_profile(self):
        installed = self.s.install(self.package())
        profile = self.repo.profile_path(installed["local_profile_id"])
        before = {path: path.read_bytes() for path in profile.rglob("*") if path.is_file()}
        with patch.object(self.repo, "soft_delete_profile", side_effect=OSError("synthetic")):
            with patch.object(self.s.agents, "sync_profiles_registry") as sync:
                with self.assertRaises(OSError):
                    self.s.uninstall(installed["local_profile_id"])
                sync.assert_not_called()
        self.assertEqual(
            before, {path: path.read_bytes() for path in profile.rglob("*") if path.is_file()}
        )

    def test_uninstall_rejects_malformed_installation_metadata(self):
        profile = self.repo.profile_path("ordinary-metadata")
        profile.mkdir()
        for marker in (True, 42, "not-an-installation", "inst_../outside"):
            with self.subTest(marker=marker):
                self.repo.atomic_yaml(
                    profile / "config.yaml", {"xnobrain": {"marketplace_installation_id": marker}}
                )
                with self.assertRaises(ServiceError) as error:
                    self.s.uninstall("ordinary-metadata")
                self.assertEqual(error.exception.code, "installation_profile_conflict")
                self.assertTrue(profile.is_dir())

    def test_uninstall_moves_profile_to_recoverable_trash(self):
        out = self.s.install(self.package())
        profile = self.repo.profile_path(out["local_profile_id"])
        (profile / "workspace" / "customer.txt").write_text("Synthetic customer work")
        (profile / "memories" / "customer.txt").write_text("Synthetic customer memory")
        before = {
            path.relative_to(profile): path.read_bytes()
            for path in profile.rglob("*") if path.is_file()
        }
        result = self.s.uninstall(out["local_profile_id"])
        self.assertEqual(result["status"], "uninstalled")
        self.assertFalse(profile.exists())
        retained = self.repo.trash_root / result["recoverable_path"]
        self.assertEqual(
            before,
            {path.relative_to(retained): path.read_bytes()
             for path in retained.rglob("*") if path.is_file()},
        )


class MarketplaceExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.profiles = root / "profiles"
        self.profiles.mkdir()
        self.repo = FileRepository(root / "data", self.profiles)
        self.agents = ExportAgents(self.profiles)
        self.service = MarketplaceService(self.repo, self.agents)
        self.profile = self.profiles / "owned-agent"
        (self.profile / "workspace").mkdir(parents=True)
        (self.profile / "skills" / "custom" / "research" / "references").mkdir(parents=True)
        (self.profile / "skills" / "custom" / "research" / "scripts").mkdir()
        (self.profile / "skills" / "custom" / "research" / "assets").mkdir()
        (self.profile / "SOUL.md").write_text("You research safely.\n", encoding="utf-8")
        (self.profile / "workspace" / "AGENTS.md").write_text(
            "Cite public sources.\n", encoding="utf-8"
        )
        (self.profile / "agent.json").write_text(
            json.dumps(
                {
                    "display_name": "Researcher",
                    "description": "Finds cited answers",
                    "owner_email": "private@example.test",
                }
            ),
            encoding="utf-8",
        )
        (self.profile / "config.yaml").write_text(
            """model:\n  default: openai/gpt-safe\nagent:\n  reasoning_effort: high\nproviders:\n  openai:\n    api_key: private-token-value\ntoolsets:\n  - web\nmcp_servers:\n  docs:\n    url: https://private.example.test\n    headers:\n      Authorization: private-token-value\nskills:\n  disabled: []\n""",
            encoding="utf-8",
        )
        skill = self.profile / "skills" / "custom" / "research"
        (skill / "SKILL.md").write_text("# Research\n", encoding="utf-8")
        (skill / "references" / "guide.md").write_text("# Guide\n", encoding="utf-8")
        (skill / "scripts" / "collect.py").write_text("print('ok')\n", encoding="utf-8")
        (skill / "assets" / "template.txt").write_text("Template\n", encoding="utf-8")
        (self.profile / "memories").mkdir()
        (self.profile / "memories" / "MEMORY.md").write_text("personal memory")
        (self.profile / "USER.md").write_text("private user")
        (self.profile / ".env").write_text("TOKEN=private-token-value")
        (self.profile / "state.db").write_bytes(b"conversation history")
        (self.profile / "workspace" / "customer.txt").write_text("private workspace")
        (self.profile / "__pycache__").mkdir()
        (self.profile / "__pycache__" / "cached.pyc").write_bytes(b"cache")

    def tearDown(self):
        self.tmp.cleanup()

    def test_export_is_complete_allowlisted_typed_and_digest_bound(self):
        package = self.service.export("owned-agent", "MIT")

        self.assertEqual(package["schema_version"], 1)
        self.assertEqual(package["source_agent_id"], "owned-agent")
        self.assertEqual(package["digest"], self.service.digest(package))
        self.assertEqual(package["definition"]["soul"], "You research safely.\n")
        self.assertEqual(package["definition"]["prompts"], {"AGENTS.md": "Cite public sources.\n"})
        self.assertEqual(
            package["definition"]["skills"],
            {"custom/research": "# Research\n"},
        )
        self.assertEqual(package["definition"]["tool_requirements"], ["web"])
        self.assertEqual(package["definition"]["mcp_requirements"], ["docs"])
        self.assertEqual(package["definition"]["model_slots"], ["openai/gpt-safe"])
        self.assertEqual(package["definition"]["public_config"]["reasoning_effort"], "high")
        self.assertNotIn("providers", package["definition"]["public_config"])
        exported = json.dumps(package, sort_keys=True)
        for private in (
            "private-token-value",
            "private@example.test",
            "personal memory",
            "private user",
            "conversation history",
            "private workspace",
        ):
            self.assertNotIn(private, exported)
        self.assertEqual(
            sorted(package["definition"]["assets"]),
            [
                "skills/custom/research/assets/template.txt",
                "skills/custom/research/references/guide.md",
                "skills/custom/research/scripts/collect.py",
            ],
        )
        self.assertEqual(package["limits"]["file_count"], 6)
        self.assertGreaterEqual(package["exclusions"]["omitted_file_count"], 7)
        MarketplaceExportPackage.model_validate(package)

    def test_export_is_deterministic_and_changes_with_public_content(self):
        first = self.service.export("owned-agent", "MIT")
        second = self.service.export("owned-agent", "MIT")
        self.assertEqual(first, second)

        guide = self.profile / "skills" / "custom" / "research" / "references" / "guide.md"
        guide.write_text("# Changed guide\n", encoding="utf-8")
        changed = self.service.export("owned-agent", "MIT")
        self.assertNotEqual(changed["digest"], first["digest"])

    def test_export_rejects_profile_and_skill_symlink_attacks(self):
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_text("outside secret")
        link = self.profile / "skills" / "custom" / "research" / "references" / "escape.md"
        link.symlink_to(outside)
        with self.assertRaises(ServiceError) as error:
            self.service.export("owned-agent", "MIT")
        self.assertEqual(error.exception.code, "marketplace_export_rejected")
        link.unlink()

        profile_link = self.profiles / "linked-agent"
        profile_link.symlink_to(self.profile, target_is_directory=True)
        with self.assertRaises(ServiceError) as error:
            self.service.export("linked-agent", "MIT")
        self.assertEqual(error.exception.code, "marketplace_export_rejected")

    def test_export_rejects_secret_like_allowed_content(self):
        (self.profile / "SOUL.md").write_text(
            "api_key = sk-this-is-a-real-looking-secret\n", encoding="utf-8"
        )
        with self.assertRaises(ServiceError) as error:
            self.service.export("owned-agent", "MIT")
        self.assertEqual(error.exception.code, "marketplace_export_credentials_detected")

    def test_export_rejects_file_and_aggregate_limits(self):
        (self.profile / "SOUL.md").write_text("x" * (MAX_EXPORT_FILE_BYTES + 1), encoding="utf-8")
        with self.assertRaises(ServiceError) as error:
            self.service.export("owned-agent", "MIT")
        self.assertEqual(error.exception.status, 413)
        self.assertEqual(error.exception.code, "marketplace_export_too_large")

    def test_export_rejects_case_colliding_skill_paths(self):
        duplicate = self.profile / "skills" / "custom" / "Research"
        duplicate.mkdir()
        (duplicate / "SKILL.md").write_text("# Collision\n", encoding="utf-8")
        with self.assertRaises(ServiceError) as error:
            self.service.export("owned-agent", "MIT")
        self.assertEqual(error.exception.code, "marketplace_export_rejected")

    def test_export_rejects_missing_agent_and_incomplete_definition(self):
        with self.assertRaises(ServiceError) as missing:
            self.service.export("foreign-agent", "MIT")
        self.assertEqual(missing.exception.status, 404)

        (self.profile / "workspace" / "AGENTS.md").unlink()
        with self.assertRaises(ServiceError) as incomplete:
            self.service.export("owned-agent", "MIT")
        self.assertEqual(incomplete.exception.code, "marketplace_export_incomplete")


class APIFakeRouter:
    async def list_connections(self):
        return {"connections": []}

    async def list_models(self):
        return {"data": []}

    async def status(self):
        return {"available": True, "provider_count": 0}


class MarketplaceExportAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.root_profile = root / "root"
        self.profiles = root / "profiles"
        self.root_profile.mkdir()
        self.profiles.mkdir()
        (self.root_profile / "config.yaml").write_text(
            yaml.safe_dump({"model": {"default": "auto"}}), encoding="utf-8"
        )
        self.environment = patch.dict(
            os.environ,
            {
                "HERMES_HOME": str(self.root_profile),
                "HERMES_ROOT_PROFILE": str(self.root_profile),
                "HERMES_PROFILES_ROOT": str(self.profiles),
                "DATA_DIR": str(root / "data"),
                "RUNTIME_INCLUDE_PACKAGED_SKILLS": "false",
            },
        )
        self.environment.start()
        app = FastAPI()
        agents = AgentManager(
            root_profile=self.root_profile,
            profiles_root=self.profiles,
            legacy_agents_root=root / "legacy",
        )
        composition = XNOBrainApplication(
            agents, GlobalConfigManager(root_profile=self.root_profile), APIFakeRouter()
        )
        composition.register(app)
        self.app = app
        self.composition = composition

    def tearDown(self):
        self.environment.stop()
        self.tmp.cleanup()

    async def test_v1_export_route_uses_typed_envelope_and_selected_agent(self):
        created = self.composition.service.create_agent({"display_name": "Publisher"})
        profile = self.profiles / created["id"]
        (profile / "SOUL.md").write_text("Safe soul\n", encoding="utf-8")
        (profile / "workspace" / "AGENTS.md").write_text("Safe instructions\n", encoding="utf-8")

        async with AsyncClient(
            transport=ASGITransport(app=self.app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/xnobrain/api/runtime/v1/marketplace/agents/{created['id']}/export",
                json={"license": "MIT"},
            )
            malformed = await client.post(
                f"/xnobrain/api/runtime/v1/marketplace/agents/{created['id']}/export",
                json={"license": "MIT", "package": {"forged": True}},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["source_agent_id"], created["id"])
        self.assertRegex(response.json()["data"]["digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(malformed.status_code, 422, malformed.text)


class ExportAgents:
    def __init__(self, profiles):
        self.profiles_root = profiles
        self.legacy_agents_root = profiles.parent / "legacy"

    def profile_path(self, agent_id):
        path = self.profiles_root / agent_id
        if not path.is_dir():
            raise ValueError("not found")
        return path
