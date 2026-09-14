"""Disposable volume/container replacement verification; no production update."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from xnobrain.models.custom_page import digest
from xnobrain.repositories import FileRepository, StoreError
from xnobrain.services.custom_page import CustomPageService
from xnobrain.services.runtime_updates import RuntimeUpdateService
from xnobrain.tests.test_custom_page import news_manifest
from xnobrain.tests.test_runtime_updates import TARGET, FakeConnector, request
from xnobrain.trusted_context import TrustedRequestContext

TOKEN = "synthetic-volume-update-token"
OWNER = TrustedRequestContext("volume-owner", "volume-tenant")


def services():
    data = Path(os.environ.get("DATA_DIR", "/opt/data/xnobrain"))
    files = FileRepository(data, data / "profiles", root_profile=data / "root-profile")
    platform = SimpleNamespace(
        repository=files,
        config=SimpleNamespace(root_profile=data / "root-profile"),
        organization_connector=FakeConnector(),
        conversation_runs=SimpleNamespace(_active={}, shutdown=AsyncMock()),
        team_runs=SimpleNamespace(_active={}, shutdown=AsyncMock()),
        kanban=SimpleNamespace(
            active_agent_ids=Mock(return_value=set()), cancel_active_tasks_for_update=Mock()
        ),
        cron=SimpleNamespace(active_execution_count=0),
    )
    pages = CustomPageService(platform)
    updates = RuntimeUpdateService(platform)
    platform.runtime_updates = updates
    return files, pages, updates


def environment():
    os.environ.update(
        {
            "RUNTIME_UPDATE_SERVICE_TOKEN": TOKEN,
            "RUNTIME_UPDATE_DATA_PATH": "/opt/data",
            # Simulated installed identity for source lifecycle verification, not
            # evidence that any production image/version has been installed.
            "XNOBRAIN_VERSION": TARGET["version"],
            "XNOBRAIN_SOURCE_COMMIT": TARGET["source_commit"],
            "XNOBRAIN_RUNTIME_DIGEST": TARGET["runtime_digest"],
            "RUNTIME_DATA_SCHEMA": "1",
        }
    )


async def run(mode):
    environment()
    data = Path(os.environ.get("DATA_DIR", "/opt/data/xnobrain"))
    if mode == "missing":
        existed = data.exists()
        try:
            services()
        except StoreError as error:
            assert error.code == "custom_page_storage_unavailable"
            assert data.exists() == existed
            print(
                "PASS rejected missing/unrelated/nonpersistent mount before fallback allocation",
                flush=True,
            )
            return
        raise AssertionError("unverified mount accepted")
    files, pages, updates = services()
    evidence = files.data_dir / "runtime-updates" / "volume-verification.json"
    if mode == "seed":
        files.live_profile_path("research").mkdir()
        (files.data_dir / "root-profile").mkdir()
        draft = pages.prepare(
            "research",
            {
                "manifest": news_manifest(),
                "expected_revision": 0,
                "idempotency_key": "volume-prepare",
            },
            OWNER,
        )
        pages.activate(
            "research",
            {
                "expected_revision": 0,
                "revision": 1,
                "digest": draft["digest"],
                "confirmation": "ACTIVATE " + draft["digest"],
            },
            OWNER,
        )
        pages.write(
            "research",
            "articles",
            {
                "expected_revision": 1,
                "idempotency_key": "volume-write",
                "records": [
                    {
                        "id": "stored-one",
                        "values": {"title": "Synthetic durable record", "topic": "AI"},
                        "provenance": {
                            "kind": "synthetic",
                            "source": "Disposable volume verification",
                            "collected_at": "2026-09-14T00:00:00Z",
                        },
                    }
                ],
            },
            OWNER,
        )
        attachment = pages.attachment(
            "research",
            {
                "filename": "source.txt",
                "content_base64": base64.b64encode(b"synthetic source bytes").decode(),
                "idempotency_key": "volume-attachment",
            },
            OWNER,
        )
        pages.backup("research", OWNER)
        exported = pages.export("research", OWNER)
        files.atomic_json(
            evidence, {"export_digest": digest(exported), "attachment": attachment["id"]}
        )
        print(
            "PASS seed: active revision, stored row, attachment, consistent backup on verified volume",
            flush=True,
        )
    elif mode == "checkpoint":
        saved = json.loads(evidence.read_text())
        assert digest(pages.export("research", OWNER)) == saved["export_digest"]
        assert (
            pages.read_attachment("research", saved["attachment"], OWNER)["content_base64"]
            == base64.b64encode(b"synthetic source bytes").decode()
        )
        assert updates.storage.layout("/opt/data")["separate_mount"]
        await updates.drain(request(deadline_seconds=0), update_token=TOKEN)
        checkpoint = await updates.checkpoint(request(), update_token=TOKEN)
        assert "data/agent-apps/research/app.sqlite3" in checkpoint["sqlite_checkpoints"]
        assert "data/agent-apps/research/backup.sqlite3" in checkpoint["sqlite_checkpoints"]
        files.atomic_json(evidence, {**saved, "checkpoint_id": checkpoint["checkpoint_id"]})
        print(
            "PASS replacement reader: exact export/attachment; real drain/checkpoint captured app+backup",
            flush=True,
        )
    elif mode == "verify":
        saved = json.loads(evidence.read_text())
        assert updates.dispatch_paused
        result = await updates.post_verify(
            request(checkpoint_id=saved["checkpoint_id"]), update_token=TOKEN
        )
        assert result["verified"] and result["data_preserved"]
        assert digest(pages.export("research", OWNER)) == saved["export_digest"]
        await updates.resume(request(), update_token=TOKEN)
        assert not updates.dispatch_paused
        print(
            "PASS replacement post-verify: preserved manifest/data; simulated identity accepted; resumed",
            flush=True,
        )
    elif mode == "crash":
        with pages.repository.database("research", "volume-tenant\0volume-owner", write=True) as db:
            db.execute(
                "UPDATE records SET value=? WHERE id='stored-one'",
                ('{"title":"uncommitted change"}',),
            )
            print("Intentional crash with uncommitted transaction; expect rollback", flush=True)
            os._exit(73)
    elif mode == "recover":
        saved = json.loads(evidence.read_text())
        assert digest(pages.export("research", OWNER)) == saved["export_digest"]
        assert pages.query("research", "count", {"expected_revision": 1}, OWNER)["rows"] == [
            {"count": 1}
        ]
        print(
            "PASS replacement after crash: uncommitted data absent; prior app/export intact",
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode", choices=["seed", "checkpoint", "verify", "crash", "recover", "missing"]
    )
    asyncio.run(run(parser.parse_args().mode))
