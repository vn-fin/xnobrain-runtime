"""Build identity must come from the running artifact, never release selection."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xnobrain.services.system import build_sha


class BuildSHATests(unittest.TestCase):
    def test_missing_malformed_and_valid_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sha.txt"
            with patch("xnobrain.repositories.system.SHA_FILE", path):
                self.assertEqual(build_sha().sha, "")
                for raw in (b"unknown", b"abc123", b"\xff", b"a" * 200):
                    path.write_bytes(raw)
                    self.assertEqual(build_sha().sha, "")
                path.write_text("a" * 40 + "\n", encoding="ascii")
                self.assertEqual(build_sha().sha, "a" * 40)
