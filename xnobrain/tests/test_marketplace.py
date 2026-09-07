import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from xnobrain.app import XNOBrainApplication
from xnobrain.integrations import AgentManager, GlobalConfigManager
from xnobrain.models.marketplace import MarketplaceExportPackage
from xnobrain.repositories.files import FileRepository
from xnobrain.services.base import ServiceError
from xnobrain.services.marketplace import (
    MAX_EXPORT_FILE_BYTES,
    MarketplaceService,
)


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


class MarketplaceExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.profiles = root / "profiles"
        self.profiles.mkdir()
        self.repo = FileRepository(root / "data", self.profiles)
        self.agents = ExportAgents(self.profiles)
        self.service = MarketplaceService(self.repo, self.agents)
        self.profile = self.profiles / "owned-agent"
        (self.profile / "workspace").mkdir(parents=True)
        (self.profile / "skills" / "custom" / "research" / "references").mkdir(parents=True)
        (self.profile / "skills" / "custom" / "research" / "scripts").mkdir()
        (self.profile / "skills" / "custom" / "research" / "assets").mkdir()
        (self.profile / "SOUL.md").write_text("You research safely.\n", encoding="utf-8")
        (self.profile / "workspace" / "AGENTS.md").write_text(
            "Cite public sources.\n", encoding="utf-8"
        )
        (self.profile / "agent.json").write_text(
            json.dumps(
                {
                    "display_name": "Researcher",
                    "description": "Finds cited answers",
                    "owner_email": "private@example.test",
                }
            ),
            encoding="utf-8",
        )
        (self.profile / "config.yaml").write_text(
            """model:\n  default: openai/gpt-safe\nagent:\n  reasoning_effort: high\nproviders:\n  openai:\n    api_key: private-token-value\ntoolsets:\n  - web\nmcp_servers:\n  docs:\n    url: https://private.example.test\n    headers:\n      Authorization: private-token-value\nskills:\n  disabled: []\n""",
            encoding="utf-8",
        )
        skill = self.profile / "skills" / "custom" / "research"
        (skill / "SKILL.md").write_text("# Research\n", encoding="utf-8")
        (skill / "references" / "guide.md").write_text("# Guide\n", encoding="utf-8")
        (skill / "scripts" / "collect.py").write_text("print('ok')\n", encoding="utf-8")
        (skill / "assets" / "template.txt").write_text("Template\n", encoding="utf-8")
        (self.profile / "memories").mkdir()
        (self.profile / "memories" / "MEMORY.md").write_text("personal memory")
        (self.profile / "USER.md").write_text("private user")
        (self.profile / ".env").write_text("TOKEN=private-token-value")
        (self.profile / "state.db").write_bytes(b"conversation history")
        (self.profile / "workspace" / "customer.txt").write_text("private workspace")
        (self.profile / "__pycache__").mkdir()
        (self.profile / "__pycache__" / "cached.pyc").write_bytes(b"cache")

    def tearDown(self):
        self.tmp.cleanup()

    def test_export_is_complete_allowlisted_typed_and_digest_bound(self):
        package = self.service.export("owned-agent", "MIT")

        self.assertEqual(package["schema_version"], 1)
        self.assertEqual(package["source_agent_id"], "owned-agent")
        self.assertEqual(package["digest"], self.service.digest(package))
        self.assertEqual(package["definition"]["soul"], "You research safely.\n")
        self.assertEqual(package["definition"]["prompts"], {"AGENTS.md": "Cite public sources.\n"})
        self.assertEqual(
            package["definition"]["skills"],
            {"custom/research": "# Research\n"},
        )
        self.assertEqual(package["definition"]["tool_requirements"], ["web"])
        self.assertEqual(package["definition"]["mcp_requirements"], ["docs"])
        self.assertEqual(package["definition"]["model_slots"], ["openai/gpt-safe"])
        self.assertEqual(package["definition"]["public_config"]["reasoning_effort"], "high")
        self.assertNotIn("providers", package["definition"]["public_config"])
        exported = json.dumps(package, sort_keys=True)
        for private in (
            "private-token-value",
            "private@example.test",
            "personal memory",
            "private user",
            "conversation history",
            "private workspace",
        ):
            self.assertNotIn(private, exported)
        self.assertEqual(
            sorted(package["definition"]["assets"]),
            [
                "skills/custom/research/assets/template.txt",
                "skills/custom/research/references/guide.md",
                "skills/custom/research/scripts/collect.py",
            ],
        )
        self.assertEqual(package["limits"]["file_count"], 6)
        self.assertGreaterEqual(package["exclusions"]["omitted_file_count"], 7)
        MarketplaceExportPackage.model_validate(package)

    def test_export_is_deterministic_and_changes_with_public_content(self):
        first = self.service.export("owned-agent", "MIT")
        second = self.service.export("owned-agent", "MIT")
        self.assertEqual(first, second)

        guide = self.profile / "skills" / "custom" / "research" / "references" / "guide.md"
        guide.write_text("# Changed guide\n", encoding="utf-8")
        changed = self.service.export("owned-agent", "MIT")
        self.assertNotEqual(changed["digest"], first["digest"])

    def test_export_rejects_profile_and_skill_symlink_attacks(self):
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_text("outside secret")
        link = self.profile / "skills" / "custom" / "research" / "references" / "escape.md"
        link.symlink_to(outside)
        with self.assertRaises(ServiceError) as error:
            self.service.export("owned-agent", "MIT")
        self.assertEqual(error.exception.code, "marketplace_export_rejected")
        link.unlink()

        profile_link = self.profiles / "linked-agent"
        profile_link.symlink_to(self.profile, target_is_directory=True)
        with self.assertRaises(ServiceError) as error:
            self.service.export("linked-agent", "MIT")
        self.assertEqual(error.exception.code, "marketplace_export_rejected")

    def test_export_rejects_secret_like_allowed_content(self):
        (self.profile / "SOUL.md").write_text(
            "api_key = sk-this-is-a-real-looking-secret\n", encoding="utf-8"
        )
        with self.assertRaises(ServiceError) as error:
            self.service.export("owned-agent", "MIT")
        self.assertEqual(error.exception.code, "marketplace_export_credentials_detected")

    def test_export_rejects_file_and_aggregate_limits(self):
        (self.profile / "SOUL.md").write_text("x" * (MAX_EXPORT_FILE_BYTES + 1), encoding="utf-8")
        with self.assertRaises(ServiceError) as error:
            self.service.export("owned-agent", "MIT")
        self.assertEqual(error.exception.status, 413)
        self.assertEqual(error.exception.code, "marketplace_export_too_large")

    def test_export_rejects_case_colliding_skill_paths(self):
        duplicate = self.profile / "skills" / "custom" / "Research"
        duplicate.mkdir()
        (duplicate / "SKILL.md").write_text("# Collision\n", encoding="utf-8")
        with self.assertRaises(ServiceError) as error:
            self.service.export("owned-agent", "MIT")
        self.assertEqual(error.exception.code, "marketplace_export_rejected")

    def test_export_rejects_missing_agent_and_incomplete_definition(self):
        with self.assertRaises(ServiceError) as missing:
            self.service.export("foreign-agent", "MIT")
        self.assertEqual(missing.exception.status, 404)

        (self.profile / "workspace" / "AGENTS.md").unlink()
        with self.assertRaises(ServiceError) as incomplete:
            self.service.export("owned-agent", "MIT")
        self.assertEqual(incomplete.exception.code, "marketplace_export_incomplete")


