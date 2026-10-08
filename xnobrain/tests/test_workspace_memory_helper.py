"""Exercise root helper policy without accessing privileged host interfaces."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import mock_open, patch


class MemoryHelperTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[2] / "runtime" / "memory-reclaim-helper.py"
        spec = importlib.util.spec_from_file_location("memory_helper_test", source)
        self.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.helper)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.helper.RECEIPT = Path(temporary.name) / "receipt.json"
        self.request = {"operation_id": "mem_test", "fence": 1}
        self.sample = {"Cached": 512 << 20, "MemAvailable": 2 << 30, "Dirty": 0, "Writeback": 0}

    def run_helper(self, *, supported=True):
        output = mock_open()
        with (
            patch.object(self.helper, "sample", return_value=self.sample),
            patch.object(self.helper, "supported", return_value=supported),
            patch.object(Path, "open", output),
        ):
            # Path.open is mocked only for the kernel write; root receipt uses
            # os.open/replace and remains a real durable file in the fixture.
            result = self.helper.execute(self.request)
        return result, output

    def test_clean_cache_only_and_duplicate_never_reexecutes(self):
        result, output = self.run_helper()
        self.assertEqual(result["state"], "evicted")
        output().write.assert_called_once_with("1\n")
        receipt = json.loads(self.helper.RECEIPT.read_text())
        with patch.object(self.helper, "sample", side_effect=AssertionError("duplicate executed")):
            self.assertEqual(self.helper.execute(self.request), receipt)
        with self.assertRaises(ValueError):
            self.helper.execute({"operation_id": "mem_old", "fence": 1})

    def test_guest_pressure_small_cache_or_disabled_backend_skip(self):
        for name, value in (
            ("Dirty", 65 << 20),
            ("Writeback", 1),
            ("MemAvailable", 1),
            ("Cached", 1),
        ):
            with self.subTest(name=name):
                self.request["operation_id"] += "a"
                self.request["fence"] += 1
                old = self.sample[name]
                self.sample[name] = value
                # Read the real existing receipt while intercepting only writes
                # to the kernel target on the eligible path.
                with (
                    patch.object(self.helper, "sample", return_value=self.sample),
                    patch.object(self.helper, "supported", return_value=True),
                ):
                    self.assertEqual(self.helper.execute(self.request)["state"], "skipped")
                self.sample[name] = old
        self.request["operation_id"] += "a"
        self.request["fence"] += 1
        with (
            patch.object(self.helper, "sample", return_value=self.sample),
            patch.object(self.helper, "supported", return_value=False),
        ):
            self.assertEqual(self.helper.execute(self.request)["reason"], "backend_unavailable")

    def test_arbitrary_command_or_path_and_invalid_fence_rejected(self):
        for request in (
            {**self.request, "command": "sync"},
            {**self.request, "path": "/tmp/target"},
            {**self.request, "fence": True},
            {**self.request, "fence": -1},
            {**self.request, "operation_id": "../../target"},
        ):
            with self.assertRaises(ValueError):
                self.helper.execute(request)

    def test_only_exact_owned_supervision_process_is_passive(self):
        from xnobrain.integrations.workspace_memory import (
            HELPER_STATUS_COMMAND,
            _passive_helper_probe,
        )

        entry = self.helper.RECEIPT.parent
        (entry / "stat").write_text("42 (systemctl) S 123 0 0")
        args = b"\0".join(arg.encode() for arg in HELPER_STATUS_COMMAND) + b"\0"
        (entry / "cmdline").write_bytes(args)
        (entry / "exe").symlink_to(HELPER_STATUS_COMMAND[0])
        self.assertTrue(_passive_helper_probe(entry, 123))
        self.assertFalse(_passive_helper_probe(entry, 456))
        (entry / "cmdline").write_bytes(args.replace(b"show\0", b"restart\0"))
        self.assertFalse(_passive_helper_probe(entry, 123))
        (entry / "cmdline").write_bytes(args)
        (entry / "exe").unlink()
        (entry / "exe").symlink_to("/tmp/systemctl")
        self.assertFalse(_passive_helper_probe(entry, 123))
