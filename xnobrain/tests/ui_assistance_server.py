"""Opt-in disposable cross-repository source fixture, never a production service.

Run inside the existing source container. Only model execution and budget admission
are scripted; native sessions, routes, gRPC, tools and durable run/storage code are
real. The random private gRPC port is recorded in --ready and never published.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import signal
import socket
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import grpc
import uvicorn
from fastapi import FastAPI, HTTPException, Request

from xnobrain.app import XNOBrainApplication
from xnobrain.integrations import AgentManager, GlobalConfigManager
from xnobrain.integrations.runtime_gateway import RuntimeGatewayService
from xnobrain.integrations.ui_composition_tools import bind_run, install_tools
from xnobrain.runtime.v1 import runtime_gateway_pb2_grpc
from xnobrain.tests.test_conversation_ownership import FakeRouter
from xnobrain.tests.test_conversation_runs import FakeAnalytics, sse
from xnobrain.trusted_context import from_request

TOKEN = "synthetic-ui-assistance-transport"


async def serve(ready: Path, stop: Path):
    finished = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(event, finished.set)
    with tempfile.TemporaryDirectory(prefix="ui-assistance-integration-") as temporary:
        root = Path(temporary)
        profile = root / "root"
        profile.mkdir()
        profiles = root / "profiles"
        profiles.mkdir()
        (profile / "config.yaml").write_text("model:\n  default: auto\n")
        environment = {
            "HERMES_HOME": str(profile),
            "HERMES_ROOT_PROFILE": str(profile),
            "HERMES_PROFILES_ROOT": str(profiles),
            "DATA_DIR": str(root / "data"),
            "RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT": "",
            "RUNTIME_INTERNAL_SERVICE_TOKEN": TOKEN,
            "RUNTIME_INCLUDE_PACKAGED_SKILLS": "false",
            "RUNTIME_ACCOUNTING_MODE": "legacy",
            "RUNTIME_GRPC_ENABLED": "false",
            "RUNTIME_CONTROL_URL": "",
            "RUNTIME_RUNNER_ENROLLMENT_PROOF": "",
        }
        with patch.dict(os.environ, environment):
            agents = AgentManager(
                root_profile=profile, profiles_root=profiles, legacy_agents_root=root / "legacy"
            )
            composition = XNOBrainApplication(
                agents, GlobalConfigManager(root_profile=profile), FakeRouter()
            )
            platform = composition.service
            await platform.ensure_default_agent()
            # Explicitly public fixture definition, distinct from private app rows.
            (profile / "SOUL.md").write_text("Synthetic public research definition\n")
            (profile / "workspace").mkdir(exist_ok=True)
            (profile / "workspace/AGENTS.md").write_text("Cite approved public sources\n")
            analytics = FakeAnalytics()
            platform.conversation_runs.analytics = analytics
            evidence = {
                "model_calls": 0,
                "tool_names": [],
                "errors": [],
                "browser_credentials_received": False,
            }

            schedule_modes = {}

            def execute(agent, body):
                from tools.registry import registry

                session = body["conversation_id"]
                with bind_run(
                    platform.ui_composition, agent, session, body["run_id"], body["_ui_assistance"]
                ):
                    model = SimpleNamespace(tools=[], valid_tool_names=set())
                    install_tools(model, session)
                    evidence["tool_names"] = sorted(model.valid_tool_names)
                    assert model.valid_tool_names == {"ui_layout_catalog", "ui_layout_propose"}
                    assert model.max_iterations == 10
                    catalog = json.loads(
                        registry.dispatch("ui_layout_catalog", {}, session_id=session)
                    )
                    assert catalog["success"], catalog
                    layout = copy.deepcopy(catalog["data"]["layout"])
                    layout.update(
                        name="Scripted focused layout",
                        preset="stacked",
                        theme="light",
                        accent="violet",
                        inspector_position="left",
                        inspector_height=300,
                        navigation={"order": ["settings", "skills"], "hidden": ["cron"]},
                        default_page="settings",
                    )
                    result = json.loads(
                        registry.dispatch(
                            "ui_layout_propose", {"layout": layout}, session_id=session
                        )
                    )
                    assert result["success"] and result["data"]["approval_required"], result

            def execute_page(agent, body):
                from tools.registry import registry

                from xnobrain.integrations.custom_page_tools import bind_run as bind_page
                from xnobrain.integrations.custom_page_tools import install_tools as install_page
                from xnobrain.tests.test_custom_page import news_manifest

                session = body["conversation_id"]
                with bind_page(
                    platform.custom_page,
                    agent,
                    session,
                    body["run_id"],
                    body["_custom_page_principal"],
                ):
                    model = SimpleNamespace(tools=[], valid_tool_names=set())
                    install_page(model, session)

                    def call(name, args):
                        assert name in model.valid_tool_names
                        response = json.loads(registry.dispatch(name, args, session_id=session))
                        assert response["success"], response
                        return response["data"]

                    if "custom_page" in body.get("capabilities", []):
                        manifest = news_manifest()
                        manifest["description"] = (
                            "Synthetic corpus, scripted model, real persisted data"
                        )
                        manifest["actions"] = [
                            {
                                "id": "analyze",
                                "label": "Analyze stored news",
                                "kind": "analyze",
                                "instruction": "Analyze the authorized synthetic corpus",
                                "datasets": ["articles"],
                            }
                        ]
                        call(
                            "custom_page_prepare",
                            {
                                "manifest": manifest,
                                "expected_revision": 0,
                                "idempotency_key": "prepare-news",
                            },
                        )
                        kind, title, key = "synthetic", "Synthetic managed news", "collect-news"
                    else:
                        kind, title, key = "generated", "Scripted analysis result", "analyze-news"
                    run = platform.repository.get_conversation_run(agent, session, body["run_id"])
                    if run.get("custom_page_schedule"):
                        assert model.max_iterations == 20
                        assert "custom_page_prepare" not in model.valid_tool_names
                        assert model.valid_tool_names == {
                            "custom_page_inspect",
                            "custom_page_query",
                            "custom_page_write",
                        }
                        kind, title, key = (
                            "generated",
                            "Scheduled analysis result",
                            "scheduled-" + run["id"],
                        )
                    call(
                        "custom_page_write",
                        {
                            "dataset_id": "articles",
                            "expected_revision": 1,
                            "idempotency_key": key,
                            "records": [
                                {
                                    "id": key,
                                    "values": {"title": title, "topic": "AI"},
                                    "provenance": {
                                        "kind": kind,
                                        "source": "Synthetic verification corpus",
                                        "collected_at": "2026-09-14T00:00:00Z",
                                    },
                                }
                            ],
                        },
                    )

            async def scripted_chat(agent, body):
                evidence["model_calls"] += 1
                run_id = body["run_id"]
                yield sse({"event": "run.started", "run_id": run_id})
                try:
                    if "Wait for cancellation" in body["input"]:
                        await asyncio.Event().wait()
                    run = platform.repository.get_conversation_run(
                        agent, body["conversation_id"], run_id
                    )
                    mode = schedule_modes.get(run.get("custom_page_schedule"), "complete")
                    if mode == "hold":
                        await asyncio.Event().wait()
                    await asyncio.to_thread(
                        execute if body.get("_ui_assistance") else execute_page, agent, body
                    )
                    if mode == "fail_after_write":
                        yield sse(
                            {
                                "event": "run.failed",
                                "run_id": run_id,
                                "message": "Synthetic failure after a committed record",
                            }
                        )
                        return
                    yield sse(
                        {
                            "event": "run.completed",
                            "run_id": run_id,
                            "output": "Scripted proposal only",
                            "usage": {"total_tokens": 0},
                        }
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    evidence["errors"].append(type(error).__name__)
                    yield sse(
                        {
                            "event": "run.failed",
                            "run_id": run_id,
                            "message": "Synthetic fixture failed",
                        }
                    )

            agents.chat_stream = scripted_chat
            app = FastAPI()
            composition.register(app)

            @app.middleware("http")
            async def check_private_boundary(request: Request, call_next):
                if "authorization" in request.headers or "cookie" in request.headers:
                    evidence["browser_credentials_received"] = True
                return await call_next(request)

            interrupted_removals = set()
            actual_delete = platform.delete_agent

            def interrupt_delete(agent_id, **kwargs):
                if agent_id in interrupted_removals:
                    from xnobrain.services.base import ServiceError

                    interrupted_removals.remove(agent_id)
                    raise ServiceError(
                        "Synthetic interruption", status=503, code="synthetic_interruption"
                    )
                return actual_delete(agent_id, **kwargs)

            platform.delete_agent = interrupt_delete

            @app.post("/xnobrain/api/runtime/v1/test-verification/interrupt-removal/{agent_id}")
            def interrupt_removal(agent_id: str, request: Request):
                if not from_request(request).subject:
                    return {"success": False}
                interrupted_removals.add(agent_id)
                return {"success": True, "data": {"armed": True}}

            @app.get("/xnobrain/api/runtime/v1/test-verification/stats")
            def stats(request: Request):
                if not from_request(request).subject:
                    return {"success": False}
                return {"success": True, "data": {**evidence, "admissions": len(analytics.calls)}}

            @app.post("/xnobrain/api/runtime/v1/test-verification/schedules/{identifier}/due")
            async def make_schedule_due(identifier: str, request: Request):
                # Fixture-only clock control: no direct execute/run_action call.
                # Production lifespan discovery and native claim/run/history do
                # the actual work. Never re-enable a stopped binding here.
                from xnobrain.integrations.custom_page_cron import store

                trusted = from_request(request)
                owner = platform.custom_page.authority("big-brother", trusted)
                binding = platform.custom_page.schedules.repository.get(
                    "big-brother", owner, identifier
                )
                body = await request.json()
                mode = body.get("mode", "complete")
                if set(body) - {"mode"} or mode not in {"complete", "fail_after_write", "hold"}:
                    raise HTTPException(status_code=422)
                if binding["state"] != "scheduled":
                    return {"data": {"armed": False}}
                with store(platform.cron, "big-brother") as jobs, jobs._jobs_lock():
                    rows = jobs.load_jobs()
                    native = next(row for row in rows if row["id"] == identifier)
                    if not native["enabled"]:
                        return {"data": {"armed": False}}
                    schedule_modes[identifier] = mode
                    native["next_run_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
                    jobs.save_jobs(rows)
                return {"data": {"armed": True}}

            @app.get("/xnobrain/api/runtime/v1/test-verification/schedules/{identifier}/native")
            def schedule_native(identifier: str, request: Request):
                owner = platform.custom_page.authority("big-brother", from_request(request))
                platform.custom_page.schedules.repository.get("big-brother", owner, identifier)
                job = platform.cron._native("big-brother", "get_job", identifier)
                history = platform.cron._native_module(
                    "big-brother", "cron.executions", "list_executions", job_id=identifier
                )
                return {
                    "data": {
                        "enabled": job["enabled"],
                        "fire_claim": bool(job.get("fire_claim")),
                        "repeat_completed": job["repeat"]["completed"],
                        "history_statuses": [row["status"] for row in history],
                    }
                }

            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            http_port = listener.getsockname()[1]
            http = uvicorn.Server(
                uvicorn.Config(app, log_level="error", access_log=False, lifespan="on")
            )
            http_task = asyncio.create_task(http.serve(sockets=[listener]))
            grpc_server = grpc.aio.server()
            relay = RuntimeGatewayService(TOKEN, http_port)
            runtime_gateway_pb2_grpc.add_RuntimeGatewayServiceServicer_to_server(relay, grpc_server)
            runtime_gateway_pb2_grpc.add_NodeGatewayServiceServicer_to_server(relay, grpc_server)
            grpc_port = grpc_server.add_insecure_port("0.0.0.0:0")
            try:
                await grpc_server.start()
                for _attempt in range(100):
                    if http.started:
                        break
                    await asyncio.sleep(0.05)
                assert http.started, "fixture HTTP did not start"
                ready.write_text(json.dumps({"grpc_port": grpc_port}))
                deadline = loop.time() + 240
                while not finished.is_set() and not stop.exists() and loop.time() < deadline:
                    await asyncio.sleep(0.1)
            finally:
                await platform.conversation_runs.shutdown()
                await grpc_server.stop(1)
                http.should_exit = True
                await http_task
                listener.close()
                print(json.dumps({"fixture_closed": True, **evidence}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument("--stop", type=Path, required=True)
    arguments = parser.parse_args()
    asyncio.run(serve(arguments.ready, arguments.stop))
