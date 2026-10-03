"""Only the canonical Hermes profiles alias is excluded from duplicate traversal."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xnobrain.integrations.runtime_update_storage import (
    RuntimeUpdateStorage,
    RuntimeUpdateStorageError,
)


class ProfileAliasTests(unittest.TestCase):
    def test_canonical_alias_is_skipped_but_target_is_inspected(self):
        with tempfile.TemporaryDirectory() as directory:
            anchor = Path(directory).resolve()
            data, profiles, root = (anchor / name for name in ("data", "profiles", "root"))
            for path in (data, profiles, root):
                path.mkdir()
            target = profiles / "safe.txt"
            target.write_text("fixture")
            alias = root / "profiles"
            alias.symlink_to(profiles, target_is_directory=True)
            with patch.dict(
                os.environ,
                {
                    "RUNTIME_UPDATE_DATA_PATH": str(anchor),
                    "RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": "",
                },
            ):
                storage = RuntimeUpdateStorage(data, profiles, root)
                self.assertEqual(list(storage._walk_files(root)), [])
                self.assertEqual(list(storage._walk_files(profiles)), [target])
                alias.unlink()
                alias.symlink_to(data, target_is_directory=True)
                with self.assertRaises(RuntimeUpdateStorageError):
                    list(storage._walk_files(root))
                alias.unlink()
                (profiles / "unsafe").symlink_to(data, target_is_directory=True)
                with self.assertRaises(RuntimeUpdateStorageError):
                    list(storage._walk_files(profiles))
