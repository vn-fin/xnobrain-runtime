"""Behavioral contracts for the trusted Incus container preparation helper."""

from __future__ import annotations

import importlib.util
import os
import re
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load_helper():
    specification = importlib.util.spec_from_file_location(
        "container_preparation_test", ROOT / "runtime/prepare-container.py"
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def decode_environment(encoded: str) -> dict[str, str]:
    """Decode the quoted subset of systemd EnvironmentFile, without a shell."""
    decoded = {}
    for line in encoded.splitlines():
        key, separator, quoted = line.partition("=")
        if not separator or not (quoted.startswith('"') and quoted.endswith('"')):
            raise AssertionError("Expected a complete quoted EnvironmentFile assignment")
        value = re.sub(r'\\([\\"$`])', r"\1", quoted[1:-1])
        decoded[key] = value
    return decoded


class ContainerEnvironmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = load_helper()

    def test_only_operator_runtime_environment_is_forwarded(self):
        expected = {
            "HOME": "/opt/data/home",
            "PATH": "/opt/xnobrain/.tools/node/bin:/usr/bin:/bin",
            "XDG_CONFIG_HOME": "/opt/data/home/.config",
            "HERMES_DESKTOP": "1",
            "RUNTIME_HERMES_HOME": "/opt/data/home with spaces=value",
            "FT_ENABLE_VM_REBALANCE": "false",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
        untrusted = {
            "LD_PRELOAD": "/opt/data/malicious.so",
            "PYTHONPATH": "/opt/data/python",
            "UNRELATED_PID1_SECRET": "synthetic-do-not-forward",
        }
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "pid1-environ"
            source.write_bytes(
                b"".join(
                    f"{key}={value}\0".encode() for key, value in (expected | untrusted).items()
                )
            )
            self.assertEqual(self.helper.read_environment(source), expected)

    def test_environment_values_roundtrip_without_shell_expansion(self):
        expected = {
            "HOME": "/opt/data/home",
            "RUNTIME_VALUE": ' leading and trailing "quote" \\slash $HOME `id` %n #comment; = \u0111 ',
            "RUNTIME_EMPTY": "",
        }
        encoded = self.helper.encode_environment(expected)
        self.assertEqual(decode_environment(encoded), expected)
        self.assertTrue(encoded.endswith("\n"))

    def test_line_break_cannot_inject_an_environment_assignment(self):
        for character in ("\n", "\r"):
            with self.subTest(character=repr(character)):
                with self.assertRaises(ValueError):
                    self.helper.encode_environment(
                        {"RUNTIME_VALUE": f"ok{character}LD_PRELOAD=bad"}
                    )
                with tempfile.TemporaryDirectory() as temporary:
                    source = Path(temporary) / "pid1-environ"
                    source.write_bytes(f"RUNTIME_VALUE=ok{character}LD_PRELOAD=bad\0".encode())
                    with self.assertRaises(ValueError):
                        self.helper.read_environment(source)

    def test_managed_home_and_python_safety_override_pid1_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "pid1-environ"
            source.write_bytes(
                b"HOME=/root\0XDG_CONFIG_HOME=/root/.config\0"
                b"PYTHONDONTWRITEBYTECODE=0\0PYTHONNOUSERSITE=0\0"
            )
            actual = self.helper.read_environment(source)
        self.assertEqual(actual["HOME"], "/opt/data/home")
        self.assertEqual(actual["XDG_CONFIG_HOME"], "/opt/data/home/.config")
        self.assertEqual(actual["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(actual["PYTHONNOUSERSITE"], "1")

    def test_invalid_contract_key_is_rejected(self):
        with self.assertRaises(ValueError):
            self.helper.encode_environment({"RUNTIME_BAD-NAME": "value"})
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "pid1-environ"
            source.write_bytes(b"RUNTIME_BAD-NAME=value\0")
            with self.assertRaises(ValueError):
                self.helper.read_environment(source)

    def test_all_control_characters_are_rejected_when_encoding(self):
        for value in (*range(32), 127):
            with self.subTest(code_point=value), self.assertRaises(ValueError):
                self.helper.encode_environment({"RUNTIME_VALUE": "unsafe" + chr(value)})


class ContainerPreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = load_helper()

    def test_wrong_volume_fails_before_ownership_or_environment_changes(self):
        with (
            patch.object(self.helper.os, "geteuid", return_value=0),
            patch.object(self.helper, "read_environment", return_value={}),
            patch.object(self.helper, "verify_data", side_effect=ValueError("wrong volume")),
            patch.object(self.helper.os, "chown") as chown,
            patch.object(self.helper, "write_environment") as publish,
        ):
            with self.assertRaisesRegex(ValueError, "wrong volume"):
                self.helper.prepare()
        chown.assert_not_called()
        publish.assert_not_called()

    def test_nonroot_preparation_cannot_publish_configuration(self):
        with (
            patch.object(self.helper.os, "geteuid", return_value=1000),
            patch.object(self.helper, "read_environment") as read,
            patch.object(self.helper.os, "chown") as chown,
            patch.object(self.helper, "write_environment") as publish,
        ):
            with self.assertRaises(PermissionError):
                self.helper.prepare()
        read.assert_not_called()
        chown.assert_not_called()
        publish.assert_not_called()

    def test_symlink_data_anchor_is_rejected_without_changing_its_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside"
            outside.mkdir()
            canary = outside / "protected"
            canary.write_text("unchanged")
            original = canary.stat()
            data = root / "data"
            data.symlink_to(outside, target_is_directory=True)
            environment = {
                "RUNTIME_DATA_MOUNT_REQUIRED": "true",
                "RUNTIME_DATA_VOLUME_PATH": str(data),
                "RUNTIME_DATA_VOLUME_ID": "fc1cfddd-df20-4f1e-a210-cfb7ce09db99",
            }
            with (
                patch.object(self.helper, "DATA_ANCHOR", data),
                patch.object(
                    self.helper,
                    "VOLUME_GUARD",
                    ROOT / "xnobrain/integrations/runtime_data_volume.py",
                ),
                patch.object(self.helper, "read_environment", return_value=environment),
                patch.object(self.helper.os, "geteuid", return_value=0),
                patch.object(self.helper.os, "chown") as chown,
                patch.object(self.helper, "write_environment") as publish,
            ):
                with self.assertRaisesRegex(ValueError, "storage_mount_unavailable"):
                    self.helper.prepare()
            chown.assert_not_called()
            publish.assert_not_called()
            self.assertEqual(canary.read_text(), "unchanged")
            self.assertEqual(canary.stat().st_uid, original.st_uid)
            self.assertEqual(canary.stat().st_mode, original.st_mode)

    def test_startup_changes_only_validated_anchor_and_never_follows_data_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            data.mkdir()
            outside = root / "protected-runtime"
            outside.write_text("runtime must stay protected")
            (data / "malicious-symlink").symlink_to(outside)
            (data / "malicious-directory-link").symlink_to(root, target_is_directory=True)
            os.link(outside, data / "hardlink")
            original = outside.stat()
            environment = {"RUNTIME_DATA_VOLUME_PATH": str(data)}
            with (
                patch.object(self.helper, "DATA_ANCHOR", data),
                patch.object(self.helper.os, "geteuid", return_value=0),
                patch.object(self.helper, "read_environment", return_value=environment),
                patch.object(self.helper, "verify_data") as verify,
                patch.object(
                    self.helper.pwd,
                    "getpwnam",
                    return_value=SimpleNamespace(pw_uid=1000, pw_gid=1000),
                ),
                patch.object(self.helper.os, "chown") as chown,
                patch.object(self.helper, "write_environment") as publish,
            ):
                self.helper.prepare()
            verify.assert_called_once_with(environment)
            chown.assert_called_once_with(data, 1000, 1000, follow_symlinks=False)
            publish.assert_called_once_with(environment)
            self.assertEqual(outside.read_text(), "runtime must stay protected")
            self.assertEqual(outside.stat().st_uid, original.st_uid)
            self.assertEqual(outside.stat().st_mode, original.st_mode)

    def test_root_runtime_account_is_refused(self):
        for uid, gid in ((0, 1000), (1000, 0)):
            with (
                self.subTest(uid=uid, gid=gid),
                patch.object(self.helper.os, "geteuid", return_value=0),
                patch.object(self.helper, "read_environment", return_value={}),
                patch.object(self.helper, "verify_data"),
                patch.object(
                    self.helper.pwd,
                    "getpwnam",
                    return_value=SimpleNamespace(pw_uid=uid, pw_gid=gid),
                ),
                patch.object(self.helper.os, "chown") as chown,
            ):
                with self.assertRaisesRegex(ValueError, "non-root runtime account"):
                    self.helper.prepare()
                chown.assert_not_called()

    def test_missing_or_wrong_data_binding_does_not_load_code(self):
        for environment in (
            {},
            {"RUNTIME_DATA_MOUNT_REQUIRED": "true", "RUNTIME_DATA_VOLUME_PATH": "/tmp/attacker"},
        ):
            with (
                self.subTest(environment=environment),
                patch.object(self.helper.importlib.util, "spec_from_file_location") as load,
            ):
                with self.assertRaisesRegex(ValueError, "managed data volume required"):
                    self.helper.verify_data(environment)
                load.assert_not_called()


class ContainerEnvironmentPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = load_helper()

    @staticmethod
    def trust_test_directory(directory):
        # Ordinary source tests are nonroot. Fake only the root-owned config
        # directory metadata; all publication and symlink behavior is real.
        original = Path.lstat

        def metadata(path):
            info = original(path)
            if path == directory:
                return SimpleNamespace(st_uid=0, st_mode=info.st_mode)
            return info

        return patch.object(Path, "lstat", metadata)

    def test_atomic_replacement_has_private_permissions_and_no_temp_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "private"
            directory.mkdir(mode=0o700)
            target = directory / "environment"
            target.write_text('RUNTIME_VALUE="old"\n')
            previous_inode = target.stat().st_ino
            original_replace = os.replace
            previous_contents = []

            def replace(source, destination):
                previous_contents.append(target.read_text())
                self.assertEqual(stat.S_IMODE(Path(source).stat().st_mode), 0o600)
                original_replace(source, destination)

            with (
                self.trust_test_directory(directory),
                patch.object(self.helper.os, "replace", side_effect=replace),
            ):
                self.helper.write_environment({"RUNTIME_VALUE": "new"}, directory)
            self.assertEqual(previous_contents, ['RUNTIME_VALUE="old"\n'])
            self.assertEqual(decode_environment(target.read_text()), {"RUNTIME_VALUE": "new"})
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertNotEqual(target.stat().st_ino, previous_inode)
            self.assertEqual(list(directory.iterdir()), [target])

    def test_failed_publish_keeps_previous_file_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "private"
            directory.mkdir(mode=0o700)
            target = directory / "environment"
            target.write_text('RUNTIME_VALUE="old"\n')
            with (
                self.trust_test_directory(directory),
                patch.object(self.helper.os, "replace", side_effect=OSError("publish failed")),
            ):
                with self.assertRaisesRegex(OSError, "publish failed"):
                    self.helper.write_environment({"RUNTIME_VALUE": "new"}, directory)
            self.assertEqual(target.read_text(), 'RUNTIME_VALUE="old"\n')
            self.assertEqual(list(directory.iterdir()), [target])

    def test_environment_symlink_cannot_replace_outside_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "private"
            directory.mkdir(mode=0o700)
            outside = root / "protected"
            outside.write_text("unchanged")
            target = directory / "environment"
            target.symlink_to(outside)
            with (
                self.trust_test_directory(directory),
                self.assertRaisesRegex(ValueError, "unsafe runtime configuration file"),
            ):
                self.helper.write_environment({"RUNTIME_VALUE": "new"}, directory)
            self.assertTrue(target.is_symlink())
            self.assertEqual(outside.read_text(), "unchanged")
            self.assertEqual(list(directory.iterdir()), [target])

    def test_symlink_or_group_writable_config_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside"
            outside.mkdir(mode=0o700)
            symlink = root / "symlink"
            symlink.symlink_to(outside, target_is_directory=True)
            unsafe = root / "group-writable"
            unsafe.mkdir(mode=0o770)
            unsafe.chmod(0o770)
            for directory in (symlink, unsafe):
                with self.subTest(directory=directory.name), self.trust_test_directory(directory):
                    with self.assertRaisesRegex(
                        ValueError, "unsafe runtime configuration directory"
                    ):
                        self.helper.write_environment({"RUNTIME_VALUE": "new"}, directory)
            self.assertEqual(list(outside.iterdir()), [])
            self.assertEqual(list(unsafe.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
