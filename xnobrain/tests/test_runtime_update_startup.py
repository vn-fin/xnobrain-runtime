"""Startup cleanup for legacy catalog MCP credentials."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

from xnobrain.integrations import extension_policy
from xnobrain.repositories import FileRepository
from xnobrain.services.runtime_update_startup import cleanup_legacy_catalog_mcp


class LegacyCatalogMCPCleanupTests(unittest.TestCase):
    def test_removes_legacy_catalog_data_without_following_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "home"
            profiles = root / "profiles"
            profile = profiles / "research"
            profile.mkdir(parents=True)
            repository = FileRepository(Path(directory) / "data", profiles, root_profile=root)
            config = {
                extension_policy.MANAGED_MCP_KEY: {
                    "xnobrain-linear": {"url": "https://example.test", "auth": "oauth"},
                    "xnobrain-render": {
                        "url": "https://example.test",
                        "headers": {"Authorization": "secret"},
                    },
                    "custom": {"url": "https://example.test", "headers": {"Authorization": "keep"}},
                }
            }
            repository.atomic_yaml(profile / "config.yaml", config)
            extension_policy.save_state(
                profile,
                {
                    "plugins": {},
                    "hooks": {},
                    "revision": 1,
                    "mcp": {
                        "linear": {"server_name": "xnobrain-linear"},
                        "render": {"server_name": "xnobrain-render"},
                    },
                },
            )
            token_dir = profile / "mcp-tokens"
            token_dir.mkdir()
            (token_dir / "xnobrain-linear.json").write_text("token", encoding="utf-8")
            target = profile / "outside-token"
            target.write_text("still here", encoding="utf-8")
            (token_dir / "xnobrain-link.json").symlink_to(target)
            platform = SimpleNamespace(repository=repository)

            cleanup_legacy_catalog_mcp(platform)

            cleaned = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(set(cleaned[extension_policy.MANAGED_MCP_KEY]), {"custom"})
            self.assertEqual(extension_policy.load_state(profile)["mcp"], {})
            self.assertFalse((token_dir / "xnobrain-linear.json").exists())
            self.assertFalse((token_dir / "xnobrain-link.json").is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "still here")


if __name__ == "__main__":
    unittest.main()
