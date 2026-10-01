"""First-boot preset isolation, integrity and recovery tests."""

import importlib.util
import json
import os
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "integrations/workspace_profile_preset.py"
SPEC = importlib.util.spec_from_file_location("workspace_profile_preset", MODULE_PATH)
preset = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preset)


class ProfilePresetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "root"
        self.profiles = self.base / "profiles"
        self.root.mkdir()
        self.profiles.mkdir()
        (self.root / "config.yaml").write_text("model:\n  default: auto\n")
        self.source = self.base / "source.zip"
        self.output = self.base / "preset.zip"
        with zipfile.ZipFile(self.source, "w") as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {"format": "xnobrain-bundle", "version": 1, "agents": [{"id": "cfa-review"}]}
                ),
            )
            for name, content in {
                "agent.json": '{"display_name":"CFA Review","name":"old"}',
                "SOUL.md": "Review research",
                "skills/research/SKILL.md": "Research skill",
                "workspace/report.csv": "a,b\n1,2",
                "state.db": "old state",
                ".env": "publisher secret",
                "config.yaml": "publisher config",
                "workspace/.env": "nested secret",
                "workspace/auth.json": "nested auth",
                "workspace/cache/log.txt": "old cache",
                "logs/agent.log": "old log",
            }.items():
                archive.writestr("profiles/cfa-review/" + name, content)

    def build(self):
        return preset.prepare([self.source], self.output)

    def test_preparation_and_install_receive_workspace_defaults(self):
        digest = self.build()
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(len(archive.namelist()), 5)
        preset.install(self.output, digest, self.root, self.profiles)
        profile = self.profiles / "cfa-review"
        self.assertEqual(
            (profile / "config.yaml").read_text(), (self.root / "config.yaml").read_text()
        )
        self.assertEqual(json.loads((profile / "agent.json").read_text())["name"], "cfa-review")
        self.assertEqual((profile / "workspace/report.csv").read_text(), "a,b\n1,2")
        self.assertFalse((profile / "state.db").exists())
        self.assertFalse((profile / ".env").exists())

    def test_checksum_failure_leaves_profiles_untouched(self):
        self.build()
        with self.assertRaises(preset.PresetError):
            preset.install(self.output, "0" * 64, self.root, self.profiles)
        self.assertEqual(list(self.profiles.iterdir()), [])

    def test_fresh_boot_initializes_config_from_packaged_template(self):
        (self.root / "config.yaml").unlink()
        template = self.root / "profile-template"
        template.mkdir()
        defaults = "model:\n  default: auto\napproval_mode: ask\n"
        (template / "config.yaml").write_text(defaults)
        preset.install(self.output, self.build(), self.root, self.profiles)
        self.assertEqual((self.root / "config.yaml").read_text(), defaults)
        self.assertEqual((self.profiles / "cfa-review/config.yaml").read_text(), defaults)
        self.assertEqual(stat.S_IMODE((self.root / "config.yaml").stat().st_mode), 0o600)
        self.assertTrue((self.profiles / preset.MARKER).is_file())
        self.assertFalse(list(self.root.glob(".preset-config-*")))

    def test_existing_config_wins_over_packaged_template(self):
        template = self.root / "profile-template"
        template.mkdir()
        (template / "config.yaml").write_text("packaged defaults")
        (self.root / "config.yaml").write_text("workspace custom configuration")
        preset.install(self.output, self.build(), self.root, self.profiles)
        for config in (self.root / "config.yaml", self.profiles / "cfa-review/config.yaml"):
            self.assertEqual(config.read_text(), "workspace custom configuration")

    def test_missing_template_fails_before_profile_extraction(self):
        (self.root / "config.yaml").unlink()
        digest = self.build()
        with self.assertRaisesRegex(preset.PresetError, "template unavailable"):
            preset.install(self.output, digest, self.root, self.profiles)
        self.assertFalse((self.root / "config.yaml").exists())
        self.assertEqual(list(self.profiles.iterdir()), [])

    def test_root_config_symlink_is_rejected(self):
        (self.root / "config.yaml").unlink()
        outside = self.base / "outside.yaml"
        outside.write_text("outside configuration")
        (self.root / "config.yaml").symlink_to(outside)
        digest = self.build()
        with self.assertRaises(preset.PresetError):
            preset.install(self.output, digest, self.root, self.profiles)
        self.assertEqual(list(self.profiles.iterdir()), [])
        self.assertEqual(outside.read_text(), "outside configuration")

    def test_script_permissions_and_interrupted_install(self):
        with zipfile.ZipFile(self.source, "a") as archive:
            entry = zipfile.ZipInfo("profiles/cfa-review/skills/research/run.sh")
            entry.external_attr = (stat.S_IFREG | 0o4777) << 16
            archive.writestr(entry, "#!/bin/sh\nexit 0\n")
        digest = self.build()
        with patch.object(preset, "sync_tree", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                preset.install(self.output, digest, self.root, self.profiles)
        self.assertEqual(list(self.profiles.iterdir()), [])
        preset.install(self.output, digest, self.root, self.profiles)
        script = self.profiles / "cfa-review/skills/research/run.sh"
        self.assertEqual(stat.S_IMODE(script.stat().st_mode), 0o700)

    def test_retry_preserves_user_changes(self):
        digest = self.build()
        preset.install(self.output, digest, self.root, self.profiles)
        user_file = self.profiles / "cfa-review/SOUL.md"
        user_file.write_text("User edited")
        (self.profiles / preset.MARKER).unlink()  # Interrupted before global commit.
        preset.install(self.output, digest, self.root, self.profiles)
        self.assertEqual(user_file.read_text(), "User edited")

    def test_existing_imported_profile_is_not_replaced(self):
        (self.profiles / "cfa-review").mkdir()
        (self.profiles / "cfa-review/user.txt").write_text("existing import")
        preset.install(self.output, self.build(), self.root, self.profiles)
        self.assertEqual((self.profiles / "cfa-review/user.txt").read_text(), "existing import")
        self.assertFalse((self.profiles / "cfa-review/SOUL.md").exists())

    def test_rejects_traversal_and_links_before_mutation(self):
        for name, mode in [
            ("profiles/cfa-review/../../escape", 0),
            ("profiles/cfa-review/workspace/link", stat.S_IFLNK | 0o777),
        ]:
            with self.subTest(name=name):
                self.build()
                with zipfile.ZipFile(self.output, "a") as archive:
                    entry = zipfile.ZipInfo(name)
                    entry.external_attr = mode << 16
                    archive.writestr(entry, "outside")
                with self.assertRaises(preset.PresetError):
                    preset.install(
                        self.output, preset.sha256(self.output), self.root, self.profiles
                    )
                self.assertEqual(list(self.profiles.iterdir()), [])

    def test_duplicate_profile_input_rejected(self):
        with self.assertRaises(preset.PresetError):
            preset.prepare([self.source, self.source], self.output)
        self.assertFalse(self.output.exists())

    def test_download_failure_and_completed_marker(self):
        digest = self.build()
        env = {
            "RUNTIME_PROFILE_PRESET_URL": "https://object.example/preset?secret=hidden",
            "RUNTIME_PROFILE_PRESET_SHA256": digest,
            "RUNTIME_HERMES_HOME": str(self.root),
            "RUNTIME_HERMES_PROFILES_ROOT": str(self.profiles),
        }
        with (
            patch.dict(os.environ, env),
            patch.object(
                preset, "download", side_effect=preset.PresetError("download unavailable")
            ) as downloader,
        ):
            with self.assertRaises(preset.PresetError):
                preset.seed_from_environment()
            self.assertFalse((self.profiles / preset.MARKER).exists())
            preset.install(self.output, digest, self.root, self.profiles)
            downloader.reset_mock()
            preset.seed_from_environment()
            downloader.assert_not_called()

    def test_payload_limits(self):
        self.build()
        with patch.object(preset, "MAX_EXPANDED", 1), self.assertRaises(preset.PresetError):
            preset.install(self.output, preset.sha256(self.output), self.root, self.profiles)

    def test_existing_volume_guard_precedes_seed(self):
        script = (MODULE_PATH.parents[2] / "scripts/prepare-service-data.sh").read_text()
        self.assertLess(
            script.index(".xnobrain-volume-initialized"),
            script.index("workspace_profile_preset.py"),
        )
        self.assertLess(
            script.index("rollout-preserve-data"), script.index("workspace_profile_preset.py")
        )
        self.assertLess(
            script.index("workspace_profile_preset.py"),
            script.index('touch "${RUNTIME_DATA_VOLUME_PATH}'),
        )


class PresetDownloadPolicyTests(unittest.TestCase):
    def test_private_http_and_https(self):
        for url in (
            "http://10.10.90.133:19093/preset.zip",
            "http://172.16.0.1/preset.zip",
            "http://192.168.1.2/preset.zip",
            "https://objects.example/preset?signature=secret",
        ):
            self.assertTrue(preset.valid_download_url(url))
        for url in (
            "http://public.example/preset.zip",
            "http://8.8.8.8/preset.zip",
            "http://127.0.0.1/preset.zip",
            "http://169.254.169.254/preset.zip",
            "http://10.0.0.1/a?token=secret",
            "file:///tmp/preset.zip",
            "https://user:secret@example.com/preset.zip",
            "http://10.0.0.1/a\nX=1",
        ):
            self.assertFalse(preset.valid_download_url(url))

    def test_redirects_preserve_transport_boundary(self):
        handler = preset.PresetRedirect()
        for source, target in (
            ("https://s3.example/a", "http://10.0.0.1/a"),
            ("http://10.0.0.1/a", "http://10.0.0.2/a"),
            ("http://10.0.0.1/a", "https://external.example/a"),
        ):
            request = preset.urllib.request.Request(source)
            with self.assertRaises(preset.PresetError):
                handler.redirect_request(request, None, 302, "Found", {}, target)
        request = preset.urllib.request.Request("http://10.0.0.1/a")
        redirected = handler.redirect_request(request, None, 302, "Found", {}, "http://10.0.0.1/b")
        self.assertEqual(redirected.full_url, "http://10.0.0.1/b")


if __name__ == "__main__":
    unittest.main()
