"""Boot-scoped memory activity state never invalidates the durable data manifest."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xnobrain.integrations.runtime_update_storage import RuntimeUpdateStorage


class MemoryActivityManifestTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        anchor = Path(self.directory.name).resolve()
        self.data, profiles, root = (anchor / name for name in ("data", "profiles", "root"))
        for path in (self.data, profiles, root):
            path.mkdir()
        (self.data / "agents.json").write_text("{}")
        environment = patch.dict(
            os.environ,
            {"RUNTIME_UPDATE_DATA_PATH": str(anchor), "RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": ""},
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(self.directory.cleanup)
        self.storage = RuntimeUpdateStorage(self.data, profiles, root)

    def test_activity_state_created_after_checkpoint_is_ignored(self):
        before = self.storage.manifest()
        memory = self.data / "runtime-memory"
        memory.mkdir(mode=0o700)
        (memory / "activity.json").write_text('{"activity_epoch": 1}\n')
        (memory / "activity.guard").write_bytes(os.urandom(16))
        (memory / "reclaim.json").write_text("{}\n")

        after = self.storage.manifest()

        self.assertEqual(after["digest"], before["digest"])
        self.assertEqual(after["file_count"], before["file_count"])

    def test_durable_data_change_still_changes_digest(self):
        before = self.storage.manifest()
        (self.data / "runtime-memory-notes.txt").write_text("durable")

        after = self.storage.manifest()

        self.assertNotEqual(after["digest"], before["digest"])
        self.assertIn(
            "data/runtime-memory-notes.txt", [entry["path"] for entry in after["entries"]]
        )


if __name__ == "__main__":
    unittest.main()
