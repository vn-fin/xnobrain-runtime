"""Selected-skill HTTP export: identity, isolation, bounds and no source mutation."""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from xnobrain.handlers.api import APIHandlers
from xnobrain.integrations import AgentManager
from xnobrain.repositories.files import FileRepository
from xnobrain.routes.setup import setup_routes
from xnobrain.services.portability import PortabilityService
from xnobrain.services.skill_exports import SkillExportService
from xnobrain.trusted_context import TrustedRequestContext, principal_signature

PATH = "/xnobrain/api/runtime/v1/skill-community/exports"
TOKEN = "synthetic-export-service-token"


class SkillExportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.profile = root / "profiles" / "writer"
        self.skill = self.profile / "skills" / "office" / "writing"
        self.skill.mkdir(parents=True)
        (self.skill / "SKILL.md").write_text("---\nname: writing\n---\n# Viết báo cáo\n")
        (self.skill / "references").mkdir()
        (self.skill / "references" / "guide.md").write_text("Reference only")
        other = self.profile / "skills" / "other"
        other.mkdir()
        (other / "SKILL.md").write_text("# Unselected skill")
        (self.profile / "SOUL.md").write_text("Private personality")
        self.repository = FileRepository(root / "data", root / "profiles")
        self.agents = AgentManager(
            root_profile=root / "default",
            profiles_root=root / "profiles",
            legacy_agents_root=root / "legacy",
        )
        self.service = SkillExportService(self.repository, self.agents)
        app = FastAPI()
        setup_routes(app, APIHandlers(SimpleNamespace(skill_exports=self.service)))
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://runtime")
        self.env = patch.dict(os.environ, {"RUNTIME_INTERNAL_SERVICE_TOKEN": TOKEN})
        self.env.start()

    async def asyncTearDown(self):
        await self.client.aclose()
        self.env.stop()
        self.temp.cleanup()

    async def call(self, *, reference=None, actor="owner", organization="", forged=False):
        reference = (
            reference
            if reference is not None
            else {
                "version": 1,
                "agent_id": "writer",
                "skill_path": "office/writing",
            }
        )
        headers = {
            "x-xnobrain-verified-subject": actor,
            "x-xnobrain-verified-tenant": "tenant",
            "x-xnobrain-verified-organization": organization,
            "x-xnobrain-principal-signature": "forged"
            if forged
            else principal_signature(TOKEN, actor, "tenant", organization),
        }
        return await self.client.post(
            PATH, headers=headers, json={"reference": json.dumps(reference)}
        )

    async def test_selected_skill_is_complete_deterministic_and_read_only(self):
        before = {
            path.relative_to(self.profile): path.read_bytes()
            for path in self.profile.rglob("*")
            if path.is_file()
        }
        response = await self.call()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        result = response.json()["data"]
        self.assertEqual(
            [file["path"] for file in result["files"]], ["SKILL.md", "references/guide.md"]
        )
        for file in result["files"]:
            payload = file["content"].encode()
            self.assertEqual(file["size"], len(payload))
            self.assertEqual(file["digest"], "sha256:" + hashlib.sha256(payload).hexdigest())
        self.assertEqual((await self.call()).json()["data"], result)
        self.assertEqual(
            before,
            {
                path.relative_to(self.profile): path.read_bytes()
                for path in self.profile.rglob("*")
                if path.is_file()
            },
        )

    async def test_unsigned_forged_and_organization_requests_fail_closed(self):
        self.assertEqual((await self.client.post(PATH, json={})).status_code, 403)
        self.assertEqual((await self.call(forged=True)).status_code, 403)
        self.assertEqual((await self.call(organization="org")).status_code, 403)

    async def test_imported_profile_must_match_recipient(self):
        self.repository.atomic_json(
            self.profile / ".community-profile-owner.json",
            PortabilityService.owner_record(TrustedRequestContext("owner", "tenant")),
        )
        self.assertEqual((await self.call(actor="other")).status_code, 403)
        self.assertEqual((await self.call()).status_code, 200)

    async def test_missing_skill_traversal_and_authority_fields_are_rejected(self):
        for path in ("../other", "/tmp", "office//writing", "office\\writing", "missing"):
            with self.subTest(path=path):
                response = await self.call(
                    reference={"version": 1, "agent_id": "writer", "skill_path": path}
                )
                self.assertIn(response.status_code, (404, 422))
        response = await self.call(
            reference={
                "version": 1,
                "agent_id": "writer",
                "skill_path": "office/writing",
                "tenant_id": "other",
            }
        )
        self.assertEqual(response.status_code, 422)

    async def test_private_files_are_excluded_without_reading(self):
        for name in (".env", "auth.json", "config.yaml", "MEMORY.md"):
            (self.skill / name).write_text("Private value")
        (self.skill / "sessions").mkdir()
        (self.skill / "sessions" / "private.txt").write_text("Private conversation")
        response = await self.call()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("Private", response.text)
        self.assertEqual(len(response.json()["data"]["excluded"]), 5)

    async def test_file_symlinks_hardlinks_and_directory_symlinks_are_rejected(self):
        target = self.profile / "SOUL.md"
        link = self.skill / "linked.txt"
        link.symlink_to(target)
        self.assertEqual((await self.call()).status_code, 422)
        link.unlink()
        os.link(target, link)
        self.assertEqual((await self.call()).status_code, 422)
        link.unlink()
        link.symlink_to(self.profile, target_is_directory=True)
        self.assertEqual((await self.call()).status_code, 422)

    async def test_skill_and_profile_symlinks_cannot_escape(self):
        outside = self.profile.parent / "outside"
        self.skill.rename(outside)
        self.skill.symlink_to(outside, target_is_directory=True)
        self.assertIn((await self.call()).status_code, (404, 422))
        self.skill.unlink()
        outside.rename(self.skill)
        moved = self.profile.parent / "moved"
        self.profile.rename(moved)
        self.profile.symlink_to(moved, target_is_directory=True)
        self.assertEqual((await self.call()).status_code, 422)

    async def test_oversize_non_text_and_credential_content_are_rejected(self):
        file = self.skill / "extra.txt"
        for payload, status, code in (
            (b"x" * (1024 * 1024 + 1), 413, "limit_exceeded"),
            (b"\xff", 422, "skill_export_text_only"),
            (b"-----BEGIN PRIVATE KEY-----", 422, "marketplace_export_credentials_detected"),
        ):
            file.write_bytes(payload)
            response = await self.call()
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.json()["error"]["code"], code)

    async def test_reserved_control_skill_is_not_exportable(self):
        reserved = self.profile / "skills" / "big-brother" / "hidden"
        reserved.mkdir(parents=True)
        (reserved / "SKILL.md").write_text("# Internal")
        response = await self.call(
            reference={"version": 1, "agent_id": "writer", "skill_path": "big-brother/hidden"}
        )
        self.assertEqual(response.status_code, 404)

    async def test_known_profile_credential_copied_into_skill_is_rejected(self):
        (self.profile / "config.yaml").write_text("api_key: fixture-personal-credential\n")
        (self.skill / "references" / "guide.md").write_text("Use fixture-personal-credential")
        response = await self.call()
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("fixture-personal-credential", response.text)

    async def test_file_count_and_total_bytes_are_bounded(self):
        for index in range(199):
            (self.skill / f"file-{index}.txt").write_text("small")
        self.assertEqual((await self.call()).status_code, 413)
        for file in self.skill.glob("file-*.txt"):
            file.unlink()
        for index in range(10):
            (self.skill / f"file-{index}.txt").write_bytes(b"x" * (1024 * 1024))
        self.assertEqual((await self.call()).status_code, 413)

    async def test_root_and_legacy_agents_use_their_own_skill_directories(self):
        for agent, profile in (
            ("big-brother", self.agents.root_profile),
            ("legacy-writer", self.agents.legacy_agents_root / "legacy-writer" / ".profile"),
        ):
            directory = profile / "skills" / "local"
            directory.mkdir(parents=True)
            (directory / "SKILL.md").write_text("---\nname: local\n---\n# Local")
            response = await self.call(
                reference={"version": 1, "agent_id": agent, "skill_path": "local"}
            )
            self.assertEqual(response.status_code, 200, response.text)
