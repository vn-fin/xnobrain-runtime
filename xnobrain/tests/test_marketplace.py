import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from xnobrain.repositories.files import FileRepository
from xnobrain.services.base import ServiceError
from xnobrain.services.marketplace import MarketplaceService


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

    def test_update_preserves_customer_memory_and_workspace(self):
        p = self.package()
        out = self.s.install(p)
        profile = self.repo.profile_path(out["local_profile_id"])
        (profile / "memories" / "MEMORY.md").write_text("private")
        (profile / "workspace" / "mine.txt").write_text("mine")
        p["definition"]["soul"] = "Updated"
        p["digest"] = self.s.digest(p)
        self.s.update(p, out["local_profile_id"])
        self.assertEqual((profile / "memories" / "MEMORY.md").read_text(), "private")
        self.assertEqual((profile / "workspace" / "mine.txt").read_text(), "mine")

    def test_uninstall_moves_profile_to_recoverable_trash(self):
        out = self.s.install(self.package())
        result = self.s.uninstall(out["local_profile_id"])
        self.assertEqual(result["status"], "uninstalled")
        self.assertFalse(self.repo.profile_path(out["local_profile_id"]).exists())
