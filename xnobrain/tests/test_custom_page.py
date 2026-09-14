"""Actual SQLite plus signed-facade API evidence for private custom pages."""

from __future__ import annotations

import base64
import concurrent.futures
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from xnobrain.handlers import APIHandlers
from xnobrain.models.custom_page import PageManifest
from xnobrain.repositories import FileRepository, StoreError
from xnobrain.routes import setup_routes
from xnobrain.services.custom_page import CustomPageService
from xnobrain.trusted_context import TrustedRequestContext, principal_signature


def news_manifest():
    return {
        "schema_version": 1,
        "title": "News Monitor",
        "datasets": [
            {
                "id": "articles",
                "label": "Articles",
                "fields": [
                    {"id": "title", "label": "Title", "type": "string", "required": True},
                    {"id": "topic", "label": "Topic", "type": "string"},
                    {"id": "published_at", "label": "Published", "type": "datetime"},
                    {"id": "score", "label": "Score", "type": "number"},
                ],
            }
        ],
        "queries": [
            {
                "id": "latest",
                "dataset": "articles",
                "fields": ["title", "topic", "published_at"],
                "filters": ["topic"],
                "search": ["title"],
                "sort": "published_at",
                "descending": True,
            },
            {"id": "count", "dataset": "articles", "operation": "count"},
            {"id": "topics", "dataset": "articles", "operation": "group", "group_by": "topic"},
        ],
        "tabs": [
            {
                "id": "latest",
                "label": "Latest",
                "widgets": [
                    {"id": "records", "kind": "table", "title": "Articles", "query": "latest"},
                    {"id": "total", "kind": "card", "title": "Count", "query": "count"},
                    {"id": "chart", "kind": "chart", "title": "Topics", "query": "topics"},
                ],
            }
        ],
    }


class CustomPageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.files = FileRepository(self.root, self.root / "profiles")
        (self.root / "profiles" / "research").mkdir()
        (self.root / "profiles" / "other").mkdir()
        self.platform = SimpleNamespace(
            repository=self.files, conversation_runs=SimpleNamespace(_active={})
        )
        self.service = CustomPageService(self.platform)
        self.owner = TrustedRequestContext("owner", "tenant")

    def tearDown(self):
        self.temporary.cleanup()

    def prepare(self, manifest=None, expected=0, key="prepare-1"):
        return self.service.prepare(
            "research",
            {
                "manifest": manifest or news_manifest(),
                "expected_revision": expected,
                "idempotency_key": key,
            },
            self.owner,
        )

    def activate(self, draft, expected=0, **changes):
        return self.service.activate(
            "research",
            {
                "revision": draft["revision"],
                "digest": draft["digest"],
                "confirmation": "ACTIVATE " + draft["digest"],
                "expected_revision": expected,
                **changes,
            },
            self.owner,
        )

    def row(self, ident="article_1", title="News", topic="AI"):
        return {
            "id": ident,
            "values": {"title": title, "topic": topic, "published_at": "2026-09-13T12:00:00Z"},
            "provenance": {
                "kind": "source",
                "source": "https://example.test/news",
                "collected_at": "2026-09-13T12:05:00Z",
            },
        }

    def write(self, rows=None, **changes):
        return self.service.write(
            "research",
            "articles",
            {
                "expected_revision": 1,
                "idempotency_key": "write-001",
                "records": rows or [self.row()],
                **changes,
            },
            self.owner,
        )

    def query(self, query="latest", **parameters):
        return self.service.query("research", query, parameters, self.owner)

    def test_news_prepare_preview_activate_ingest_deduplicate_query_restart(self):
        draft = self.prepare()
        self.assertEqual(self.service.read("research", self.owner)["active"], 0)
        self.assertEqual(self.query(draft_revision=1)["rows"], [])
        with self.assertRaisesRegex(StoreError, "confirmation"):
            self.activate(draft, confirmation="model approved")
        self.activate(draft)
        self.write()
        self.write()
        self.assertEqual(self.query("count")["rows"], [{"count": 1}])
        self.assertEqual(self.query("topics")["rows"], [{"label": "AI", "count": 1}])
        self.assertEqual(
            self.query(search="NEWS", parameters={"topic": "AI"})["rows"][0]["id"], "article_1"
        )
        self.assertEqual(self.query(search="missing")["rows"], [])
        self.service = CustomPageService(self.platform)
        result = self.query()["rows"][0]
        self.assertEqual(result["values"]["title"], "News")
        self.assertEqual(result["provenance"]["kind"], "source")
        self.assertEqual(
            self.service.activity("research", self.owner)[0]["kind"], "records_written"
        )
        self.assertFalse((self.root / "profiles" / "research" / "state.db").exists())

    def test_reads_bind_exact_revision_and_reject_cancelled_previews(self):
        first = self.prepare()
        self.activate(first)
        self.write()
        self.assertEqual(self.query(expected_revision=1)["revision"], 1)
        next_page = news_manifest()
        next_page["title"] = "Updated news"
        second = self.prepare(next_page, expected=1, key="draft-next")
        self.activate(second, expected=1)
        with self.assertRaisesRegex(StoreError, "revision conflict"):
            self.query(expected_revision=1)
        self.assertEqual(self.query(expected_revision=2)["revision"], 2)
        self.assertEqual(self.query(draft_revision=1, expected_revision=1)["revision"], 1)
        third = self.prepare(expected=2, key="draft-cancelled")
        self.service.cancel("research", third["revision"], self.owner)
        with self.assertRaisesRegex(StoreError, "draft cancelled"):
            self.query(draft_revision=third["revision"])

    def test_ingest_before_first_activation_is_durable_and_deduplicated(self):
        draft = self.prepare()
        self.write()
        self.write()
        self.assertEqual(self.service.read("research", self.owner)["active"], 0)
        self.assertEqual(self.query(draft_revision=1)["rows"][0]["id"], "article_1")
        self.activate(draft)
        self.assertEqual(self.query("count")["rows"], [{"count": 1}])
        self.service = CustomPageService(self.platform)
        self.assertEqual(self.query()["rows"][0]["provenance"]["kind"], "source")

    def test_first_activation_cannot_hide_incompatible_draft_data(self):
        self.prepare()
        self.write()
        replacement = news_manifest()
        replacement["datasets"][0]["fields"][3]["type"] = "string"
        replacement["datasets"][0]["fields"][3]["required"] = True
        draft = self.prepare(replacement, key="changed-draft-schema")
        with self.assertRaises(StoreError):
            self.activate(draft)
        self.assertEqual(self.service.read("research", self.owner)["active"], 0)
        self.assertEqual(self.query(draft_revision=1)["rows"][0]["id"], "article_1")
        with self.assertRaisesRegex(StoreError, "not active"):
            self.service.activate(
                "research",
                {
                    "revision": draft["revision"],
                    "digest": draft["digest"],
                    "expected_revision": 0,
                    "confirmation": "RESTORE " + draft["digest"],
                },
                self.owner,
                restore=True,
            )

    def test_group_queries_honor_declared_domain_order(self):
        manifest = news_manifest()
        manifest["queries"][2].update(sort="topic", descending=False)
        self.activate(self.prepare(manifest))
        self.write(
            [self.row("one", topic="Z"), self.row("two", topic="Z"), self.row("three", topic="A")]
        )
        self.assertEqual(
            self.query("topics")["rows"],
            [
                {"label": "A", "count": 1},
                {"label": "Z", "count": 2},
            ],
        )

    def test_update_manifest_includes_app_and_maintenance_blocks_mutations(self):
        from unittest.mock import Mock

        from xnobrain.integrations.runtime_update_storage import RuntimeUpdateStorage
        from xnobrain.services.base import ServiceError

        self.activate(self.prepare())
        self.write()
        self.service.backup("research", self.owner)
        storage = RuntimeUpdateStorage(
            self.root, self.files.profiles_root, self.root / "root-profile"
        )
        checkpoints = storage.flush_sqlite()
        self.assertIn("data/agent-apps/research/app.sqlite3", checkpoints)
        self.assertIn("data/agent-apps/research/backup.sqlite3", checkpoints)
        before = storage.manifest()
        self.platform.runtime_updates = SimpleNamespace(
            require_dispatch=Mock(
                side_effect=ServiceError(
                    "maintenance", code="runtime_update_maintenance", status=503
                )
            )
        )
        with self.assertRaises(ServiceError):
            self.write(idempotency_key="during-maintenance")
        with self.assertRaises(ServiceError):
            self.service.backup("research", self.owner)
        self.service = CustomPageService(self.platform)
        self.assertEqual(self.query()["rows"][0]["values"]["title"], "News")
        self.assertEqual(storage.manifest()["digest"], before["digest"])

    def test_owner_org_and_agent_isolation_before_reads_or_writes(self):
        self.prepare()
        for trusted in [
            TrustedRequestContext(),
            TrustedRequestContext("foreign", "tenant"),
            TrustedRequestContext("owner", "other"),
            TrustedRequestContext("owner", "tenant", "organization"),
            TrustedRequestContext(
                "owner", "tenant", ownership_context={"owner_kind": "organization"}
            ),
        ]:
            with self.subTest(trusted=trusted), self.assertRaises(StoreError):
                self.service.read("research", trusted)
        with self.assertRaises(StoreError):
            self.service.read("other", self.owner)
        with self.assertRaises(StoreError):
            self.service.prepare("../outside", {}, self.owner)
        self.assertFalse((self.root / "agent-apps" / "other").exists())

    def test_manifest_rejects_executables_unregistered_bindings_duplicate_ids(self):
        for mutate in [
            lambda m: m.update(script="alert(1)"),
            lambda m: m["queries"][0].update(sql="ATTACH DATABASE '/other' AS x"),
            lambda m: m["tabs"][0]["widgets"][0].update(kind="javascript"),
            lambda m: m["queries"][0].update(filters=["unregistered"]),
            lambda m: m["datasets"][0]["fields"].append(m["datasets"][0]["fields"][0]),
        ]:
            manifest = news_manifest()
            mutate(manifest)
            with self.assertRaises(ValueError):
                PageManifest.model_validate(manifest)
        self.assertFalse((self.root / "agent-apps").exists())

    def test_query_injection_is_data_and_unknown_parameters_are_rejected(self):
        self.activate(self.prepare())
        self.write()
        self.assertEqual(self.query(parameters={"topic": "AI' OR 1=1--"})["rows"], [])
        for body in [
            {"parameters": {"sql": "select *"}},
            {"sql": "ATTACH DATABASE 'x' AS other"},
            {"limit": 1000},
            {"offset": -1},
        ]:
            with self.assertRaises(ValueError):
                self.query(**body)
        with self.assertRaises(StoreError):
            self.query("../other")
        self.assertEqual(len(self.query()["rows"]), 1)

    def test_batch_validation_quota_and_receipt_rollback_preserve_committed_rows(self):
        self.activate(self.prepare())
        self.write()
        with self.assertRaisesRegex(StoreError, "idempotency"):
            self.write([self.row(title="changed")])
        invalid = self.row("invalid")
        invalid["values"]["score"] = "not a number"
        with self.assertRaises(StoreError):
            self.write([self.row("valid"), invalid], idempotency_key="batch-invalid")
        with patch("xnobrain.repositories.custom_page.MAX_RECORDS", 1):
            with self.assertRaisesRegex(StoreError, "quota"):
                self.write([self.row("second")], idempotency_key="quota-001")
        self.assertEqual(self.query("count")["rows"][0]["count"], 1)
        self.write([self.row("second")], idempotency_key="quota-001")
        self.assertEqual(self.query(limit=1)["next_offset"], 1)
        self.assertEqual(len(self.query(offset=1)["rows"]), 1)

    def test_conflicting_drafts_destructive_schema_cancel_and_compatible_restore(self):
        first = self.prepare()
        self.activate(first)
        self.write()
        changed = news_manifest()
        changed["title"] = "New title"
        second = self.prepare(changed, expected=1, key="draft-002")
        third = self.prepare(changed, expected=1, key="draft-003")
        self.activate(second, expected=1)
        with self.assertRaisesRegex(StoreError, "revision conflict"):
            self.activate(third, expected=1)
        self.service.cancel("research", third["revision"], self.owner)
        with self.assertRaises(StoreError):
            self.activate(third, expected=2)
        incompatible = news_manifest()
        incompatible["datasets"][0]["fields"][3]["type"] = "string"
        fourth = self.prepare(incompatible, expected=2, key="draft-004")
        with self.assertRaisesRegex(StoreError, "migration"):
            self.activate(fourth, expected=2)
        self.assertEqual(self.query()["rows"][0]["values"]["title"], "News")
        self.service.activate(
            "research",
            {
                "expected_revision": 2,
                "revision": 1,
                "digest": first["digest"],
                "confirmation": "RESTORE " + first["digest"],
            },
            self.owner,
            restore=True,
        )
        self.assertEqual(len(self.query()["rows"]), 1)

    def test_backup_attachments_archive_and_symlink_missing_mount(self):
        self.activate(self.prepare())
        self.write()
        backup = self.service.backup("research", self.owner)
        self.assertGreater(backup["bytes"], 0)
        with sqlite3.connect(self.root / "agent-apps" / "research" / "backup.sqlite3") as db:
            self.assertEqual(db.execute("SELECT count(*) FROM records").fetchone()[0], 1)
        attachment = self.service.attachment(
            "research",
            {
                "filename": "data.txt",
                "content_base64": base64.b64encode(b"data").decode(),
                "idempotency_key": "attach-001",
            },
            self.owner,
        )
        self.assertTrue(
            self.service.read_attachment("research", attachment["id"], self.owner)["download_only"]
        )
        self.service.archive(
            "research", {"expected_revision": 1, "confirmation": "ARCHIVE research"}, self.owner
        )
        with self.assertRaises(StoreError):
            self.write(idempotency_key="after-archive")
        self.assertTrue(self.query()["archived"])
        with patch.dict(
            "os.environ", {"RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": str(self.root / "missing")}
        ):
            with self.assertRaises(StoreError):
                FileRepository(self.root, self.files.profiles_root)
        (self.root / "agent-apps" / "other").symlink_to(
            self.root / "agent-apps" / "research", target_is_directory=True
        )
        with self.assertRaises(StoreError):
            self.service.read("other", self.owner)

    def test_migration_explicit_plan_backup_and_interrupted_commit(self):
        self.activate(self.prepare())
        self.write()
        edited = news_manifest()
        edited["datasets"][0]["fields"] = edited["datasets"][0]["fields"][:-1]
        revision = self.prepare(edited, expected=1, key="drop-score")
        plan = self.service.migration_plan("research", revision["revision"], self.owner)
        body = {k: v for k, v in plan.items() if k != "affected_records"}
        with self.assertRaises(StoreError):
            self.service.migrate("research", {**body, "confirmation": "approved"}, self.owner)
        body["confirmation"] = "MIGRATE " + plan["plan_digest"]
        with patch.object(
            self.service.repository, "log", side_effect=RuntimeError("injected interruption")
        ):
            with self.assertRaises(RuntimeError):
                self.service.migrate("research", body, self.owner)
        self.assertEqual(self.service.read("research", self.owner)["active"], 1)
        self.assertEqual(len(self.query()["rows"]), 1)
        self.service.migrate("research", body, self.owner)
        self.service.migrate("research", body, self.owner)
        self.assertEqual(self.service.read("research", self.owner)["active"], 2)
        with sqlite3.connect(self.root / "agent-apps" / "research" / "backup.sqlite3") as db:
            self.assertEqual(db.execute("SELECT count(*) FROM records").fetchone()[0], 1)

    def test_additive_schema_restore_preserves_current_fields_and_records(self):
        first = self.prepare()
        self.activate(first)
        updated = news_manifest()
        updated["datasets"][0]["fields"].append(
            {"id": "summary", "type": "string", "label": "Summary"}
        )
        second = self.prepare(updated, expected=1, key="new-summary")
        self.activate(second, expected=1)
        record = self.row()
        record["values"]["summary"] = "Generated later, must survive page restore"
        record["provenance"].update(kind="generated", run_id="run-verified")
        self.write([record], expected_revision=2)
        restored = self.service.activate(
            "research",
            {
                "expected_revision": 2,
                "revision": 1,
                "digest": first["digest"],
                "confirmation": "RESTORE " + first["digest"],
            },
            self.owner,
            restore=True,
        )
        self.assertEqual(restored["revision"], 3)
        self.assertIn("summary", [f["id"] for f in restored["manifest"]["datasets"][0]["fields"]])
        exported = self.service.export("research", self.owner)
        self.assertEqual(exported["records"][0]["value"]["summary"], record["values"]["summary"])

    def test_parallel_matching_writes_and_conflicting_activation(self):
        first = self.prepare()
        self.activate(first)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.write(), range(8)))
        self.assertEqual(results, [results[0]] * 8)
        self.assertEqual(self.query("count")["rows"], [{"count": 1}])
        second = self.prepare(expected=1, key="parallel-second")
        third = self.prepare(expected=1, key="parallel-third")

        def activate(draft):
            try:
                self.activate(draft, expected=1)
                return True
            except StoreError:
                return False

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(activate, [second, third])), 1)

    def test_explicit_archive_delete_and_export_retention(self):
        self.activate(self.prepare())
        self.write()
        with self.assertRaisesRegex(StoreError, "archive required"):
            self.service.delete(
                "research", {"expected_revision": 1, "confirmation": "DELETE research"}, self.owner
            )
        self.platform.conversation_runs._active["run"] = SimpleNamespace(agent_id="research")
        with self.assertRaisesRegex(StoreError, "jobs active"):
            self.service.archive(
                "research", {"expected_revision": 1, "confirmation": "ARCHIVE research"}, self.owner
            )
        self.platform.conversation_runs._active.clear()
        self.service.archive(
            "research", {"expected_revision": 1, "confirmation": "ARCHIVE research"}, self.owner
        )
        self.assertEqual(len(self.service.export("research", self.owner)["records"]), 1)
        self.service.delete(
            "research", {"expected_revision": 1, "confirmation": "DELETE research"}, self.owner
        )
        self.assertFalse((self.root / "agent-apps" / "research").exists())
        self.assertTrue((self.root / "profiles" / "research").is_dir())

    def test_corrupted_manifest_and_profile_alias_fail_closed(self):
        self.activate(self.prepare())
        with sqlite3.connect(self.root / "agent-apps" / "research" / "app.sqlite3") as db:
            db.execute("UPDATE revisions SET manifest='{}'")
        with self.assertRaisesRegex(StoreError, "manifest unsupported"):
            self.query()
        alias = self.root / "profiles" / "alias"
        alias.symlink_to(self.root / "profiles" / "research", target_is_directory=True)
        with self.assertRaises(StoreError):
            self.service.read("alias", self.owner)

    def test_unknown_reference_attachment_and_invalid_datetime_roll_back(self):
        manifest = news_manifest()
        manifest["datasets"][0]["fields"].extend(
            [
                {"id": "previous", "label": "Previous", "type": "reference", "target": "articles"},
                {"id": "attachment", "label": "Attachment", "type": "attachment"},
            ]
        )
        self.activate(self.prepare(manifest))
        for key, value in [
            ("previous", "missing"),
            ("attachment", "att_missing"),
            ("published_at", "no timezone"),
        ]:
            row = self.row()
            row["values"][key] = value
            with self.assertRaises(StoreError):
                self.write([row])
        self.assertEqual(self.query("count")["rows"], [{"count": 0}])


class CustomPageAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_closed_routes_authentication_and_preview(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"RUNTIME_INTERNAL_SERVICE_TOKEN": "synthetic"}),
        ):
            files = FileRepository(directory, Path(directory) / "profiles")
            (files.profiles_root / "research").mkdir()
            platform = SimpleNamespace(repository=files)
            platform.custom_page = CustomPageService(platform)
            app = FastAPI()
            setup_routes(app, APIHandlers(platform))
            base = "/xnobrain/api/runtime/v1/agents/research/custom-page"
            headers = {
                "x-xnobrain-verified-subject": "owner",
                "x-xnobrain-principal-signature": principal_signature("synthetic", "owner"),
            }
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                caps = await client.get(base + "/capabilities", headers=headers)
                self.assertEqual(caps.status_code, 200)
                from xnobrain.routes.custom_page import ROUTES

                self.assertTrue(all(route.special is None for route in ROUTES))
                schema = app.openapi()
                capability_response = schema["paths"][
                    base.replace("research", "{agent_id}") + "/capabilities"
                ]["get"]["responses"]["200"]
                self.assertIn("PageCapabilities", str(capability_response))

                self.assertFalse(caps.json()["data"]["query_actions_paid"])
                self.assertEqual(caps.json()["data"]["max_action_model_turns"], 20)
                self.assertIn("action", caps.json()["data"]["operations"])
                self.assertFalse((Path(directory) / "agent-apps").exists())
                denied = await client.get(base)
                self.assertEqual(denied.status_code, 401)
                prepared = await client.post(
                    base + "/drafts",
                    headers=headers,
                    json={
                        "expected_revision": 0,
                        "idempotency_key": "draft-api-01",
                        "manifest": news_manifest(),
                    },
                )
                self.assertEqual(prepared.status_code, 201, prepared.text)
                read = await client.get(base + "/revisions/1", headers=headers)
                self.assertEqual(read.status_code, 200, read.text)
                self.assertEqual(read.json()["data"]["status"], "preview_ready")
                invalid = await client.post(
                    base + "/queries/latest", headers=headers, json={"sql": "select *"}
                )
                self.assertEqual(invalid.status_code, 422)
