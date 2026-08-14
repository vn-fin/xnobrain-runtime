"""Workspace create-path validation regression tests."""

import unittest

from xnobrain.integrations.hermes_support import AgentAPIError
from xnobrain.services.workspaces import _validate_create_path


class WorkspaceCreatePathTests(unittest.TestCase):
    def test_allows_nested_safe_paths(self) -> None:
        self.assertEqual(_validate_create_path("reports/notes.md"), "reports/notes.md")

    def test_rejects_traversal_and_separator_variants(self) -> None:
        invalid = (
            "../escape.txt",
            "reports/../escape.txt",
            "/absolute.txt",
            r"reports\escape.txt",
            "reports/%2e%2e/escape.txt",
            "reports%2Fescape.txt",
            "reports∕escape.txt",
        )
        for path in invalid:
            with self.subTest(path=path), self.assertRaises(AgentAPIError):
                _validate_create_path(path)


if __name__ == "__main__":
    unittest.main()
