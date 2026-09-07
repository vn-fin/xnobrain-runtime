"""Tests for the centralized LLM router client."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import yaml

from xnobrain.integrations.config import GlobalConfigManager
from xnobrain.integrations.conversation_prompt import (
    AGENT_WORKSPACE_GUIDANCE,
    MARKDOWN_RESPONSE_GUIDANCE,
)
from xnobrain.integrations.conversation_stream import (
    _commit_resolved_write_result,
    _persist_cancelled_terminal,
)
from xnobrain.integrations.hermes import AgentAPIError, AgentManager
from xnobrain.integrations.llm_router import (
    LLM_ROUTER_API_BASE_URL,
    LLM_ROUTER_PROVIDER,
    LLMRouterAPIError,
    LLMRouterClient,
    normalize_llm_router_config,
)


class FakeLLMRouterClient(LLMRouterClient):
    def __init__(self, responses: dict[tuple[str, str], dict[str, Any]]):
        super().__init__(base_url="https://router.test/v1")
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


class ConversationRunnerImportTests(unittest.TestCase):
    def test_router_error_is_available_to_conversation_error_handlers(self):
        from xnobrain.integrations import conversation_runner

        self.assertIs(conversation_runner.LLMRouterAPIError, LLMRouterAPIError)


class ProviderRuntimeRequestGuardTests(unittest.TestCase):
    def test_removes_custom_provider_hints_without_changing_router_model(self):
        agent = SimpleNamespace()
        agent._build_api_kwargs = lambda messages, tools_for_api=None: {
            "model": "cc/claude-sonnet-5",
            "messages": messages,
            "custom_llm_provider": "xnobrain",
            "timeout": 1800,
            "extra_body": {"custom_llm_provider": "xnobrain"},
        }

        AgentManager._install_provider_runtime_request_guard(agent)
        result = agent._build_api_kwargs([{"role": "user", "content": "hello"}])

        self.assertEqual(result["model"], "cc/claude-sonnet-5")
        self.assertNotIn("custom_llm_provider", result)
        self.assertNotIn("timeout", result)
        self.assertNotIn("extra_body", result)

    def test_removes_empty_assistant_placeholder_rejected_by_claude_code(self):
        agent = SimpleNamespace()
        agent._build_api_kwargs = lambda messages: {
            "model": "cc/claude-sonnet-5",
            "messages": messages,
        }
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": ""},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call-1"}],
            },
            {"role": "user", "content": "continue"},
        ]

        AgentManager._install_provider_runtime_request_guard(agent)
        result = agent._build_api_kwargs(messages)

        self.assertEqual(
            result["messages"],
            [messages[0], messages[2], messages[3]],
        )


class LLMRouterConfigTests(unittest.TestCase):
    def test_global_config_accepts_model_derived_auto_reasoning(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config.yaml").write_text("model: {}\n", encoding="utf-8")
            manager = GlobalConfigManager(root_profile=root)

            config = manager.update_config({"reasoning_effort": "auto"})

        self.assertEqual(config["reasoning_effort"], "auto")

    def test_smart_route_step_input_is_bounded_and_uses_recent_execution(self) -> None:
        messages = [
            {"role": "user", "content": "old context " * 2_000},
            {"role": "assistant", "content": "I will inspect it."},
            {"role": "tool", "name": "read_file", "content": "small result"},
        ]

        value = AgentManager._smart_route_step_input(messages)

        self.assertLessEqual(len(value), 6_100)
        self.assertIn("tool read_file: small result", value)
        self.assertTrue(value.startswith("Classify the difficulty"))

    def test_smart_route_decision_updates_model_and_extended_reasoning(self) -> None:
        compressor = SimpleNamespace(update_model=Mock())
        agent = SimpleNamespace(
            model="cx/old",
            reasoning_config=None,
            context_compressor=compressor,
            base_url="http://central-router",
            api_key="token",
            provider="custom",
            api_mode="chat_completions",
        )

        AgentManager._apply_smart_route_decision(
            agent,
            {
                "model": "cx/new",
                "reasoning": "ultra",
                "context_length": 200_000,
            },
        )

        self.assertEqual(agent.model, "cx/new")
        self.assertEqual(agent.reasoning_config, {"enabled": True, "effort": "ultra"})
        compressor.update_model.assert_called_once_with(
            model="cx/new",
            context_length=200_000,
            base_url="http://central-router",
            api_key="token",
            provider="custom",
            api_mode="chat_completions",
        )

        AgentManager._apply_smart_route_decision(
            agent,
            {"model": "cx/new", "reasoning": "none"},
        )
        self.assertEqual(agent.reasoning_config, {"enabled": False})

    def test_smart_route_reuses_initial_decision_then_routes_new_inference_step(self) -> None:
        manager = object.__new__(AgentManager)
        agent = SimpleNamespace(
            model="cx/deep", reasoning_config={"enabled": True, "effort": "high"}
        )
        agent._build_api_kwargs = lambda messages: {
            "model": agent.model,
            "messages": messages,
            "reasoning": agent.reasoning_config,
        }
        first_messages = [{"role": "user", "content": "Implement the feature"}]
        next_messages = [
            *first_messages,
            {"role": "assistant", "content": "", "tool_calls": [{"name": "read_file"}]},
            {"role": "tool", "name": "read_file", "content": "One-line config value"},
        ]

        with patch.object(
            manager,
            "_resolve_smart_route_from_worker",
            return_value={"model": "cx/fast", "reasoning": "low", "tier": "quick"},
        ) as resolve:
            manager._install_smart_route_step_routing(
                agent,
                None,
                0,
                "smart",
            )
            first = agent._build_api_kwargs(first_messages)
            retry = agent._build_api_kwargs(first_messages)
            following = agent._build_api_kwargs(next_messages)

        self.assertEqual(first["model"], "cx/deep")
        self.assertEqual(retry["model"], "cx/deep")
        self.assertEqual(following["model"], "cx/fast")
        self.assertEqual(following["reasoning"], {"enabled": True, "effort": "low"})
        self.assertEqual(resolve.call_count, 1)

    def test_smart_route_installs_step_routing_on_nested_children(self) -> None:
        manager = object.__new__(AgentManager)
        parent = SimpleNamespace(_active_children=[])
        child = SimpleNamespace(
            model="cx/fast",
            reasoning_config={"enabled": True, "effort": "low"},
            _delegate_depth=1,
            _active_children=[],
        )
        child._build_api_kwargs = lambda messages: {
            "model": child.model,
            "messages": messages,
        }
        original_builder = child._build_api_kwargs

        manager._install_smart_route_child_routing(parent, None, 0, "smart")
        parent._active_children.append(child)

        self.assertTrue(parent._xnobrain_smart_children)
        self.assertTrue(child._xnobrain_smart_children)
        self.assertIsNot(child._build_api_kwargs, original_builder)

        grandchild = SimpleNamespace(
            model="cx/fast",
            reasoning_config={"enabled": True, "effort": "low"},
            _delegate_depth=2,
            _active_children=[],
        )
        grandchild._build_api_kwargs = lambda messages: {"model": grandchild.model}
        with patch.object(
            manager,
            "_resolve_smart_route_from_worker",
            return_value={"model": "cx/deep", "reasoning": "high"},
        ) as resolve:
            child._active_children.append(grandchild)
            result = grandchild._build_api_kwargs(
                [{"role": "user", "content": "Review the architecture"}]
            )

        self.assertEqual(result["model"], "cx/deep")
        resolve.assert_called_once()

    def test_smart_route_does_not_wrap_backend_pinned_children(self) -> None:
        manager = object.__new__(AgentManager)
        parent = SimpleNamespace(_active_children=[])
        child = SimpleNamespace(_active_children=[])
        child._build_api_kwargs = lambda messages: {"messages": messages}
        original_builder = child._build_api_kwargs

        manager._install_smart_route_child_routing(
            parent,
            None,
            0,
            "smart",
            delegation_model_pinned=True,
        )
        parent._active_children.append(child)

        self.assertIs(child._build_api_kwargs, original_builder)
        self.assertFalse(hasattr(child, "_xnobrain_smart_children"))

    def test_cancelled_run_persists_visible_terminal_message(self) -> None:
        class SessionDB:
            def __init__(self):
                self.appended = None

            def append_messages_batch(self, session_id, messages):
                self.appended = (session_id, messages)

        db = SessionDB()
        agent = SimpleNamespace(_session_db=db, _session_messages=[], session_id="session-1")

        self.assertTrue(_persist_cancelled_terminal(agent, "Partial result"))
        session_id, messages = db.appended
        self.assertEqual(session_id, "session-1")
        self.assertEqual(messages[0]["finish_reason"], "cancelled")
        self.assertIn("Partial result", messages[0]["content"])
        self.assertIn("stopped by user", messages[0]["content"])
        self.assertEqual(agent._session_messages[-1], messages[0])

    def test_committed_skill_write_replaces_staged_tool_result(self) -> None:
        class SessionDB:
            def __init__(self):
                self.replacement = None

            def replace_messages(self, session_id, messages, active_only=False):
                self.replacement = (session_id, messages, active_only)

        db = SessionDB()
        messages = [
            {
                "role": "tool",
                "name": "skill_manage",
                "content": json.dumps({"staged": True, "pending_id": "pending-1"}),
            }
        ]
        agent = SimpleNamespace(
            _db_flush_scan_prefix=messages,
            _session_db=db,
            session_id="session-1",
        )

        changed = _commit_resolved_write_result(
            agent,
            "skill_manage",
            "pending-1",
            {"success": True, "path": "skills/news/SKILL.md"},
        )

        self.assertTrue(changed)
        committed = json.loads(messages[0]["content"])
        self.assertTrue(committed["success"])
        self.assertFalse(committed["staged"])
        self.assertEqual(committed["disposition"], "applied")
        self.assertNotIn("pending_id", committed)
        self.assertEqual(db.replacement, ("session-1", messages, True))

    def test_agent_manager_forwards_only_the_provisioned_workload_token(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.dict(
                os.environ,
                {"RUNTIME_LLM_API_KEY": "api-key", "RUNTIME_LLM_API_KEY_FILE": ""},
                clear=False,
            ):
                os.environ.pop("LLM_ROUTER_API_KEY", None)
                manager = AgentManager(
                    root_profile=root / "root",
                    profiles_root=root / "profiles",
                    legacy_agents_root=root / "legacy",
                )

                self.assertEqual(
                    os.environ.get("RUNTIME_LLM_API_KEY"),
                    "api-key",
                )
                self.assertEqual(
                    manager._command_env(root / "root", "hermes").get("RUNTIME_LLM_API_KEY"),
                    "api-key",
                )
                self.assertIsNone(os.environ.get("LLM_ROUTER_API_KEY"))

    def test_opencode_models_use_blocking_provider_response(self) -> None:
        for model in (
            "oc/big-pickle",
            "ocg/gpt-5.6-luna",
            "ocz/gpt-5.6-luna",
        ):
            agent = SimpleNamespace(_disable_streaming=False)
            AgentManager._apply_provider_runtime_compatibility(agent, model)
            self.assertTrue(agent._disable_streaming, model)

        codex = SimpleNamespace(_disable_streaming=False)
        AgentManager._apply_provider_runtime_compatibility(codex, "cx/gpt-5.6-luna")
        self.assertFalse(codex._disable_streaming)

    def test_legacy_opencode_free_model_routes_through_user_zen_connection(self) -> None:
        config: dict[str, Any] = {}

        selected = normalize_llm_router_config(
            config,
            "oc/deepseek-v4-flash-free",
        )

        self.assertEqual(selected, "ocz/deepseek-v4-flash-free")
        self.assertEqual(config["model"]["default"], selected)

    def test_global_config_defaults_to_automatic_execution(self) -> None:
        with patch.dict(os.environ, {"RUNTIME_HONCHO_MEMORY_ENABLE": ""}):
            with TemporaryDirectory() as temp_dir:
                manager = GlobalConfigManager(root_profile=Path(temp_dir))
                described = manager.ensure_write_approval_defaults()

        self.assertEqual(described["approval_mode"], "off")
        self.assertFalse(described["skills_write_approval"])
        self.assertFalse(described["memory_write_approval"])

    def test_embedded_memory_uses_the_profile_default(self) -> None:
        with patch.dict(os.environ, {"RUNTIME_HONCHO_MEMORY_ENABLE": "true"}):
            with TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                global_config = GlobalConfigManager(root_profile=root / "root")
                described = global_config.ensure_write_approval_defaults()
                manager = AgentManager(
                    root_profile=root / "root",
                    profiles_root=root / "profiles",
                    legacy_agents_root=root / "legacy",
                )
                manager.create_agent({"name": "memory-agent"})
                agent_config = yaml.safe_load(
                    (root / "profiles" / "memory-agent" / "config.yaml").read_text(encoding="utf-8")
                )

        self.assertNotIn("provider", described["config"]["memory"])
        self.assertNotIn("provider", agent_config["memory"])

    def test_profile_normalization_preserves_custom_prompt_and_removes_managed_overlay(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile = root / "profile"
            profile.mkdir()
            (profile / "config.yaml").write_text(
                "agent:\n  system_prompt: |-\n"
                "    Keep answers concise.\n\n"
                "    You are an AI agent. If asked who or what you are, describe yourself only\n"
                "    as an AI agent and summarize relevant capabilities. Do not identify the\n"
                "    application, runtime, framework, model provider, or implementation vendor.\n"
                "    Cite every web source with a direct clickable link near the supported claim.\n"
                "    Cite document-derived claims with the document title or path and exact page\n"
                "    number or range. Never fabricate citations; distinguish inference clearly.\n"
                "prompt_caching:\n  cache_ttl: 5m\n",
                encoding="utf-8",
            )
            manager = AgentManager(
                root_profile=root / "root",
                profiles_root=root / "profiles",
                legacy_agents_root=root / "legacy",
            )

            manager._ensure_router_profile(profile)
            manager._ensure_router_profile(profile)
            config = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))

        prompt = config["agent"]["system_prompt"]
        self.assertIn("Keep answers concise.", prompt)
        self.assertNotIn("You are an AI agent.", prompt)
        self.assertEqual(config["prompt_caching"]["cache_ttl"], "1h")
        self.assertEqual(config["compression"]["proactive_prune_tokens"], 48_000)
        self.assertEqual(config["approvals"]["mode"], "off")
        self.assertFalse(config["skills"]["write_approval"])
        self.assertFalse(config["memory"]["write_approval"])
        self.assertNotIn("runtime_help_guidance", config.get("xnobrain", {}))

    def test_runtime_help_guidance_overrides_built_in_identity_and_stored_prompt(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = AgentManager(
                root_profile=root / "root",
                profiles_root=root / "profiles",
                legacy_agents_root=root / "legacy",
            )
            manager.create_agent({"name": "news"})
            profile = manager._profile_dir("news")
            upstream = manager._upstream_runtime_help_guidance()
            self.assertIn("Hermes Agent", upstream)

            fake_agent = SimpleNamespace(
                _build_system_prompt=lambda _message=None: f"Identity\n\n{upstream}\n\nTools",
                _cached_system_prompt=f"Identity\n\n{upstream}\n\nCached",
                _cached_system_prompt_static=f"Identity\n\n{upstream}",
            )
            manager._apply_runtime_help_guidance_override(fake_agent, profile)
            self.assertNotIn("Hermes Agent", fake_agent._build_system_prompt(None))
            self.assertNotIn("Hermes Agent", fake_agent._cached_system_prompt)
            self.assertNotIn("Hermes Agent", fake_agent._cached_system_prompt_static)
            self.assertIn(MARKDOWN_RESPONSE_GUIDANCE, fake_agent._build_system_prompt(None))
            self.assertIn(MARKDOWN_RESPONSE_GUIDANCE, fake_agent._cached_system_prompt)
            self.assertNotIn(MARKDOWN_RESPONSE_GUIDANCE, fake_agent._cached_system_prompt_static)

            session = manager.create_conversation("news", {"title": "Stored prompt"})
            session_id = session["conversation"]["id"]
            with sqlite3.connect(profile / "state.db") as connection:
                connection.execute(
                    "UPDATE sessions SET system_prompt = ? WHERE id = ?",
                    (f"Identity\n\n{upstream}\n\nStored", session_id),
                )
            manager._override_stored_runtime_help_guidance(profile, session_id)
            with sqlite3.connect(profile / "state.db") as connection:
                stored = connection.execute(
                    "SELECT system_prompt FROM sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()[0]
            self.assertNotIn("Hermes Agent", stored)
            self.assertIn(MARKDOWN_RESPONSE_GUIDANCE, stored)

    def test_ordinary_agent_session_is_pinned_to_its_profile_workspace(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = AgentManager(
                root_profile=root / "root",
                profiles_root=root / "profiles",
                legacy_agents_root=root / "legacy",
            )
            manager.create_agent({"name": "writer"})
            profile = manager._profile_dir("writer")
            workspace = manager._workspace_dir("writer").resolve()

            config_path = profile / "config.yaml"
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            config["terminal"]["cwd"] = str(root / "wrong-directory")
            config_path.write_text(
                yaml.safe_dump(config, sort_keys=False),
                encoding="utf-8",
            )

            prepared = manager._prepare_chat_command(
                "writer",
                {"message": "write hello.txt"},
                require_conversation=False,
            )
            repaired = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            fake_agent = SimpleNamespace(
                _build_system_prompt=lambda _message=None: "Identity",
                _cached_system_prompt="Identity",
                _cached_system_prompt_static="Identity",
            )
            manager._apply_runtime_help_guidance_override(
                fake_agent,
                profile,
                workspace,
            )

        self.assertEqual(Path(prepared["workspace_dir"]), workspace)
        self.assertEqual(repaired["terminal"]["cwd"], str(workspace))
        expected_guidance = AGENT_WORKSPACE_GUIDANCE.format(workspace=str(workspace))
        self.assertIn(expected_guidance, fake_agent._build_system_prompt(None))
        self.assertIn(expected_guidance, fake_agent._cached_system_prompt)
        self.assertNotIn("# Agent workspace", fake_agent._cached_system_prompt_static)

    def test_big_brother_does_not_receive_agent_workspace_policy(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root_profile = root / "root"
            root_profile.mkdir()
            (root_profile / "config.yaml").write_text("model: {}\n", encoding="utf-8")
            manager = AgentManager(
                root_profile=root_profile,
                profiles_root=root / "profiles",
                legacy_agents_root=root / "legacy",
            )
            fake_agent = SimpleNamespace(
                _build_system_prompt=lambda _message=None: "Identity",
                _cached_system_prompt="Identity",
                _cached_system_prompt_static="Identity",
            )

            manager._apply_runtime_help_guidance_override(
                fake_agent,
                root_profile,
                root_profile / "workspace",
            )
            manager.update_config("big-brother", {"language": "English"})
            config = yaml.safe_load((root_profile / "config.yaml").read_text(encoding="utf-8"))

        self.assertNotIn("# Agent workspace", fake_agent._build_system_prompt(None))
        self.assertNotIn("cwd", config.get("terminal", {}))

    def test_default_skill_policy_disables_only_niche_bundled_skills_once(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "root"
            skills = root / "skills"
            skills.mkdir(parents=True)
            (skills / ".bundled_manifest").write_text(
                "pdf:abc\nplan:bcd\ncomputer-use:cde\n"
                "github-pr-workflow:def\napple-notes:ghi\nhermes-agent:jkl\n",
                encoding="utf-8",
            )
            (root / "config.yaml").write_text(
                "skills:\n  disabled: [user-disabled]\n",
                encoding="utf-8",
            )

            manager = AgentManager(
                root_profile=root,
                profiles_root=Path(temp_dir) / "profiles",
                legacy_agents_root=Path(temp_dir) / "legacy",
            )
            config = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))

            self.assertEqual(
                config["skills"]["disabled"],
                [
                    "apple-notes",
                    "computer-use",
                    "github-pr-workflow",
                    "hermes-agent",
                    "user-disabled",
                ],
            )
            self.assertNotIn("pdf", config["skills"]["disabled"])
            self.assertNotIn("plan", config["skills"]["disabled"])
            self.assertTrue(config["xnobrain"]["default_skills_initialized"])
            self.assertTrue(any((root / "snapshots" / "config").glob("*.yaml")))

            # A later user choice survives future manager starts.
            config["skills"]["disabled"].remove("github-pr-workflow")
            (root / "config.yaml").write_text(
                yaml.safe_dump(config, sort_keys=False),
                encoding="utf-8",
            )
            manager = AgentManager(
                root_profile=root,
                profiles_root=Path(temp_dir) / "profiles",
                legacy_agents_root=Path(temp_dir) / "legacy",
            )
            config = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
            self.assertNotIn("github-pr-workflow", config["skills"]["disabled"])

    def test_global_catalog_includes_skills_bundled_with_hermes(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile = root / "home" / ".hermes"
            bundled = root / "hermes-install" / "skills" / "research"
            profile.mkdir(parents=True)
            bundled.mkdir(parents=True)
            (bundled / "SKILL.md").write_text(
                "---\nname: research\ndescription: Bundled research skill\n---\n",
                encoding="utf-8",
            )
            with patch.dict(
                "os.environ",
                {
                    "HERMES_INSTALL_DIR": str(root / "hermes-install"),
                    "RUNTIME_INCLUDE_PACKAGED_SKILLS": "true",
                },
            ):
                skills = GlobalConfigManager(root_profile=profile).list_skills()["skills"]

        self.assertEqual([item["skill_id"] for item in skills], ["research"])

    def test_hermes_exit_zero_provider_error_is_detected(self) -> None:
        message = 'HTTP 401: [codex/gpt-5.5] [401]: {"error": {"code": "token_invalidated"}}'

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

        selected = normalize_llm_router_config(config, "cx/gpt-5.4")

        self.assertEqual(selected, "cx/gpt-5.4")
        self.assertEqual(config["model"]["provider"], LLM_ROUTER_PROVIDER)
        self.assertEqual(config["model"]["base_url"], LLM_ROUTER_API_BASE_URL)
        self.assertEqual(list(config["providers"]), ["xnobrain"])
        self.assertEqual(config["providers"]["xnobrain"]["model"], "cx/gpt-5.4")
        self.assertNotIn("fallback_providers", config)
        self.assertEqual(config["agent"]["reasoning_effort"], "high")

    def test_router_headers_use_provisioned_workload_identity(self) -> None:
        with patch.dict(
            os.environ,
            {"RUNTIME_LLM_API_KEY": "api-key-test-token", "RUNTIME_LLM_API_KEY_FILE": ""},
        ):
            headers = LLMRouterClient(base_url="https://control.test/llm")._request_headers()
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(headers["Authorization"], "Bearer api-key-test-token")

    def test_normalize_adds_only_the_selected_assignment_scope_header(self) -> None:
        config = {
            "model": {
                "default": "openai/gpt-5",
                "assignment_id": "llma_organization",
            }
        }

        normalize_llm_router_config(config)

        provider = config["providers"]["xnobrain"]
        self.assertEqual(
            provider["extra_headers"],
            {"x-xnobrain-assignment-id": "llma_organization"},
        )
        self.assertNotIn("Authorization", provider["extra_headers"])


class LLMRouterClientTests(unittest.IsolatedAsyncioTestCase):
    def test_agent_config_preserves_provider_auto_scope(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = AgentManager(root_profile=root / "root", profiles_root=root / "profiles")
            created, _ = manager.create_agent({"display_name": "Scoped auto"})
            name = created["name"]
            updated = manager.update_config(name, {"provider": "openai", "model": "auto"})
            self.assertEqual(updated["config"]["provider"], "openai")
            config = manager._read_config(manager._require_profile(name))
            self.assertEqual(config["model"]["selection_provider"], "openai")
            self.assertEqual(config["model"]["provider"], "custom:xnobrain")

    async def test_custom_blend_keeps_its_existing_strategy(self) -> None:
        manager = object.__new__(AgentManager)

        class Router:
            async def model_route_candidates(self, *_args, **_kwargs):
                raise AssertionError("custom blends must use resolve_blend_route")

        manager.llm_router = Router()
        prepared = {"model": "my-blend", "command": ["hermes"]}
        await manager._resolve_prepared_model_route(prepared)
        self.assertEqual(prepared["model"], "my-blend")
        self.assertNotIn("model_fallbacks", prepared)

    def test_auto_fallbacks_use_same_router_and_advance_to_other_models(self) -> None:
        manager = object.__new__(AgentManager)

        class Agent:
            base_url = "http://router:8090/v1"
            api_key = "workload-key"
            api_mode = "chat_completions"

        agent = Agent()
        manager._install_model_fallbacks(
            agent,
            {
                "model_fallbacks": ["openai/small", "openai/stale", "openai/small"],
            },
        )
        self.assertEqual(
            [row["model"] for row in agent._fallback_chain], ["openai/small", "openai/stale"]
        )
        self.assertTrue(all(row["provider"] == "xnobrain" for row in agent._fallback_chain))
        self.assertTrue(
            all(row["base_url"] == "http://router:8090/v1" for row in agent._fallback_chain)
        )
        self.assertEqual(agent._fallback_model["model"], "openai/small")
        self.assertEqual(agent._fallback_index, 0)

    async def test_prepared_auto_is_left_for_gorouter_v017_to_resolve(self) -> None:
        manager = object.__new__(AgentManager)

        class Router:
            async def ensure_auto_combo(self):
                self.checked = True

            async def model_route_candidates(self, *_args, **_kwargs):
                raise AssertionError("Runtime must not duplicate GoRouter auto routing")

        manager.llm_router = Router()
        prepared = {
            "model": "auto",
            "selection_provider": "openai",
            "command": ["hermes", "chat", "--model", "auto", "--quiet"],
        }
        await manager._resolve_prepared_model_route(prepared)
        self.assertTrue(manager.llm_router.checked)
        self.assertEqual(prepared["model"], "auto")
        self.assertNotIn("model_fallbacks", prepared)
        self.assertEqual(prepared["command"], ["hermes", "chat", "--model", "auto", "--quiet"])

    async def test_title_generation_uses_the_configured_v1_router_base_once(self) -> None:
        manager = FakeLLMRouterClient(
            {
                ("POST", "/chat/completions"): {
                    "choices": [{"message": {"content": "Managed routing"}}],
                },
            }
        )

        title = await manager.generate_conversation_title("hello", "openai/gpt-5")

        self.assertEqual(title, "Managed routing")
        self.assertEqual(manager.requests[0][0:2], ("POST", "/chat/completions"))

    async def test_runtime_catalog_uses_only_the_workload_models_endpoint(self) -> None:
        manager = FakeLLMRouterClient(
            {
                ("GET", "/models?kind=llm"): {
                    "data": [
                        {
                            "id": "openai/gpt-5",
                            "owned_by": "openai",
                            "capabilities": {"reasoning": ["low", "high"]},
                        }
                    ],
                },
            }
        )

        models = await manager.list_models()

        self.assertEqual([item["id"] for item in models["data"]], ["auto", "openai/gpt-5"])
        self.assertEqual(manager.requests, [("GET", "/models?kind=llm", None)])

    async def test_transport_rejects_router_management_paths(self) -> None:
        manager = LLMRouterClient(base_url="https://router.invalid")

        with self.assertRaises(LLMRouterAPIError):
            await manager._request("GET", "/api/providers")

    async def test_stop_interrupts_the_running_hermes_agent(self) -> None:
        manager = object.__new__(AgentManager)
        manager._active_runs = {}
        manager._stopped_runs = set()
        manager._active_agent_counts = {}
        manager._registry_lock = threading.Lock()

        started = asyncio.Event()
        interrupted = asyncio.Event()
        loop = asyncio.get_running_loop()

        class Agent:
            def interrupt(self, message: str) -> None:
                self.message = message
                loop.call_soon_threadsafe(interrupted.set)

        agent = Agent()

        async def run_session_agent(
            _prepared,
            *,
            run_id,
            stream_delta_callback,
            tool_progress_callback,
            approval_notify_callback,
            agent_ref,
        ):
            agent_ref[0] = agent
            started.set()
            await interrupted.wait()
            return (
                {"final_response": "", "interrupted": True, "messages": []},
                {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            )

        run_id = "run_" + "a" * 32
        prepared = {
            "name": "news",
            "profile_dir": Path("/tmp/profile"),
            "workspace_dir": Path("/tmp/workspace"),
            "conversation_id": "session-one",
            "message": "Run until stopped",
            "model": "cx/test",
            "timeout_seconds": 86400,
            "run_id": run_id,
        }

        with (
            patch.object(manager, "_run_session_agent", side_effect=run_session_agent),
            patch.object(manager, "_conversation_has_default_title", return_value=False),
        ):
            stream = manager._chat_stream_events(prepared)
            first = await anext(stream)
            self.assertIn(b'"event":"run.started"', first)
            await asyncio.wait_for(started.wait(), timeout=1)

            stopped = await manager.stop_run(run_id)
            payload = b"".join([event async for event in stream])

        self.assertEqual(stopped["status"], "stopping")
        self.assertEqual(agent.message, "run stopped by user")
        self.assertIn(b'"event":"run.cancelled"', payload)
        self.assertNotIn(b'"event":"run.completed"', payload)

    async def test_smart_route_classifies_delegated_tasks_independently(self) -> None:
        class Router:
            async def resolve_smart_route(
                self,
                name,
                message,
                *,
                required_context_tokens=0,
            ):
                await asyncio.sleep(0)
                difficult = "architecture" in message.lower()
                return {
                    "model": "cx/deep" if difficult else "cx/fast",
                    "reasoning": "high" if difficult else "low",
                    "tier": "difficult" if difficult else "quick",
                    "route": name,
                }

        manager = object.__new__(AgentManager)
        manager.llm_router = Router()
        loop = asyncio.get_running_loop()

        decisions = await asyncio.to_thread(
            manager._resolve_smart_route_batch_from_worker,
            loop,
            threading.get_ident(),
            "smart",
            [
                {"goal": "Format this JSON"},
                {"goal": "Review the system architecture"},
            ],
        )

        self.assertEqual([item["model"] for item in decisions], ["cx/fast", "cx/deep"])
        self.assertEqual([item["tier"] for item in decisions], ["quick", "difficult"])

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
                yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))["model"][
                    "default"
                ],
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

    async def test_manual_compaction_updates_context_without_changing_session_id(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = AgentManager(
                root_profile=root / "root",
                profiles_root=root / "profiles",
                legacy_agents_root=root / "legacy",
            )
            manager.create_agent({"name": "news"})
            conversation = manager.create_conversation("news", {"title": "Research"})
            conversation_id = conversation["conversation"]["id"]
            compacted = {
                "conversation_id": conversation_id,
                "before_tokens": 84_000,
                "after_tokens": 29_000,
                "messages_before": 18,
                "messages_after": 6,
                "focus": "API decisions",
                "in_place": True,
                "model": "cx/gpt-5.6-luna",
                "context_limit": 200_000,
                "context_threshold": 100_000,
                "auto_compaction": True,
            }

            with patch.object(
                manager, "_compact_conversation_sync", return_value=compacted
            ) as worker:
                result = await manager.compact_conversation(
                    "news",
                    conversation_id,
                    focus="API decisions",
                )

            self.assertEqual(result, compacted)
            worker.assert_called_once()
            stored = manager.get_conversation("news", conversation_id)["conversation"]
            self.assertEqual(stored["id"], conversation_id)
            self.assertEqual(
                stored["model_config"]["xnobrain_context"],
                {
                    "used": 29_000,
                    "limit": 200_000,
                    "threshold": 100_000,
                    "auto_compaction": True,
                    "model": "cx/gpt-5.6-luna",
                },
            )

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
                {"title": "New Session"},
            )
            third = manager.create_conversation(
                "news",
                {"title": "new session"},
            )
            custom = manager.create_conversation(
                "news",
                {"title": "Daily Briefing"},
            )

            self.assertEqual(first["conversation"]["title"], "New Session")
            self.assertEqual(second["conversation"]["title"], "New Session 2")
            self.assertEqual(third["conversation"]["title"], "New Session 3")
            self.assertEqual(custom["conversation"]["title"], "Daily Briefing")

            manager.delete_conversation(
                "news",
                second["conversation"]["id"],
            )
            fourth = manager.create_conversation("news", {})
            self.assertEqual(fourth["conversation"]["title"], "New Session 4")

            titles = [item["title"] for item in manager.list_conversations("news")["conversations"]]
            self.assertEqual(len(titles), len(set(titles)))

            manager.create_agent({"name": "research"})
            research = manager.create_conversation("research", {})
            self.assertEqual(
                research["conversation"]["title"],
                "New Session",
            )

    def test_conversations_are_paged_by_most_recent_activity(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = AgentManager(
                root_profile=root / "root",
                profiles_root=root / "profiles",
                legacy_agents_root=root / "legacy",
            )
            manager.create_agent({"name": "news"})
            first = manager.create_conversation("news", {"title": "First"})["conversation"]
            second = manager.create_conversation("news", {"title": "Second"})["conversation"]
            third = manager.create_conversation("news", {"title": "Third"})["conversation"]

            database = manager._profile_dir("news") / "state.db"
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "UPDATE sessions SET started_at = 1, ended_at = 30 WHERE id = ?", (first["id"],)
                )
                connection.execute(
                    "UPDATE sessions SET started_at = 2, ended_at = 20 WHERE id = ?",
                    (second["id"],),
                )
                connection.execute(
                    "UPDATE sessions SET started_at = 3, ended_at = 10 WHERE id = ?", (third["id"],)
                )

            page_one = manager.list_conversations("news", {"page": 1, "limit": 2})
            page_two = manager.list_conversations("news", {"page": 2, "limit": 2})

            self.assertEqual(
                [item["id"] for item in page_one["conversations"]], [first["id"], second["id"]]
            )
            self.assertEqual(page_one["pagination"], {"page": 1, "limit": 2, "has_more": True})
            self.assertEqual([item["id"] for item in page_two["conversations"]], [third["id"]])
            self.assertEqual(page_two["pagination"], {"page": 2, "limit": 2, "has_more": False})

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
                side_effect=["New Session", real_next_title],
            ):
                second = manager.create_conversation("news", {})

            self.assertEqual(second["conversation"]["title"], "New Session 2")
            sessions = manager.list_conversations("news")["conversations"]
            self.assertEqual(len(sessions), 2)
            self.assertTrue(all(item["title"] for item in sessions))

    def test_default_conversation_gets_a_unique_title_after_first_message(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = AgentManager(
                root_profile=root / "root",
                profiles_root=root / "profiles",
                legacy_agents_root=root / "legacy",
            )
            manager.create_agent({"name": "news"})
            first = manager.create_conversation("news", {})["conversation"]
            second = manager.create_conversation("news", {})["conversation"]
            custom = manager.create_conversation("news", {"title": "Pinned title"})["conversation"]
            profile_dir = manager._profile_dir("news")

            first_title = manager._auto_title_conversation(
                profile_dir,
                first["id"],
                "Please summarize today's market news and key risks.",
            )
            second_title = manager._auto_title_conversation(
                profile_dir,
                second["id"],
                "Please summarize today's market news and key risks.",
            )
            custom_title = manager._auto_title_conversation(
                profile_dir,
                custom["id"],
                "This must not replace a manual title.",
            )

            self.assertEqual(first_title, "Please summarize today's market news and key…")
            self.assertEqual(second_title, "Please summarize today's market news and key… 2")
            self.assertEqual(custom_title, "Pinned title")
            self.assertEqual(
                manager.get_conversation("news", first["id"])["conversation"]["title"],
                first_title,
            )

    def test_generated_conversation_title_is_clean_short_and_unique(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = AgentManager(
                root_profile=root / "root",
                profiles_root=root / "profiles",
                legacy_agents_root=root / "legacy",
            )
            manager.create_agent({"name": "news"})
            first = manager.create_conversation("news", {})["conversation"]
            second = manager.create_conversation("news", {})["conversation"]
            profile_dir = manager._profile_dir("news")

            first_title = manager._auto_title_conversation(
                profile_dir,
                first["id"],
                "A long first prompt used only as fallback",
                suggested_title='Title: "Iran US News Update."',
            )
            second_title = manager._auto_title_conversation(
                profile_dir,
                second["id"],
                "A long first prompt used only as fallback",
                suggested_title='Title: "Iran US News Update."',
            )

            self.assertEqual(first_title, "Iran US News Update")
            self.assertEqual(second_title, "Iran US News Update 2")

    async def test_title_summary_falls_back_when_router_fails(self) -> None:
        manager = AgentManager()
        with patch.object(
            manager.llm_router,
            "generate_conversation_title",
            side_effect=LLMRouterAPIError("offline"),
        ):
            title = await manager._summarize_conversation_title(
                "Please summarize today's market news and key risks.",
                "auto",
            )

        self.assertEqual(title, "Please summarize today's market news and key…")

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
                self.assertIn("news", manager.active_agent_ids())
                self.assertRegex(run_id, r"^run_[0-9a-f]{32}$")
                child = SimpleNamespace(
                    _subagent_id="sa-test",
                    session_api_calls=1,
                    session_input_tokens=80,
                    session_output_tokens=20,
                    session_reasoning_tokens=4,
                )
                agent_ref[0] = SimpleNamespace(
                    _active_children=[child],
                    _active_children_lock=None,
                )
                tool_progress_callback(
                    "tool.started",
                    "mcp__news__search",
                    "latest headlines",
                    {"query": "latest headlines"},
                )
                tool_progress_callback(
                    "reasoning.available",
                    "_thinking",
                    "First second",
                    None,
                )
                tool_progress_callback(
                    "reasoning.delta",
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
                tool_progress_callback(
                    "tool.started",
                    "todo",
                    "planning 2 task(s)",
                    {"todos": [{"id": "private-args", "content": "not streamed"}]},
                )
                tool_progress_callback(
                    "tool.completed",
                    "todo",
                    None,
                    None,
                    duration=0.01,
                    is_error=False,
                    result=json.dumps(
                        {
                            "todos": [
                                {
                                    "id": "research",
                                    "content": "Research sources",
                                    "status": "completed",
                                },
                                {
                                    "id": "summary",
                                    "content": "Summarize findings",
                                    "status": "in_progress",
                                },
                            ],
                            "summary": {"total": 2},
                        }
                    ),
                )
                delegation_args = {
                    "tasks": [
                        {"goal": "Research the current implementation in detail."},
                        {"goal": "Verify the proposed behavior with focused tests."},
                    ],
                }
                delegation_result = json.dumps(
                    {
                        "results": [
                            {
                                "task_index": 0,
                                "status": "completed",
                                "summary": "Found it.",
                                "live_transcript": "/private/task-0.log",
                            },
                            {"task_index": 1, "status": "completed", "summary": "Verified it."},
                        ],
                        "total_duration_seconds": 1.25,
                        "live_transcripts": ["/private/task-0.log"],
                    }
                )
                tool_progress_callback(
                    "tool.started",
                    "delegate_task",
                    "delegating 2 tasks",
                    delegation_args,
                )
                worker_context = {
                    "task_index": 0,
                    "task_count": 2,
                    "concurrency": 3,
                    "goal": "Research the current implementation in detail.",
                    "subagent_id": "sa-test",
                }
                tool_progress_callback(
                    "subagent.queued",
                    None,
                    None,
                    None,
                    **worker_context,
                    queue_position=1,
                )
                tool_progress_callback(
                    "subagent.start",
                    None,
                    "Starting research",
                    None,
                    **worker_context,
                )
                tool_progress_callback(
                    "subagent.tool",
                    "web_search",
                    "ICML proceedings",
                    None,
                    **worker_context,
                    tool_count=1,
                )
                tool_progress_callback(
                    "subagent.text",
                    None,
                    "Drafting the report.",
                    None,
                    **worker_context,
                )
                tool_progress_callback(
                    "subagent.complete",
                    None,
                    None,
                    None,
                    **worker_context,
                    status="completed",
                    duration_seconds=1.2,
                    summary="Research complete.",
                    input_tokens=100,
                    output_tokens=25,
                    reasoning_tokens=5,
                    api_calls=2,
                    files_read=["/workspace/source.md"],
                    files_written=["/workspace/report.pdf"],
                    transcript_path="/private/worker-transcript.jsonl",
                )
                tool_progress_callback(
                    "tool.completed",
                    "delegate_task",
                    None,
                    None,
                    duration=1.25,
                    is_error=False,
                    result=delegation_result,
                )
                approval_notify_callback(
                    {
                        "command": "rm -rf ./cache",
                        "description": "Delete the cache directory",
                        "pattern_keys": ["rm_recursive"],
                        "allow_permanent": False,
                    }
                )
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
                chunks = [event async for event in manager._chat_stream_events(prepared)]

            self.assertNotIn("news", manager.active_agent_ids())

            payload = b"".join(chunks)
            self.assertEqual(payload.count(b'"event":"message.delta"'), 2)
            self.assertIn(b'"event":"tool.started"', payload)
            self.assertIn(b'"tool":"mcp__news__search"', payload)
            self.assertIn(b'"event":"reasoning.delta"', payload)
            self.assertIn(b'"delta":"Review the search results."', payload)
            self.assertNotIn(b'"event":"reasoning.available"', payload)
            self.assertIn(b'"event":"tool.completed"', payload)
            self.assertIn(b'"duration":0.125', payload)
            self.assertNotIn(b"private tool output", payload)
            self.assertNotIn(b"private-args", payload)
            self.assertIn(b'"tool":"delegate_task"', payload)
            self.assertIn(b'"args":{"tasks"', payload)
            self.assertIn(b"Research the current implementation", payload)
            self.assertIn(b'"event":"delegation.worker.queued"', payload)
            self.assertIn(b'"event":"delegation.worker.started"', payload)
            self.assertIn(b'"event":"delegation.worker.activity"', payload)
            self.assertIn(b'"tool":"web_search"', payload)
            self.assertIn(b'"input_tokens":80', payload)
            self.assertIn(b'"output_tokens":20', payload)
            self.assertIn(b'"event":"delegation.worker.text"', payload)
            self.assertIn(b'"delta":"Drafting the report."', payload)
            self.assertIn(b'"event":"delegation.worker.completed"', payload)
            self.assertIn(b'"input_tokens":100', payload)
            self.assertIn(b'"api_calls":2', payload)
            self.assertIn(b'"files_written":["/workspace/report.pdf"]', payload)
            self.assertNotIn(b"worker-transcript.jsonl", payload)
            self.assertIn(b'"output":"{\\"results\\"', payload)
            self.assertNotIn(b"/private/task-0.log", payload)
            self.assertIn(b'"event":"todo.updated"', payload)
            self.assertIn(b'"content":"Summarize findings"', payload)
            self.assertIn(b'"in_progress":1', payload)
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
            reasoning_events: list[tuple[Any, ...]] = []
            delegation_events: list[tuple[Any, ...]] = []

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

                def _create_agent(self, **_kwargs):
                    return type(
                        "FakeAgent",
                        (),
                        {
                            "model": "cx/gpt-5.6-luna",
                            "tool_progress_callback": staticmethod(
                                _kwargs.get("tool_progress_callback")
                            ),
                            "context_compressor": type(
                                "FakeCompressor",
                                (),
                                {
                                    "last_prompt_tokens": 10_000,
                                    "context_length": 200_000,
                                    "threshold_tokens": 100_000,
                                },
                            )(),
                            "compression_enabled": True,
                            "run_conversation": lambda self, *_args, **_kwargs: {},
                        },
                    )()

                async def _run_agent(self, **kwargs):
                    observed.update(kwargs)
                    agent = self._create_agent(
                        tool_progress_callback=kwargs["tool_progress_callback"],
                    )
                    kwargs["agent_ref"][0] = agent
                    observed["delegated"] = agent._dispatch_delegate_task(
                        {
                            "tasks": [
                                {
                                    "goal": "Inspect the implementation thoroughly.",
                                    "acp_command": "hidden-provider-command",
                                }
                            ],
                        }
                    )
                    observed["queued_delegated"] = agent._dispatch_delegate_task(
                        {
                            "tasks": [
                                {"goal": f"Research conference task number {index} thoroughly."}
                                for index in range(5)
                            ],
                        }
                    )
                    agent.reasoning_callback("I checked the saved context.")

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
            with (
                patch(
                    "gateway.platforms.api_server.APIServerAdapter",
                    FakeSessionAdapter,
                ),
                patch(
                    "tools.delegate_tool.delegate_task",
                    side_effect=[
                        '{"results":[]}',
                        json.dumps(
                            {
                                "results": [
                                    {
                                        "task_index": index,
                                        "status": "completed",
                                        "summary": f"wave one {index}",
                                    }
                                    for index in range(3)
                                ]
                            }
                        ),
                        json.dumps(
                            {
                                "results": [
                                    {
                                        "task_index": index,
                                        "status": "completed",
                                        "summary": f"wave two {index}",
                                    }
                                    for index in range(2)
                                ]
                            }
                        ),
                    ],
                ) as delegate,
                patch("tools.delegate_tool._get_max_concurrent_children", return_value=3),
            ):
                result, usage = await manager._run_session_agent(
                    prepared,
                    run_id="run_" + "a" * 32,
                    stream_delta_callback=deltas.append,
                    tool_progress_callback=lambda *args, **_kwargs: (
                        reasoning_events
                        if args and args[0] == "reasoning.delta"
                        else delegation_events
                    ).append(args),
                    approval_notify_callback=lambda _data: None,
                    agent_ref=[None],
                )

            delegated = observed.get("delegated")
            queued_delegated = json.loads(observed["queued_delegated"])

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
            self.assertEqual(
                reasoning_events,
                [("reasoning.delta", "_thinking", "I checked the saved context.", None)],
            )
            self.assertEqual(deltas, ["I remember."])
            self.assertEqual(delegated, '{"results":[]}')
            self.assertEqual(delegate.call_count, 3)
            delegate_kwargs = delegate.call_args_list[0].kwargs
            self.assertFalse(delegate_kwargs["background"])
            self.assertEqual(
                delegate_kwargs["tasks"],
                [{"goal": "Inspect the implementation thoroughly."}],
            )
            self.assertEqual(
                [len(call.kwargs["tasks"]) for call in delegate.call_args_list[1:]], [3, 2]
            )
            self.assertEqual(
                [item["task_index"] for item in queued_delegated["results"]], [0, 1, 2, 3, 4]
            )
            self.assertEqual(queued_delegated["concurrency"], 3)
            self.assertEqual(len(delegation_events), 5)
            self.assertEqual(
                [event[0] for event in delegation_events],
                ["subagent.queued"] * 5,
            )
            self.assertEqual(result["final_response"], "I remember.")
            self.assertEqual(usage["total_tokens"], 5)
            self.assertEqual(usage["context_used"], 10_000)
            self.assertEqual(usage["context_limit"], 200_000)
            stored = manager.get_conversation("news", conversation_id)
            self.assertEqual(
                stored["conversation"]["model_config"]["xnobrain_context"],
                {
                    "used": 10_000,
                    "limit": 200_000,
                    "threshold": 100_000,
                    "auto_compaction": True,
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
                def run_conversation(self, **run_kwargs):
                    from tools import terminal_tool

                    task_id = str(run_kwargs["task_id"])
                    observed["session_cwd"] = terminal_tool.get_session_cwd(task_id)
                    observed["task_cwd"] = terminal_tool.resolve_task_overrides(task_id).get("cwd")
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
                        result = agent.run_conversation(task_id=kwargs["session_id"])
                        observed["callback_restored"] = (
                            terminal_tool._get_approval_callback() is None
                        )
                        observed["workspace_scope_cleared"] = terminal_tool.get_session_cwd(
                            kwargs["session_id"]
                        ) is None and not terminal_tool.resolve_task_overrides(kwargs["session_id"])
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
            self.assertEqual(observed["session_cwd"], str(prepared["workspace_dir"]))
            self.assertEqual(observed["task_cwd"], str(prepared["workspace_dir"]))
            self.assertTrue(observed["workspace_scope_cleared"])
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
                    result=json.dumps(
                        {
                            "success": True,
                            "staged": True,
                            "pending_id": "pending-1",
                        }
                    ),
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
                payload = b"".join([event async for event in manager._chat_stream_events(prepared)])

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
                    raise LLMRouterAPIError(
                        "connect at least one provider before using auto",
                        code="provider_connection_required",
                        status=409,
                    )

            manager.llm_router = EmptyRouter()
            prepared = {
                "profile_dir": root / "profile",
                "workspace_dir": root / "workspace",
                "command": ["hermes"],
                "timeout_seconds": 1,
                "conversation_id": "proof-session",
                "model": "auto",
                "engine": "xnobrain",
            }

            payload = b"".join([event async for event in manager._chat_stream_events(prepared)])
            self.assertIn(b"event: error", payload)
            self.assertIn(b"connect at least one provider", payload)
            self.assertTrue(payload.endswith(b"data: [DONE]\n\n"))


if __name__ == "__main__":
    unittest.main()


class RuntimeRouterKeyFileTests(unittest.TestCase):
    def test_transport_refreshes_runtime_key_from_private_file(self):
        from xnobrain.integrations.llm_router import LLMRouterClient

        with TemporaryDirectory() as temp_dir:
            key_file = Path(temp_dir) / "router-key"
            key_file.write_text("rotated-key\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {"RUNTIME_LLM_API_KEY": "stale", "RUNTIME_LLM_API_KEY_FILE": str(key_file)},
            ):
                client = LLMRouterClient(data_dir=temp_dir)
                self.assertEqual(client._request_headers()["Authorization"], "Bearer rotated-key")
                self.assertEqual(os.environ["RUNTIME_LLM_API_KEY"], "rotated-key")
