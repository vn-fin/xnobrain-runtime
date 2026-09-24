"""Real filesystem no-replace behavior, including empty-directory collisions."""

import tempfile
import unittest
from pathlib import Path

from xnobrain.repositories.base import StoreError
from xnobrain.repositories.portability_publication import rename_without_replace


class PublicationTests(unittest.TestCase):
    def test_directory_publication_never_clobbers_existing_empty_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "source", root / "target"
            source.mkdir()
            (source / "content").write_text("owned")
            target.mkdir()
            with self.assertRaises(StoreError):
                rename_without_replace(source, target)
            self.assertEqual(list(target.iterdir()), [])
            self.assertEqual((source / "content").read_text(), "owned")
            target.rmdir()
            rename_without_replace(source, target)
            self.assertFalse(source.exists())
            self.assertEqual((target / "content").read_text(), "owned")