class APIFakeRouter:
    async def list_connections(self):
        return {"connections": []}

    async def list_models(self):
        return {"data": []}

    async def status(self):
        return {"available": True, "provider_count": 0}


class MarketplaceExportAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.root_profile = root / "root"
        self.profiles = root / "profiles"
        self.root_profile.mkdir()
        self.profiles.mkdir()
        (self.root_profile / "config.yaml").write_text(
            yaml.safe_dump({"model": {"default": "auto"}}), encoding="utf-8"
        )
        self.environment = patch.dict(
            os.environ,
            {
                "HERMES_HOME": str(self.root_profile),
                "HERMES_ROOT_PROFILE": str(self.root_profile),
                "HERMES_PROFILES_ROOT": str(self.profiles),
                "DATA_DIR": str(root / "data"),
                "RUNTIME_INCLUDE_PACKAGED_SKILLS": "false",
            },
        )
        self.environment.start()
        app = FastAPI()
        agents = AgentManager(
            root_profile=self.root_profile,
            profiles_root=self.profiles,
            legacy_agents_root=root / "legacy",
        )
        composition = XNOBrainApplication(
            agents, GlobalConfigManager(root_profile=self.root_profile), APIFakeRouter()
        )
        composition.register(app)
        self.app = app
        self.composition = composition

    def tearDown(self):
        self.environment.stop()
        self.tmp.cleanup()

    async def test_v1_export_route_uses_typed_envelope_and_selected_agent(self):
        created = self.composition.service.create_agent({"display_name": "Publisher"})
        profile = self.profiles / created["id"]
        (profile / "SOUL.md").write_text("Safe soul\n", encoding="utf-8")
        (profile / "workspace" / "AGENTS.md").write_text("Safe instructions\n", encoding="utf-8")

        async with AsyncClient(
            transport=ASGITransport(app=self.app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/xnobrain/api/runtime/v1/marketplace/agents/{created['id']}/export",
                json={"license": "MIT"},
            )
            malformed = await client.post(
                f"/xnobrain/api/runtime/v1/marketplace/agents/{created['id']}/export",
                json={"license": "MIT", "package": {"forged": True}},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["source_agent_id"], created["id"])
        self.assertRegex(response.json()["data"]["digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(malformed.status_code, 422, malformed.text)


class ExportAgents:
    def __init__(self, profiles):
        self.profiles_root = profiles
        self.legacy_agents_root = profiles.parent / "legacy"

    def profile_path(self, agent_id):
        path = self.profiles_root / agent_id
        if not path.is_dir():
            raise ValueError("not found")
        return path
