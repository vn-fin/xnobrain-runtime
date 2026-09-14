"""Deterministic news journey through signed HTTP, native tools, runs and SQLite.

The model is a scripted fixture, not a paid LLM or claim of live news collection.
All resulting data is genuinely persisted/read by the same production contracts.
"""

from __future__ import annotations

import asyncio
import base64
import json
import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from xnobrain.handlers import APIHandlers
from xnobrain.integrations.custom_page_tools import bind_run, install_tools
from xnobrain.repositories import FileRepository
from xnobrain.routes import setup_routes
from xnobrain.services.conversation_runs import ConversationRunService
from xnobrain.services.custom_page import CustomPageService
from xnobrain.tests.test_conversation_runs import FakeAnalytics, sse
from xnobrain.tests.test_custom_page import news_manifest
from xnobrain.tests.test_custom_page_actions import PagePlatform
from xnobrain.trusted_context import TrustedRequestContext, principal_signature


def journey_manifest():
    manifest = news_manifest()
    manifest["description"] = (
        "Synthetic news corpus and deterministic mock analysis for verification."
    )
    manifest["datasets"].append(
        {
            "id": "summaries",
            "label": "Stored summaries",
            "fields": [
                {"id": "article", "label": "Article", "type": "reference", "target": "articles"},
                {"id": "analysis", "label": "Analysis", "type": "string", "required": True},
            ],
        }
    )
    manifest["queries"].append(
        {"id": "summary", "dataset": "summaries", "fields": ["analysis", "article"]}
    )
    manifest["tabs"].append(
        {
            "id": "summary",
            "label": "Summary",
            "widgets": [
                {
                    "id": "analysis",
                    "kind": "markdown",
                    "query": "summary",
                    "text_field": "analysis",
                    "title": "Stored analysis",
                }
            ],
        }
    )
    manifest["actions"] = [
        {
            "id": "analyze",
            "kind": "analyze",
            "label": "Analyze stored news",
            "instruction": "Analyze only this app's retained synthetic news corpus.",
            "datasets": ["articles", "summaries"],
        }
    ]
    return manifest


def read_from_new_process(root, channel):
    """No shared service cache, run registry or Python DB connection."""
    files = FileRepository(root, Path(root) / "profiles")
    pages = CustomPageService(SimpleNamespace(repository=files))
    owner = TrustedRequestContext("owner", "tenant")
    channel.send(
        {
            "summary": pages.query("research", "summary", {"expected_revision": 1}, owner),
            "export": pages.export("research", owner),
        }
    )
    channel.close()


class ScriptedNewsAgent:
    def __init__(self):
        self.calls = 0
        self.platform = None
        self.prepared = None
        self.errors = []

    def get_conversation(self, agent, conversation):
        return {"conversation": {"id": conversation, "agent_id": agent}, "messages": []}

    async def chat_stream(self, agent, body):
        self.calls += 1
        run_id = body["run_id"]
        yield sse({"event": "run.started", "run_id": run_id})
        try:
            await asyncio.to_thread(self.execute, agent, body)
            yield sse(
                {
                    "event": "run.completed",
                    "run_id": run_id,
                    "output": "Stored synthetic fixture result",
                    "usage": {"total_tokens": 0},
                }
            )
        except Exception as error:
            self.errors.append(type(error).__name__)
            yield sse(
                {"event": "run.failed", "run_id": run_id, "message": "Fixture execution failed"}
            )

    def execute(self, agent, body):
        from tools.registry import registry

        pages = self.platform.custom_page
        with bind_run(pages, agent, "session", body["run_id"], body["_custom_page_principal"]):
            model = SimpleNamespace(tools=[], valid_tool_names=set())
            install_tools(model, "session")

            def tool(name, args):
                assert name in model.valid_tool_names
                response = json.loads(registry.dispatch(name, args, session_id="session"))
                assert response.get("success"), response
                return response["data"]

            inspected = tool("custom_page_inspect", {})
            if "custom_page" in body.get("capabilities", []):
                assert inspected["page"] is None
                self.prepared = tool(
                    "custom_page_prepare",
                    {
                        "manifest": journey_manifest(),
                        "expected_revision": 0,
                        "idempotency_key": "prepare-synthetic-news",
                    },
                )
                rows = [
                    {
                        "id": f"article-{index}",
                        "values": {
                            "title": title,
                            "topic": topic,
                            "published_at": "2026-09-14T00:00:00Z",
                        },
                        "provenance": {
                            "kind": "synthetic",
                            "source": "Synthetic local verification corpus, not live news",
                            "collected_at": "2026-09-14T00:01:00Z",
                        },
                    }
                    for index, (title, topic) in enumerate(
                        [
                            ("Synthetic research launch", "AI"),
                            ("Synthetic model update", "AI"),
                            ("Synthetic energy report", "Energy"),
                        ]
                    )
                ]
                payload = {
                    "dataset_id": "articles",
                    "expected_revision": 1,
                    "idempotency_key": "ingest-synthetic-news",
                    "records": rows,
                }
                first = tool("custom_page_write", payload)
                assert tool("custom_page_write", payload) == first
                return
            assert model.max_iterations == 20
            assert "custom_page_prepare" not in model.valid_tool_names
            articles = tool("custom_page_query", {"query_id": "latest", "expected_revision": 1})
            assert len(articles["rows"]) == 3
            tool(
                "custom_page_write",
                {
                    "dataset_id": "summaries",
                    "expected_revision": 1,
                    "idempotency_key": "analyze-synthetic-news",
                    "records": [
                        {
                            "id": "daily",
                            "values": {
                                "article": "article-0",
                                "analysis": "## Synthetic analysis\n3 fixture articles: 2 AI and 1 Energy.\nDeterministic mock model output, not live analysis.",
                            },
                            "provenance": {
                                "kind": "generated",
                                "source": "Synthetic article-0, article-1, article-2",
                                "run_id": "model-cannot-forge-this",
                                "collected_at": "2026-09-14T01:00:00Z",
                            },
                        }
                    ],
                },
            )


class StoredNewsJourneyTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_news_prepare_ingest_analyze_activate_search_backup_restart(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(
                os.environ,
                {
                    "RUNTIME_INTERNAL_SERVICE_TOKEN": "synthetic-secret",
                    "RUNTIME_ACCOUNTING_MODE": "legacy",
                },
            ),
        ):
            root = Path(temporary)
            files = FileRepository(root, root / "profiles")
            (files.profiles_root / "research").mkdir()
            agent, analytics = ScriptedNewsAgent(), FakeAnalytics()
            runs = ConversationRunService(files, agent, analytics)
            platform = PagePlatform()
            platform.repository, platform.agents, platform.conversation_runs = files, agent, runs
            platform.custom_page = CustomPageService(platform)
            agent.platform = platform
            trusted = TrustedRequestContext("owner", "tenant")
            files.create_conversation_context(
                files.profile_path("research"),
                {
                    **platform._personal_context(),
                    "agent_id": "research",
                    "conversation_id": "session",
                    "actor_user_id": "owner",
                    "actor_tenant_id": "tenant",
                },
            )
            headers = {
                "x-xnobrain-verified-subject": "owner",
                "x-xnobrain-tenant-id": "tenant",
                "x-xnobrain-principal-signature": principal_signature(
                    "synthetic-secret", "owner", "tenant"
                ),
            }
            # Use canonical verified-tenant header from the production verifier.
            from xnobrain.trusted_context import TRUSTED_TENANT_HEADER

            headers.pop("x-xnobrain-tenant-id")
            headers[TRUSTED_TENANT_HEADER] = "tenant"
            app = FastAPI()
            setup_routes(app, APIHandlers(platform))
            base = "/xnobrain/api/runtime/v1/agents/research/custom-page"
            fixtures = {}
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test", headers=headers
                ) as client:
                    caps = await client.get(base + "/capabilities")
                    self.assertEqual(caps.status_code, 200, caps.text)
                    self.assertIn("prepare", caps.json()["data"]["operations"])
                    self.assertFalse((root / "agent-apps").exists())
                    first = await platform.start_conversation_run(
                        "research",
                        "session",
                        {
                            "input": "Prepare a page for this synthetic local news fixture",
                            "capabilities": ["todo", "custom_page"],
                            "idempotency_key": "news-prepare-once",
                        },
                        trusted,
                    )
                    await runs._active[first["id"]].task
                    await asyncio.sleep(0)
                    self.assertEqual(
                        runs.get_run("research", "session", first["id"])["status"],
                        "completed",
                        agent.errors,
                    )
                    prepared = agent.prepared
                    self.assertTrue(prepared["approval_required"])
                    self.assertEqual(prepared["preview_path"], "/agents/research/custom?draft=1")
                    state = (await client.get(base)).json()["data"]
                    self.assertEqual(state["active"], 0)
                    self.assertEqual(agent.calls, 1)
                    draft = (await client.get(base + "/revisions/1")).json()["data"]
                    preview_rows = (
                        await client.post(
                            base + "/queries/latest",
                            json={"draft_revision": 1, "expected_revision": 1},
                        )
                    ).json()["data"]
                    self.assertEqual(len(preview_rows["rows"]), 3)
                    self.assertTrue(
                        all(
                            row["provenance"]["kind"] == "synthetic" for row in preview_rows["rows"]
                        )
                    )
                    denied = await client.post(
                        base + "/activate",
                        json={
                            "revision": 1,
                            "expected_revision": 0,
                            "digest": draft["digest"],
                            "confirmation": "chat said approve",
                        },
                    )
                    self.assertEqual(denied.status_code, 403)
                    activation = {
                        "revision": 1,
                        "expected_revision": 0,
                        "digest": draft["digest"],
                        "confirmation": "ACTIVATE " + draft["digest"],
                    }
                    activated = await client.post(base + "/activate", json=activation)
                    self.assertEqual(activated.status_code, 200, activated.text)
                    self.assertEqual(
                        (await client.post(base + "/activate", json=activation)).json()["data"],
                        activated.json()["data"],
                    )
                    self.assertEqual(agent.calls, 1)  # Activation never analyzes.
                    action = {
                        "expected_revision": 1,
                        "conversation_id": "session",
                        "idempotency_key": "news-analysis-once",
                        "timeout_seconds": 30,
                        "confirmation": "RUN analyze",
                    }
                    action_response = await client.post(base + "/actions/analyze", json=action)
                    self.assertEqual(action_response.status_code, 202, action_response.text)
                    accepted = action_response.json()["data"]
                    entry = runs._active.get(accepted["id"])
                    if entry is not None:
                        await entry.task
                    await asyncio.sleep(0)
                    self.assertEqual(
                        runs.get_run("research", "session", accepted["id"])["status"],
                        "completed",
                        agent.errors,
                    )
                    replay = await client.post(base + "/actions/analyze", json=action)
                    self.assertEqual(replay.json()["data"]["id"], accepted["id"])
                    self.assertEqual(agent.calls, 2)
                    self.assertEqual(analytics.calls, ["research", "research"])
                    for query, query_input in [
                        ("latest", {}),
                        ("count", {}),
                        ("topics", {}),
                        ("summary", {}),
                        ("latest", {"search": "research", "parameters": {"topic": "AI"}}),
                    ]:
                        response = await client.post(
                            base + "/queries/" + query, json={"expected_revision": 1, **query_input}
                        )
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertEqual(response.headers["cache-control"], "private, no-store")
                        fixtures[query if not query_input else "search"] = response.json()["data"]
                    self.assertEqual(fixtures["count"]["rows"], [{"count": 3}])
                    self.assertEqual(
                        fixtures["topics"]["rows"],
                        [{"label": "AI", "count": 2}, {"label": "Energy", "count": 1}],
                    )
                    self.assertEqual(fixtures["search"]["rows"][0]["id"], "article-0")
                    self.assertEqual(
                        fixtures["summary"]["rows"][0]["provenance"]["run_id"], accepted["id"]
                    )
                    one = (
                        await client.post(
                            base + "/queries/latest", json={"expected_revision": 1, "limit": 1}
                        )
                    ).json()["data"]
                    two = (
                        await client.post(
                            base + "/queries/latest",
                            json={"expected_revision": 1, "limit": 1, "offset": one["next_offset"]},
                        )
                    ).json()["data"]
                    self.assertNotEqual(one["rows"][0]["id"], two["rows"][0]["id"])
                    attachment = await client.post(
                        base + "/attachments",
                        json={
                            "filename": "sources.txt",
                            "content_base64": base64.b64encode(
                                b"Synthetic fixture sources"
                            ).decode(),
                            "idempotency_key": "news-attachment",
                        },
                    )
                    self.assertEqual(attachment.status_code, 201, attachment.text)
                    self.assertEqual((await client.post(base + "/backup")).status_code, 200)
                    exported = (await client.get(base + "/export")).json()["data"]
                    self.assertEqual(len(exported["records"]), 4)
                    self.assertEqual(len(exported["attachments"]), 1)
                    fixtures.update(
                        capabilities=caps.json()["data"],
                        page=(await client.get(base)).json()["data"],
                        activity=(await client.get(base + "/activity")).json()["data"],
                    )
                    # A new repository/service opens the committed disk state.
                    replacement = PagePlatform()
                    replacement.repository = FileRepository(root, root / "profiles")
                    replacement.conversation_runs = SimpleNamespace(_active={})
                    replacement.custom_page = CustomPageService(replacement)
                    platform.custom_page = replacement.custom_page
                    self.assertEqual((await client.get(base + "/export")).json()["data"], exported)
                    self.assertEqual(
                        (
                            await client.post(
                                base + "/queries/summary", json={"expected_revision": 1}
                            )
                        ).json()["data"],
                        fixtures["summary"],
                    )
                    context = multiprocessing.get_context("spawn")
                    channel, child = context.Pipe()
                    process = context.Process(target=read_from_new_process, args=(str(root), child))
                    process.start()
                    child.close()
                    try:
                        self.assertTrue(await asyncio.to_thread(channel.poll, 10))
                        restarted = channel.recv()
                        self.assertEqual(restarted["summary"], fixtures["summary"])
                        self.assertEqual(restarted["export"], exported)
                        await asyncio.to_thread(process.join, 5)
                        self.assertEqual(process.exitcode, 0)
                    finally:
                        if process.is_alive():
                            process.terminate()
                            await asyncio.to_thread(process.join, 5)
                        channel.close()
                    self.assertEqual(agent.calls, 2)
                    foreign = {
                        **headers,
                        "x-xnobrain-verified-subject": "other",
                        "x-xnobrain-principal-signature": principal_signature(
                            "synthetic-secret", "other", "tenant"
                        ),
                    }
                    self.assertEqual(
                        (await client.get(base + "/export", headers=foreign)).status_code, 404
                    )
                    output = os.getenv("XNOBRAIN_TEST_NEWS_FIXTURE_OUTPUT")
                    if output:
                        Path(output).write_text(json.dumps(fixtures), encoding="utf-8")
            finally:
                await runs.shutdown()
