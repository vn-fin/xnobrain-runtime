"""Tests for the platform 9router integration adapter."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

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
            (root_profile / "skills" / "office" / "writer").mkdir(parents=True)
            (root_profile / "memories").mkdir()
            (root_profile / "plugins" / "calendar").mkdir(parents=True)
            (root_profile / "cron").mkdir()
            (root_profile / "home").mkdir()
            (root_profile / "config.yaml").write_text(
                "model:\n  default: auto\nterminal:\n  home_mode: profile\n",
                encoding="utf-8",
            )
            (root_profile / "SOUL.md").write_text("Office assistant\n", encoding="utf-8")
            (root_profile / "AGENTS.md").write_text("Office rules\n", encoding="utf-8")
            (root_profile / "memories" / "MEMORY.md").write_text(
                "Baseline office conventions\n",
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
            manager = AgentManager(
                root_profile=root_profile,
                profiles_root=root / "profiles",
                legacy_agents_root=root / "legacy",
            )

            payload, status = manager.create_agent({"name": "office1"})
            profile = Path(payload["profile_path"])

            self.assertEqual(status, 201)
            self.assertTrue((profile / "state.db").is_file())
            self.assertTrue((profile / "profile.yaml").is_file())
            self.assertTrue((profile / "skills" / "office" / "writer" / "SKILL.md").is_file())
            self.assertEqual(
                (profile / "memories" / "MEMORY.md").read_text(encoding="utf-8"),
                "Baseline office conventions\n",
            )
            self.assertTrue((profile / "plugins" / "calendar" / "config.yaml").is_file())
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
            self.assertEqual(manager.list_agents()["agents"], [])

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
