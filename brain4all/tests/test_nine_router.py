"""Tests for the platform 9router integration adapter."""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

import yaml

from brain4all.integrations.hermes import AgentAPIError, AgentManager
from brain4all.integrations.nine_router import (
    NINE_ROUTER_API_BASE_URL,
    NINE_ROUTER_PROVIDER,
    NineRouterAPIError,
    NineRouterManager,
    normalize_nine_router_config,
)


class FakeNineRouterManager(NineRouterManager):
    def __init__(self, responses: dict[tuple[str, str], dict[str, Any]]):
        super().__init__(base_url="http://127.0.0.1:20128")
        self.responses = responses
        self.requests: list[tuple[str, str, dict[str, Any] | None]] = []

    async def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.requests.append((method, path, body))
        return self.responses.get((method, path), {})


class NineRouterConfigTests(unittest.TestCase):
    def test_hermes_exit_zero_provider_error_is_detected(self) -> None:
        message = (
            'HTTP 401: [codex/gpt-5.5] [401]: {"error": {'
            '"code": "token_invalidated"}}'
        )

        self.assertEqual(AgentManager._provider_error(message), message)
        self.assertEqual(AgentManager._provider_error("HTTP is a protocol"), "")

    def test_normalize_replaces_legacy_providers_and_fallbacks(self) -> None:
        config = {
            "model": {"provider": "anthropic", "default": "legacy-model"},
            "providers": {
                "anthropic": {"api": "https://api.anthropic.com"},
                "openai": {"api": "https://api.openai.com/v1"},
            },
            "fallback_providers": ["openai"],
            "agent": {"reasoning_effort": "high"},
        }

        selected = normalize_nine_router_config(config, "cx/gpt-5.4")

        self.assertEqual(selected, "cx/gpt-5.4")
        self.assertEqual(config["model"]["provider"], NINE_ROUTER_PROVIDER)
        self.assertEqual(config["model"]["base_url"], NINE_ROUTER_API_BASE_URL)
        self.assertEqual(list(config["providers"]), ["nine-router"])
        self.assertEqual(config["providers"]["nine-router"]["model"], "cx/gpt-5.4")
        self.assertNotIn("fallback_providers", config)
        self.assertEqual(config["agent"]["reasoning_effort"], "high")

    def test_cli_token_matches_native_nine_router_algorithm(self) -> None:
        with TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            (data_dir / "auth").mkdir()
            (data_dir / "machine-id").write_text("machine-123\n", encoding="utf-8")
            (data_dir / "auth" / "cli-secret").write_text("secret-456\n", encoding="utf-8")

            manager = NineRouterManager(data_dir=data_dir)

            self.assertEqual(manager._cli_token(), "35499f2df791a8a0")


class NineRouterManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_models_are_filtered_and_auto_combo_is_created(self) -> None:
        manager = FakeNineRouterManager(
            {
                ("GET", "/api/providers"): {
                    "connections": [
                        {
                            "id": "claude-1",
                            "provider": "claude",
                            "authType": "oauth",
                            "apiKey": "must-not-leak",
                        },
                        {
                            "id": "codex-1",
                            "provider": "codex",
                            "authType": "oauth",
                        },
                        {"id": "other-1", "provider": "openrouter"},
                    ]
                },
                ("GET", "/v1/models?kind=llm"): {
                    "data": [
                        {"id": "cc/claude-opus-4-1", "owned_by": "cc"},
                        {"id": "cx/gpt-5.4", "owned_by": "cx"},
                        {"id": "ag/gemini-3-pro", "owned_by": "ag"},
                        {"id": "openai/gpt-5.4", "owned_by": "openai"},
                    ]
                },
                ("GET", "/api/combos"): {"combos": []},
                ("POST", "/api/combos"): {"success": True},
            }
        )

        payload = await manager.list_models()

        self.assertEqual(
            [item["id"] for item in payload["data"]],
            ["auto", "cc/claude-opus-4-1", "cx/gpt-5.4"],
        )
        self.assertEqual(payload["data"][1]["provider"], "claude")
        self.assertEqual(payload["data"][2]["provider"], "codex")
        self.assertIn(
            (
                "POST",
                "/api/combos",
                {
                    "name": "auto",
                    "models": ["cc/claude-opus-4-1", "cx/gpt-5.4"],
                },
            ),
            manager.requests,
        )

        connections = await manager.list_connections()
        self.assertEqual(len(connections["connections"]), 2)
        self.assertNotIn("api_key", connections["connections"][0])
        self.assertNotIn("apiKey", connections["connections"][0])

    async def test_auto_requires_a_connected_provider_for_chat(self) -> None:
        manager = FakeNineRouterManager(
            {
                ("GET", "/api/providers"): {"connections": []},
                ("GET", "/v1/models?kind=llm"): {"data": []},
                ("GET", "/api/combos"): {"combos": []},
            }
        )

        with self.assertRaisesRegex(NineRouterAPIError, "connect at least one provider"):
            await manager.ensure_auto_combo()

    async def test_usage_is_filtered_to_the_current_model_provider(self) -> None:
        manager = FakeNineRouterManager(
            {
                ("GET", "/api/providers"): {
                    "connections": [
                        {
                            "id": "codex-1",
                            "provider": "codex",
                            "authType": "oauth",
                        }
                    ]
                },
                ("GET", "/api/usage/codex-1"): {
                    "plan": "plus",
                    "quotas": {
                        "session": {"used": 20, "total": 100, "resetAt": "2026-07-16T12:00:00Z"},
                        "weekly": {"used": 30, "total": 100, "resetAt": "2026-07-20T12:00:00Z"},
                        "review_session": {"used": 90, "total": 100},
                    },
                },
            }
        )

        payload = await manager.usage("cx/gpt-5.5")

        self.assertTrue(payload["available"])
        self.assertEqual(payload["provider"], "codex")
        self.assertEqual(payload["plan"], "plus")
        self.assertEqual(
            [(item["name"], item["remaining_percent"]) for item in payload["quotas"]],
            [("session", 80), ("weekly", 70)],
        )

    async def test_list_connections_returns_priority_and_email_without_credentials(self) -> None:
        manager = FakeNineRouterManager(
            {
                ("GET", "/api/providers"): {
                    "connections": [
                        {"id": "codex-1", "provider": "codex", "authType": "oauth",
                         "name": "work", "email": "work@example.com", "priority": 1,
                         "isActive": True, "apiKey": "must-not-leak",
                         "providerSpecificData": {"token": "must-not-leak"}},
                        {"id": "codex-2", "provider": "codex", "authType": "oauth",
                         "name": "home", "email": "home@example.com", "priority": 0,
                         "isActive": False},
                    ]
                }
            }
        )
        rows = (await manager.list_connections())["connections"]
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(by_id["codex-1"]["email"], "work@example.com")
        self.assertEqual(by_id["codex-1"]["priority"], 1)
        self.assertFalse(by_id["codex-2"]["active"])
        for row in rows:
            for forbidden in ("apiKey", "api_key", "providerSpecificData", "data", "token"):
                self.assertNotIn(forbidden, row)

    async def test_update_connection_puts_partial_body_and_reensures_auto(self) -> None:
        manager = FakeNineRouterManager(
            {
                ("PUT", "/api/providers/codex-2"): {
                    "connection": {"id": "codex-2", "provider": "codex",
                                   "authType": "oauth", "isActive": False},
                },
                ("GET", "/v1/models?kind=llm"): {"data": []},
                ("GET", "/api/combos"): {"combos": []},
                ("GET", "/api/providers"): {"connections": []},
            }
        )
        result = await manager.update_connection("codex-2", active=False)
        self.assertIn(("PUT", "/api/providers/codex-2", {"isActive": False}), manager.requests)
        self.assertFalse(result["connection"]["active"])
        self.assertTrue(any(path == "/api/combos" for _, path, _ in manager.requests))

    async def test_update_connection_requires_a_field(self) -> None:
        manager = FakeNineRouterManager({})
        with self.assertRaises(NineRouterAPIError):
            await manager.update_connection("codex-2")

    async def test_usage_for_connection_returns_all_quota_windows(self) -> None:
        manager = FakeNineRouterManager(
            {
                ("GET", "/api/usage/codex-2"): {
                    "plan": "plus",
                    "quotas": {
                        "session": {"used": 20, "total": 100},
                        "review_session": {"used": 90, "total": 100},
                    },
                }
            }
        )
        payload = await manager.usage_for_connection("codex-2")
        names = {item["name"] for item in payload["quotas"]}
        self.assertEqual(names, {"session", "review_session"})  # unfiltered by model
        self.assertTrue(payload["available"])

    def test_generated_agent_names_are_lowercase_and_start_with_a_letter(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = AgentManager(
                root_profile=root / "root",
                profiles_root=root / "profiles",
                legacy_agents_root=root / "legacy",
            )

            name = manager._new_agent_name()

            self.assertRegex(name, r"^[a-z][a-z0-9]{5}$")

    def test_agent_profile_starts_with_independent_state_and_office_baseline(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root_profile = root / "root"
            profile_template = root / "profile-template"
            (root_profile / "skills" / "office" / "writer").mkdir(parents=True)
            (root_profile / "memories").mkdir()
            (root_profile / "plugins" / "calendar").mkdir(parents=True)
            (root_profile / "cron").mkdir()
            (root_profile / "home").mkdir()
            profile_template.mkdir()
            (root_profile / "config.yaml").write_text(
                "model:\n  default: big-brother-model\n",
                encoding="utf-8",
            )
            (root_profile / "SOUL.md").write_text("Mutable Big Brother\n", encoding="utf-8")
            (root_profile / "AGENTS.md").write_text("Mutable Big Brother rules\n", encoding="utf-8")
            (root_profile / "memories" / "MEMORY.md").write_text(
                "Big Brother private memory\n",
                encoding="utf-8",
            )
            (root_profile / "plugins" / "calendar" / "config.yaml").write_text(
                "enabled: true\n",
                encoding="utf-8",
            )
            (root_profile / "cron" / "ticker_heartbeat").write_text(
                "runtime state\n",
                encoding="utf-8",
            )
            (root_profile / "home" / "runtime-cache").write_text(
                "runtime state\n",
                encoding="utf-8",
            )
            (root_profile / "skills" / "office" / "writer" / "SKILL.md").write_text(
                "# Writer\n",
                encoding="utf-8",
            )
            (profile_template / "config.yaml").write_text(
                "model:\n  default: auto\nterminal:\n  home_mode: profile\n",
                encoding="utf-8",
            )
            (profile_template / "SOUL.md").write_text(
                "Office assistant\n",
                encoding="utf-8",
            )
            (profile_template / "AGENTS.md").write_text(
                "Office rules\n",
                encoding="utf-8",
            )
            manager = AgentManager(
                root_profile=root_profile,
                profiles_root=root / "profiles",
                legacy_agents_root=root / "legacy",
                profile_template=profile_template,
            )

            payload, status = manager.create_agent({"name": "office1"})
            profile = Path(payload["profile_path"])

            self.assertEqual(status, 201)
            self.assertTrue((profile / "state.db").is_file())
            self.assertTrue((profile / "profile.yaml").is_file())
            self.assertTrue((profile / "skills" / "office" / "writer" / "SKILL.md").is_file())
            self.assertFalse((profile / "memories" / "MEMORY.md").exists())
            self.assertFalse((profile / "plugins" / "calendar" / "config.yaml").exists())
            self.assertEqual(
                (profile / "SOUL.md").read_text(encoding="utf-8"),
                "Office assistant\n",
            )
            self.assertEqual(
                yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))[
                    "model"
                ]["default"],
                "auto",
            )
            self.assertFalse((profile / "cron" / "ticker_heartbeat").exists())
            self.assertFalse((profile / "home" / "runtime-cache").exists())
            for dirname in ("sessions", "logs", "memories", "cron", "plugins", "home"):
                self.assertTrue((profile / dirname).is_dir(), dirname)

    def test_failed_agent_creation_removes_the_partial_generated_profile(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profiles = root / "profiles"
            manager = AgentManager(
                root_profile=root / "root",
                profiles_root=profiles,
                legacy_agents_root=root / "legacy",
            )

            with patch.object(
                manager,
                "_copy_seed_profile",
                side_effect=PermissionError("runtime state is unreadable"),
            ):
                with self.assertRaises(PermissionError):
                    manager.create_agent({"display_name": "News Summary"})

            self.assertEqual(list(profiles.iterdir()), [])
            self.assertEqual(
                [item["name"] for item in manager.list_agents()["agents"]],
                ["big-brother"],
            )

    def test_conversation_stream_resumes_the_open_session(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = AgentManager(
                root_profile=root / "root",
                profiles_root=root / "profiles",
                legacy_agents_root=root / "legacy",
            )
            manager.create_agent({"name": "news"})
            conversation = manager.create_conversation(
                "news",
                {"title": "News Summary"},
            )
            conversation_id = conversation["conversation"]["id"]

            prepared = manager._prepare_chat_command(
                "news",
                {
                    "message": "Summarize what we discussed.",
                    "conversation_id": conversation_id,
                },
                require_conversation=True,
            )

            command = prepared["command"]
            self.assertEqual(prepared["conversation_id"], conversation_id)
            self.assertEqual(
                command[command.index("--resume") : command.index("--resume") + 2],
                ["--resume", conversation_id],
            )
            self.assertEqual(
                command[-4:],
                ["chat", "--quiet", "--query", "Summarize what we discussed."],
            )
            self.assertNotIn("--oneshot", command)
            self.assertNotIn("-z", command)

    def test_default_conversation_titles_continue_without_duplicates(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = AgentManager(
                root_profile=root / "root",
                profiles_root=root / "profiles",
                legacy_agents_root=root / "legacy",
            )
            manager.create_agent({"name": "news"})

            first = manager.create_conversation("news", {})
            second = manager.create_conversation(
                "news",
                {"title": "New Conversation"},
            )
            third = manager.create_conversation(
                "news",
                {"title": "new conversation"},
            )
            custom = manager.create_conversation(
                "news",
                {"title": "Daily Briefing"},
            )

            self.assertEqual(first["conversation"]["title"], "New Conversation")
            self.assertEqual(second["conversation"]["title"], "New Conversation 2")
            self.assertEqual(third["conversation"]["title"], "New Conversation 3")
            self.assertEqual(custom["conversation"]["title"], "Daily Briefing")

            manager.delete_conversation(
                "news",
                second["conversation"]["id"],
            )
            fourth = manager.create_conversation("news", {})
            self.assertEqual(fourth["conversation"]["title"], "New Conversation 4")

            titles = [
                item["title"]
                for item in manager.list_conversations("news")["conversations"]
            ]
            self.assertEqual(len(titles), len(set(titles)))

            manager.create_agent({"name": "research"})
            research = manager.create_conversation("research", {})
            self.assertEqual(
                research["conversation"]["title"],
                "New Conversation",
            )

    def test_default_conversation_retries_an_atomic_title_conflict(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = AgentManager(
                root_profile=root / "root",
                profiles_root=root / "profiles",
                legacy_agents_root=root / "legacy",
            )
            manager.create_agent({"name": "news"})
            manager.create_conversation("news", {})
            profile_dir = manager._profile_dir("news")
            real_next_title = manager._next_default_conversation_title(profile_dir)

            with patch.object(
                manager,
                "_next_default_conversation_title",
                side_effect=["New Conversation", real_next_title],
            ):
                second = manager.create_conversation("news", {})

            self.assertEqual(second["conversation"]["title"], "New Conversation 2")
            sessions = manager.list_conversations("news")["conversations"]
            self.assertEqual(len(sessions), 2)
            self.assertTrue(all(item["title"] for item in sessions))

    def test_conversation_stream_does_not_create_a_missing_session(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = AgentManager(
                root_profile=root / "root",
                profiles_root=root / "profiles",
                legacy_agents_root=root / "legacy",
            )
            manager.create_agent({"name": "news"})
            profile = manager._profile_dir("news")
            missing_id = "20260727_092138_26112b"

            with self.assertRaises(AgentAPIError) as raised:
                manager._prepare_chat_command(
                    "news",
                    {
                        "message": "This must not create a conversation.",
                        "conversation_id": missing_id,
                    },
                    require_conversation=True,
                )

            self.assertEqual(raised.exception.status, 404)
            self.assertEqual(raised.exception.code, "conversation_not_found")
            self.assertIsNone(manager._session(profile, missing_id))

    async def test_conversation_stream_emits_incremental_model_deltas(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = AgentManager(
                root_profile=root / "root",
                profiles_root=root / "profiles",
                legacy_agents_root=root / "legacy",
            )

            async def run_session_agent(
                _prepared,
                *,
                run_id,
                stream_delta_callback,
                tool_progress_callback,
                approval_notify_callback,
                agent_ref,
            ):
                self.assertRegex(run_id, r"^run_[0-9a-f]{32}$")
                tool_progress_callback(
                    "tool.started",
                    "mcp__news__search",
                    "latest headlines",
                    {"query": "latest headlines"},
                )
                tool_progress_callback(
                    "reasoning.available",
                    "_thinking",
                    "Review the search results.",
                    None,
                )
                tool_progress_callback(
                    "tool.completed",
                    "mcp__news__search",
                    None,
                    None,
                    duration=0.125,
                    is_error=False,
                    result="private tool output",
                )
                approval_notify_callback({
                    "command": "rm -rf ./cache",
                    "description": "Delete the cache directory",
                    "pattern_keys": ["rm_recursive"],
                    "allow_permanent": False,
                })
                stream_delta_callback("First ")
                await asyncio.sleep(0)
                stream_delta_callback("second")
                return (
                    {"final_response": "First second", "messages": []},
                    {"input_tokens": 7, "output_tokens": 2, "total_tokens": 9},
                )

            prepared = {
                "name": "news",
                "profile_dir": root / "profile",
                "conversation_id": "20260727_093154_c2b5fe",
                "message": "Continue",
                "model": "cx/gpt-5.5",
                "requested_model": "",
                "timeout_seconds": 30,
            }
            with patch.object(
                manager,
                "_run_session_agent",
                side_effect=run_session_agent,
            ):
                chunks = [
                    event
                    async for event in manager._chat_stream_events(prepared)
                ]

            payload = b"".join(chunks)
            self.assertEqual(payload.count(b'"event":"message.delta"'), 2)
            self.assertIn(b'"event":"tool.started"', payload)
            self.assertIn(b'"tool":"mcp__news__search"', payload)
            self.assertIn(b'"event":"reasoning.available"', payload)
            self.assertIn(b'"text":"Review the search results."', payload)
            self.assertIn(b'"event":"tool.completed"', payload)
            self.assertIn(b'"duration":0.125', payload)
            self.assertNotIn(b"private tool output", payload)
            self.assertNotIn(b'"args"', payload)
            self.assertIn(b'"event":"approval.request"', payload)
            self.assertIn(b'"description":"Delete the cache directory"', payload)
            self.assertIn(b'"allow_permanent":false', payload)
            self.assertLess(
                payload.index(b'"delta":"First "'),
                payload.index(b'"delta":"second"'),
            )
            self.assertIn(
                b'"usage":{"input_tokens":7,"output_tokens":2,"total_tokens":9}',
                payload,
            )
            self.assertTrue(payload.endswith(b"data: [DONE]\n\n"))

    async def test_native_session_runner_loads_existing_history(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = AgentManager(
                root_profile=root / "root",
                profiles_root=root / "profiles",
                legacy_agents_root=root / "legacy",
            )
            manager.create_agent({"name": "news"})
            conversation = manager.create_conversation(
                "news",
                {"title": "News Summary"},
            )
            conversation_id = conversation["conversation"]["id"]
            profile = manager._profile_dir("news")
            db = manager._session_db(profile)
            db.append_message(conversation_id, "user", "Remember this.")
            db.append_message(conversation_id, "assistant", "I will remember.")
            db.close()
            observed: dict[str, Any] = {}

            class FakeSessionAdapter:
                def __init__(self, _config):
                    self._session_db = None

                @staticmethod
                def _bind_api_server_session(*, chat_id="", session_key="", session_id=""):
                    from gateway.session_context import set_session_vars

                    return set_session_vars(
                        platform="api_server",
                        chat_id=chat_id,
                        session_key=session_key,
                        session_id=session_id,
                        async_delivery=False,
                    )

                async def _conversation_history_for_session(self, session_id):
                    return self._session_db.get_messages_as_conversation(session_id)

                async def _run_agent(self, **kwargs):
                    observed.update(kwargs)
                    kwargs["agent_ref"][0] = type("FakeAgent", (), {
                        "model": "cx/gpt-5.6-luna",
                        "context_compressor": type("FakeCompressor", (), {
                            "last_prompt_tokens": 10_000,
                            "context_length": 200_000,
                        })(),
                    })()

                    def execute():
                        from gateway.session_context import clear_session_vars
                        from tools.approval import get_current_session_key

                        tokens = self._bind_api_server_session(
                            chat_id=kwargs["session_id"],
                            session_key=kwargs["gateway_session_key"],
                            session_id=kwargs["session_id"],
                        )
                        try:
                            observed["approval_session_key"] = get_current_session_key()
                            kwargs["stream_delta_callback"]("I remember.")
                        finally:
                            clear_session_vars(tokens)

                    await asyncio.to_thread(execute)
                    return (
                        {
                            "final_response": "I remember.",
                            "messages": [],
                            "model": "cx/gpt-5.6-luna",
                        },
                        {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
                    )

            prepared = manager._prepare_chat_command(
                "news",
                {
                    "message": "What did I ask you to remember?",
                    "conversation_id": conversation_id,
                },
                require_conversation=True,
            )
            deltas: list[str] = []
            with patch(
                "gateway.platforms.api_server.APIServerAdapter",
                FakeSessionAdapter,
            ):
                result, usage = await manager._run_session_agent(
                    prepared,
                    run_id="run_" + "a" * 32,
                    stream_delta_callback=deltas.append,
                    tool_progress_callback=lambda *_args, **_kwargs: None,
                    approval_notify_callback=lambda _data: None,
                    agent_ref=[None],
                )

            self.assertEqual(
                [item["content"] for item in observed["conversation_history"]],
                ["Remember this.", "I will remember."],
            )
            self.assertEqual(observed["session_id"], conversation_id)
            self.assertEqual(observed["gateway_session_key"], conversation_id)
            self.assertEqual(observed["approval_session_key"], "run_" + "a" * 32)
            self.assertEqual(observed["route"], {"model": prepared["model"]})
            self.assertNotIn("requested_model", observed)
            self.assertNotIn("session_model", observed)
            self.assertIn("tool_progress_callback", observed)
            self.assertEqual(deltas, ["I remember."])
            self.assertEqual(result["final_response"], "I remember.")
            self.assertEqual(usage["total_tokens"], 5)
            self.assertEqual(usage["context_used"], 10_000)
            self.assertEqual(usage["context_limit"], 200_000)
            stored = manager.get_conversation("news", conversation_id)
            self.assertEqual(
                stored["conversation"]["model_config"]["brain4all_context"],
                {
                    "used": 10_000,
                    "limit": 200_000,
                    "model": "cx/gpt-5.6-luna",
                },
            )

    async def test_run_approval_resolves_run_scope_and_emits_response_event(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = AgentManager(
                root_profile=root / "root",
                profiles_root=root / "profiles",
                legacy_agents_root=root / "legacy",
            )
            run_id = "run_" + "b" * 32
            queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
            manager._active_runs[run_id] = {
                "approval_session": run_id,
                "event_loop": asyncio.get_running_loop(),
                "event_queue": queue,
            }

            with patch("tools.approval.resolve_gateway_approval", return_value=1) as resolve:
                result = manager.resolve_approval(run_id, {"choice": "once"})

            resolve.assert_called_once_with(run_id, "once", False)
            self.assertEqual(result["resolved"], 1)
            kind, event = await queue.get()
            self.assertEqual(kind, "event")
            self.assertEqual(event["event"], "approval.responded")
            self.assertEqual(event["run_id"], run_id)

    async def test_native_runner_approves_memory_before_the_tool_saves(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = AgentManager(
                root_profile=root / "root",
                profiles_root=root / "profiles",
                legacy_agents_root=root / "legacy",
            )
            manager.create_agent({"name": "news"})
            conversation = manager.create_conversation("news", {"title": "News"})
            prepared = manager._prepare_chat_command(
                "news",
                {
                    "message": "Remember my preference",
                    "conversation_id": conversation["conversation"]["id"],
                },
                require_conversation=True,
            )
            observed: dict[str, Any] = {}
            approvals: list[dict[str, Any]] = []

            class FakeAgent:
                def run_conversation(self, **_kwargs):
                    from tools import terminal_tool

                    callback = terminal_tool._get_approval_callback()
                    observed["choice"] = callback(
                        "Use concise summaries",
                        "Save to memory: response preference",
                        allow_permanent=False,
                    )
                    return {"final_response": "Saved.", "messages": []}

            class FakeSessionAdapter:
                def __init__(self, _config):
                    self._session_db = None

                async def _conversation_history_for_session(self, _session_id):
                    return []

                def _create_agent(self, **_kwargs):
                    return FakeAgent()

                async def _run_agent(self, **kwargs):
                    def execute():
                        from tools import terminal_tool

                        agent = self._create_agent(
                            stream_delta_callback=kwargs["stream_delta_callback"],
                        )
                        result = agent.run_conversation()
                        observed["callback_restored"] = (
                            terminal_tool._get_approval_callback() is None
                        )
                        return result

                    result = await asyncio.to_thread(execute)
                    return result, {
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "total_tokens": 2,
                    }

            def approve(run_id, notify, data, *, surface):
                self.assertEqual(run_id, "run_" + "c" * 32)
                self.assertEqual(surface, "api_server")
                notify(data)
                return {"resolved": True, "choice": "once"}

            with (
                patch(
                    "gateway.platforms.api_server.APIServerAdapter",
                    FakeSessionAdapter,
                ),
                patch("tools.approval._await_gateway_decision", side_effect=approve),
            ):
                result, _usage = await manager._run_session_agent(
                    prepared,
                    run_id="run_" + "c" * 32,
                    stream_delta_callback=lambda _delta: None,
                    tool_progress_callback=lambda *_args, **_kwargs: None,
                    approval_notify_callback=approvals.append,
                    agent_ref=[None],
                )

            self.assertEqual(observed["choice"], "once")
            self.assertTrue(observed["callback_restored"])
            self.assertEqual(approvals[0]["subsystem"], "memory")
            self.assertEqual(result["final_response"], "Saved.")

    async def test_staged_skill_write_is_approved_applied_and_streamed(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = AgentManager(
                root_profile=root / "root",
                profiles_root=root / "profiles",
                legacy_agents_root=root / "legacy",
            )

            async def run_session_agent(
                _prepared,
                *,
                run_id,
                stream_delta_callback,
                tool_progress_callback,
                approval_notify_callback,
                agent_ref,
            ):
                del stream_delta_callback, approval_notify_callback, agent_ref
                tool_progress_callback(
                    "tool.started",
                    "skill_manage",
                    "create news-summary",
                    None,
                )
                tool_progress_callback(
                    "tool.completed",
                    "skill_manage",
                    None,
                    None,
                    duration=0.25,
                    is_error=False,
                    result=json.dumps({
                        "success": True,
                        "staged": True,
                        "pending_id": "pending-1",
                    }),
                )
                return (
                    {"final_response": "The skill was saved.", "messages": []},
                    {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                )

            def approve(_run_id, notify, data, *, surface):
                self.assertEqual(surface, "api_server")
                notify(data)
                return {"resolved": True, "choice": "once"}

            prepared = {
                "name": "news",
                "profile_dir": root / "profile",
                "conversation_id": "20260727_093154_c2b5fe",
                "message": "Create the skill",
                "model": "cx/gpt-5.5",
                "requested_model": "",
                "timeout_seconds": 30,
            }
            pending = {
                "id": "pending-1",
                "summary": "create 'news-summary'",
                "payload": {"action": "create", "name": "news-summary"},
            }
            with (
                patch.object(manager, "_run_session_agent", side_effect=run_session_agent),
                patch("tools.write_approval.get_pending", return_value=pending),
                patch("tools.approval._await_gateway_decision", side_effect=approve),
                patch(
                    "tools.skill_manager_tool.apply_skill_pending",
                    return_value='{"success": true}',
                ) as apply,
                patch("tools.write_approval.discard_pending", return_value=True) as discard,
            ):
                payload = b"".join([
                    event async for event in manager._chat_stream_events(prepared)
                ])

            self.assertIn(b'"event":"approval.request"', payload)
            self.assertIn(b'"subsystem":"skills"', payload)
            self.assertIn(b'"pending_id":"pending-1"', payload)
            self.assertIn(b'"event":"write.applied"', payload)
            apply.assert_called_once_with(pending["payload"])
            discard.assert_called_once_with("skills", "pending-1")

    async def test_agent_stream_closes_cleanly_without_a_provider(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = AgentManager(
                root_profile=root / "root",
                profiles_root=root / "profiles",
            )

            class EmptyRouter:
                async def ensure_auto_combo(self) -> None:
                    raise NineRouterAPIError(
                        "connect at least one provider before using auto",
                        code="provider_connection_required",
                        status=409,
                    )

            manager.nine_router = EmptyRouter()
            prepared = {
                "profile_dir": root / "profile",
                "workspace_dir": root / "workspace",
                "command": ["hermes"],
                "timeout_seconds": 1,
                "conversation_id": "proof-session",
                "model": "auto",
                "engine": "hermes",
            }

            payload = b"".join(
                [event async for event in manager._chat_stream_events(prepared)]
            )
            self.assertIn(b"event: error", payload)
            self.assertIn(b"connect at least one provider", payload)
            self.assertTrue(payload.endswith(b"data: [DONE]\n\n"))


if __name__ == "__main__":
    unittest.main()
