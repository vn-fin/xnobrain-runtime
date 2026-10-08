"""Two-recipient signed HTTP admission, receipts and disabled installation."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from xnobrain.handlers.api import APIHandlers
from xnobrain.repositories.files import FileRepository
from xnobrain.services.portability import PortabilityService
from xnobrain.services.skill_community import SkillCommunityService
from xnobrain.trusted_context import TrustedRequestContext, principal_signature, snapshot_signature

TOKEN = "synthetic-community-service-token"
PATH = "/xnobrain/api/runtime/v1/skill-community/installations"


class SkillCommunityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.repository = FileRepository(root / "data", root / "profiles")
        self.service = SkillCommunityService(self.repository)
        self.actor = TrustedRequestContext("consumer", "tenant", "")
        profile = self.repository.profile_path("profile")
        profile.mkdir()
        self.repository.atomic_json(
            profile / ".community-profile-owner.json", PortabilityService.owner_record(self.actor)
        )
        text = "---\nname: original-name\ndescription: Example\n---\n# Example"
        self.payload = {
            "operation_id": "ski_operation1",
            "candidate_id": "skc_candidate",
            "use_grant_id": "skg_grant",
            "skill_id": "example",
            "version": "1.0.0",
            "digest": "sha256:" + "a" * 64,
            "files": [
                {
                    "path": "SKILL.md",
                    "content": text,
                    "encoding": "utf8",
                    "size": len(text),
                    "digest": "sha256:" + hashlib.sha256(text.encode()).hexdigest(),
                }
            ],
            "target_profile_id": "profile",
            "collision_resolution": "fail",
            "accepted_permissions": [],
            "enable": False,
        }
        handlers = APIHandlers(SimpleNamespace(skill_community=self.service))
        app = FastAPI()

        @app.post(PATH)
        @app.get(PATH + "/{operation_id}/receipt")
        async def endpoint(request: Request):
            return await handlers.community_skill(request)

        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://runtime")
        self.env = patch.dict("os.environ", {"RUNTIME_INTERNAL_SERVICE_TOKEN": TOKEN})
        self.env.start()

    async def asyncTearDown(self):
        self.env.stop()
        await self.client.aclose()
        self.temp.cleanup()

    async def call(self, *, actor="consumer", forged=False, payload=None, receipt=False):
        payload = self.payload if payload is None else payload
        body = b"" if receipt else json.dumps(payload).encode()
        method = "GET" if receipt else "POST"
        path = PATH + "/ski_operation1/receipt" if receipt else PATH
        digest = hashlib.sha256(body).hexdigest()
        signature = snapshot_signature(
            TOKEN, method, path, actor, "tenant", "", digest, str(len(body)), "ski_operation1"
        )
        headers = {
            "x-xnobrain-verified-subject": actor,
            "x-xnobrain-verified-tenant": "tenant",
            "x-xnobrain-principal-signature": principal_signature(TOKEN, actor, "tenant"),
            "x-xnobrain-community-snapshot-signature": "forged" if forged else signature,
            "x-xnobrain-community-snapshot-sha256": digest,
            "x-xnobrain-community-snapshot-size": str(len(body)),
            "x-xnobrain-community-snapshot-idempotency-key": "ski_operation1",
            "content-type": "application/json",
        }
        return await self.client.request(method, path, headers=headers, content=body)

    async def test_forged_intent_and_foreign_profile_are_denied(self):
        self.assertEqual((await self.call(forged=True)).status_code, 403)
        self.assertEqual((await self.call(actor="other")).status_code, 403)
        self.assertFalse((self.repository.profile_path("profile") / "skills").exists())

    async def test_disabled_install_idempotent_replay_and_receipt_recovery(self):
        first = await self.call()
        self.assertEqual(first.status_code, 201, first.text)
        profile = self.repository.profile_path("profile")
        disabled = yaml.safe_load((profile / "config.yaml").read_text())["skills"]["disabled"]
        self.assertIn("original-name", disabled)
        self.assertIn("example", disabled)
        self.assertEqual((await self.call()).json(), first.json())
        # Crash after target rename and before the central receipt: GET finds
        # the staged marker and never imports or overwrites consumer edits.
        root = self.service._paths(self.actor, "ski_operation1")
        (root / "receipt.json").unlink()
        skill = profile / "skills/example/SKILL.md"
        skill.write_text("consumer edits")
        receipt = await self.call(receipt=True)
        self.assertEqual(receipt.status_code, 200, receipt.text)
        self.assertEqual(receipt.json()["data"], first.json()["data"])
        self.assertEqual((await self.call(actor="other", receipt=True)).status_code, 404)
        self.assertEqual((await self.call()).status_code, 201)
        self.assertEqual(skill.read_text(), "consumer edits")
        self.assertEqual(len(list((profile / "skills").iterdir())), 1)

    async def test_changed_operation_traversal_checksum_and_enable_are_rejected(self):
        self.assertEqual((await self.call()).status_code, 201)
        self.assertEqual(
            (await self.call(payload={**self.payload, "version": "1.0.1"})).status_code, 409
        )
        self.assertEqual(
            (await self.call(payload={**self.payload, "enable": True})).status_code, 400
        )
        for change in ({"path": "../escape"}, {"digest": "sha256:" + "b" * 64}):
            with self.assertRaises(ValueError):
                self.service._files([{**self.payload["files"][0], **change}])


class LegacyCommunityHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_signed_install_receipt_and_foreign_recipient(self):
        from xnobrain.handlers.community_legacy import deliver
        from xnobrain.services.marketplace import MarketplaceService

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = FileRepository(root / "data", root / "profiles")
            service = MarketplaceService(repo, SimpleNamespace(sync_profiles_registry=lambda: None))
            handler = APIHandlers(SimpleNamespace(marketplace=service, repository=repo))
            app = FastAPI()
            install_path = "/xnobrain/api/runtime/v1/marketplace/install"
            receipt_path = "/xnobrain/api/runtime/v1/marketplace/installations/inst_test/receipt"

            @app.post(install_path)
            async def install(request: Request):
                from xnobrain.repositories.base import StoreError
                from xnobrain.services.base import ServiceError

                try:
                    return handler.success(
                        await deliver(handler, request, await request.json(), "install")
                    )
                except (ServiceError, StoreError) as error:
                    return handler.failure(error)

            @app.get(
                "/xnobrain/api/runtime/v1/marketplace/installations/{operation_id}/receipt",
                name="community_legacy_receipt",
            )
            async def receipt(request: Request):
                return await handler.community_skill(request)

            package = {
                "id": "inst_test",
                "status": "pending",
                "update_policy": "pinned",
                "definition": {"soul": "Private soul"},
                "requested_permissions": [],
                "compatibility": {},
                "license": "MIT",
            }
            package["digest"] = service.digest(package)
            body = json.dumps({"package": package}).encode()
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://runtime"
            ) as client:

                async def call(actor="consumer", forged=False, recovery=False):
                    method, path = ("GET", receipt_path) if recovery else ("POST", install_path)
                    digest = (
                        package["digest"].removeprefix("sha256:")
                        if recovery
                        else hashlib.sha256(body).hexdigest()
                    )
                    size = "0" if recovery else str(len(body))
                    headers = {
                        "x-xnobrain-verified-subject": actor,
                        "x-xnobrain-verified-tenant": "tenant",
                        "x-xnobrain-principal-signature": principal_signature(
                            TOKEN, actor, "tenant"
                        ),
                        "x-xnobrain-community-snapshot-signature": "forged"
                        if forged
                        else snapshot_signature(
                            TOKEN, method, path, actor, "tenant", "", digest, size, "inst_test"
                        ),
                        "x-xnobrain-community-snapshot-sha256": digest,
                        "x-xnobrain-community-snapshot-size": size,
                        "x-xnobrain-community-snapshot-idempotency-key": "inst_test",
                    }
                    return await client.request(
                        method, path, content=b"" if recovery else body, headers=headers
                    )

                with patch.dict("os.environ", {"RUNTIME_INTERNAL_SERVICE_TOKEN": TOKEN}):
                    self.assertEqual((await call(forged=True)).status_code, 403)
                    result = await call()
                    self.assertEqual(result.status_code, 200, result.text)
                    soul = repo.profile_path("market-test") / "SOUL.md"
                    soul.write_text("consumer edit")
                    recovered = await call(recovery=True)
                    self.assertEqual(recovered.status_code, 200, recovered.text)
                    self.assertEqual(recovered.json()["data"], result.json()["data"])
                    self.assertNotIn("Private soul", recovered.text)
                    self.assertEqual((await call(actor="other", recovery=True)).status_code, 404)
                    self.assertEqual((await call()).status_code, 200)
                    self.assertEqual(soul.read_text(), "consumer edit")
