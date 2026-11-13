"""Install and enable the packaged social-channels skill for channel agents."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml

from xnobrain.integrations.hermes import AgentManager
from xnobrain.repositories.files import FileRepository
from xnobrain.services.base import ServiceError
from xnobrain.services.channels import ChannelsService
from xnobrain.services.runtime_update_startup import sync_social_channels_skill

SKILL_ID = "social-channels"
PACKAGED = Path(__file__).resolve().parents[1] / "assets" / "skills" / SKILL_ID


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


class _Platform:
    def __init__(self, repository: FileRepository, root: Path, agents: AgentManager):
        self.repository = repository
        self.root = root
        self.agents = agents
        self.config = SimpleNamespace(root_profile=root)

    def _conversation_profile(self, agent_id: str) -> Path:
        if agent_id == "big-brother":
            return self.root
        profile = self.repository.profile_path(agent_id)
        if not profile.is_dir():
            raise ServiceError("agent profile not found", status=404, code="not_found")
        return profile

    def _snapshot_agent(self, agent_id, kind, target, content):
        return self.repository.snapshot(agent_id, kind, target, content)


class SocialChannelsSkillTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.profiles = root / "profiles"
        self.root = self.profiles / "big-brother"
        self.profile = self.profiles / "agent-1"
        self.other = self.profiles / "agent-2"
        for profile in (self.root, self.profile, self.other):
            profile.mkdir(parents=True)
        self.profile.joinpath("config.yaml").write_text(
            "platforms:\n"
            "  zalo:\n"
            "    enabled: false\n"
            "    extra:\n"
            "      dm_policy: disabled\n",
            encoding="utf-8",
        )
        self.other.joinpath("config.yaml").write_text(
            "platforms:\n  zalo:\n    enabled: false\n", encoding="utf-8"
        )
        self.root.joinpath("config.yaml").write_text(
            "gateway:\n"
            "  multiplex_profiles: false\n"
            "  multiplex_profile_allowlist: []\n",
            encoding="utf-8",
        )
        self.root.joinpath("gateway.pid").write_text(
            '{"pid": 111, "kind": "gateway"}', encoding="utf-8"
        )
        self.environment = mock.patch.dict(
            os.environ,
            {
                "FT_ENABLE_AGENT_CHANNELS": "true",
                "HERMES_ROOT_PROFILE": str(self.root),
                "DATA_DIR": str(root / "data"),
                "RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": "",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.repository = FileRepository(
            root / "data", self.profiles, root_profile=self.root
        )
        self.agents = AgentManager(
            root_profile=self.root,
            profiles_root=self.profiles,
            legacy_agents_root=root / "legacy",
            profile_template=root / "template",
        )
        self.service = ChannelsService(
            _Platform(self.repository, self.root, self.agents)
        )
        self.kill = mock.patch("os.kill", return_value=None)
        self.kill.start()
        self.addCleanup(self.kill.stop)

    def _skill_dir(self, profile: Path) -> Path:
        return profile / "skills" / "social" / SKILL_ID

    def _assert_installed(self, profile: Path) -> None:
        skill = self._skill_dir(profile)
        self.assertEqual(
            (skill / "SKILL.md").read_bytes(), (PACKAGED / "SKILL.md").read_bytes()
        )
        for platform in ("zalo", "zalo_bot"):
            self.assertEqual(
                (skill / "platforms" / f"{platform}.md").read_bytes(),
                (PACKAGED / "platforms" / f"{platform}.md").read_bytes(),
            )
        disabled = _read_yaml(profile / "config.yaml").get("skills", {}).get(
            "disabled", []
        )
        self.assertNotIn(SKILL_ID, disabled)

    def test_enabling_a_channel_installs_and_enables_the_skill(self):
        config = _read_yaml(self.profile / "config.yaml")
        config["skills"] = {"disabled": [SKILL_ID]}
        self.profile.joinpath("config.yaml").write_text(
            yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
        )

        self.service.set_channel_enabled("agent-1", True)

        self._assert_installed(self.profile)

    def test_profile_without_a_channel_gets_nothing(self):
        self.service.sync_social_channels_skill()

        self.assertFalse(self._skill_dir(self.other).exists())
        self.assertFalse(self._skill_dir(self.root).exists())

    def test_modified_skill_is_overwritten_and_identical_skill_is_not_rewritten(self):
        self.service.set_channel_enabled("agent-1", True)
        skill_file = self._skill_dir(self.profile) / "SKILL.md"
        skill_file.write_text("tampered\n", encoding="utf-8")
        old = 1_000_000_000
        os.utime(skill_file, (old, old))

        self.service.sync_social_channels_skill()

        self.assertEqual(skill_file.read_bytes(), (PACKAGED / "SKILL.md").read_bytes())
        self.assertNotEqual(skill_file.stat().st_mtime, old)

        os.utime(skill_file, (old, old))
        self.service.sync_social_channels_skill()

        self.assertEqual(skill_file.stat().st_mtime, old)

    def test_root_with_a_channel_enabled_gets_the_skill(self):
        self.service.set_channel_enabled("big-brother", True)

        self._assert_installed(self.root)

    def test_disabling_a_channel_keeps_the_skill(self):
        self.service.set_channel_enabled("agent-1", True)
        self.service.set_channel_enabled("agent-1", False)

        self.assertTrue((self._skill_dir(self.profile) / "SKILL.md").is_file())

    def test_startup_sync_runs_only_when_channels_are_enabled(self):
        calls: list[bool] = []
        platform = SimpleNamespace(
            channels=SimpleNamespace(
                sync_social_channels_skill=lambda: calls.append(True)
            )
        )
        with mock.patch.dict(
            os.environ, {"FT_ENABLE_AGENT_CHANNELS": "true"}, clear=False
        ):
            sync_social_channels_skill(platform)
        self.assertEqual(calls, [True])

        calls.clear()
        with mock.patch.dict(
            os.environ, {"FT_ENABLE_AGENT_CHANNELS": "false"}, clear=False
        ):
            sync_social_channels_skill(platform)
        self.assertEqual(calls, [])

    def test_startup_sync_swallows_errors_without_content(self):
        def fail():
            raise RuntimeError("secret profile content")

        platform = SimpleNamespace(
            channels=SimpleNamespace(sync_social_channels_skill=fail)
        )
        with mock.patch.dict(
            os.environ, {"FT_ENABLE_AGENT_CHANNELS": "true"}, clear=False
        ), self.assertLogs(
            "xnobrain.services.runtime_update_startup", level="WARNING"
        ) as logs:
            sync_social_channels_skill(platform)

        self.assertNotIn("secret profile content", "\n".join(logs.output))
        self.assertIn("Social channels skill sync failed", "\n".join(logs.output))

    def test_packaged_skill_matches_the_channel_contract(self):
        text = (PACKAGED / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = yaml.safe_load(text.split("---", 2)[1])

        self.assertEqual(frontmatter["name"], SKILL_ID)
        self.assertNotIn("version", frontmatter)
        self.assertIn("channel settings in the app", " ".join(text.split()))
        for line in text.splitlines():
            if "agent send" in line:
                self.assertIn("never", line.casefold())


if __name__ == "__main__":
    unittest.main()
