"""No-network regressions for workspace keys in goal/background profile scopes."""

import os
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from agent.secret_scope import (
    current_secret_scope,
    get_secret,
    is_multiplex_active,
    set_multiplex_active,
)

from xnobrain.integrations.conversation_credentials import conversation_profile_scope
from xnobrain.integrations.llm_router_support import LLM_ROUTER_KEY_ENV


class ConversationCredentialsTests(TestCase):
    def test_workload_key_visible_in_goal_scope_without_global_secret_leak(self):
        previous_mode = is_multiplex_active()
        previous_scope = current_secret_scope()
        with TemporaryDirectory() as directory:
            profile = Path(directory)
            (profile / ".env").write_text("PROFILE_LOCAL_KEY=own-profile\n", encoding="utf-8")
            try:
                set_multiplex_active(True)
                with patch.dict(
                    os.environ,
                    {
                        "RUNTIME_LLM_API_KEY": "synthetic-workload",
                        "RUNTIME_LLM_API_KEY_FILE": "",
                        "FOREIGN_PROVIDER_KEY": "must-not-leak",
                        "CONTROL_MANAGEMENT_KEY": "must-not-leak",
                    },
                ):
                    with conversation_profile_scope(profile):
                        self.assertEqual(get_secret(LLM_ROUTER_KEY_ENV), "synthetic-workload")
                        self.assertEqual(get_secret("PROFILE_LOCAL_KEY"), "own-profile")
                        self.assertIsNone(get_secret("FOREIGN_PROVIDER_KEY"))
                        self.assertIsNone(get_secret("CONTROL_MANAGEMENT_KEY"))
                self.assertIs(current_secret_scope(), previous_scope)
            finally:
                set_multiplex_active(previous_mode)

    def test_rotated_mounted_key_is_read_without_writing_profile_or_environment(self):
        with TemporaryDirectory() as directory:
            profile = Path(directory) / "profile"
            profile.mkdir()
            key_file = Path(directory) / "key"
            key_file.write_text("synthetic-file-key\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "RUNTIME_LLM_API_KEY": "synthetic-old-key",
                    "RUNTIME_LLM_API_KEY_FILE": str(key_file),
                },
            ):
                with conversation_profile_scope(profile):
                    self.assertEqual(get_secret(LLM_ROUTER_KEY_ENV), "synthetic-file-key")
                self.assertEqual(os.environ["RUNTIME_LLM_API_KEY"], "synthetic-old-key")
            self.assertFalse((profile / ".env").exists())

    def test_missing_key_does_not_borrow_another_profiles_process_key(self):
        previous_mode = is_multiplex_active()
        try:
            set_multiplex_active(True)
            with (
                TemporaryDirectory() as directory,
                patch.dict(
                    os.environ,
                    {
                        "RUNTIME_LLM_API_KEY": "",
                        "RUNTIME_LLM_API_KEY_FILE": "",
                        "OPENAI_API_KEY": "foreign-profile",
                    },
                ),
            ):
                with conversation_profile_scope(Path(directory)):
                    self.assertIsNone(get_secret(LLM_ROUTER_KEY_ENV))
        finally:
            set_multiplex_active(previous_mode)

    def test_goal_model_route_uses_only_scoped_workload_credential(self):
        from xnobrain.integrations.conversation_credentials import conversation_model_route

        with patch.dict(
            os.environ,
            {
                "RUNTIME_LLM_API_KEY": "synthetic-workload",
                "RUNTIME_LLM_API_KEY_FILE": "",
                "CONTROL_MANAGEMENT_KEY": "not-for-inference",
            },
        ):
            route = conversation_model_route("synthetic/model", "https://router.invalid/v1")
        self.assertEqual(
            route,
            {
                "model": "synthetic/model",
                "provider": "custom:xnobrain",
                "base_url": "https://router.invalid/v1",
                "api_key": "synthetic-workload",
            },
        )

    def test_native_background_provider_resolver_sees_workspace_key(self):
        import yaml
        from gateway.run import _resolve_runtime_agent_kwargs

        from xnobrain.integrations.llm_router_support import normalize_llm_router_config

        previous_mode = is_multiplex_active()
        try:
            set_multiplex_active(True)
            with (
                TemporaryDirectory() as directory,
                patch.dict(
                    os.environ,
                    {
                        "RUNTIME_LLM_API_KEY": "synthetic-workload",
                        "RUNTIME_LLM_API_KEY_FILE": "",
                    },
                ),
            ):
                profile = Path(directory)
                config = {}
                normalize_llm_router_config(config, "synthetic/model")
                config["model"]["base_url"] = "https://router.invalid/v1"
                config["providers"]["xnobrain"]["api"] = "https://router.invalid/v1"
                (profile / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
                with conversation_profile_scope(profile):
                    runtime = _resolve_runtime_agent_kwargs()
                self.assertEqual(runtime.get("api_key"), "synthetic-workload")
                self.assertEqual(runtime.get("base_url"), "https://router.invalid/v1")
        finally:
            set_multiplex_active(previous_mode)

    def test_cron_profile_scope_keeps_file_key_after_scheduler_rebuilds_scope(self):
        from agent import secret_scope as secret_scope_module
        from agent.secret_scope import get_secret, set_secret_scope

        from xnobrain.integrations.conversation_credentials import cron_profile_scope

        previous_mode = is_multiplex_active()
        try:
            set_multiplex_active(True)
            with TemporaryDirectory() as directory:
                profile = Path(directory) / "profile"
                profile.mkdir()
                key_file = Path(directory) / "key"
                key_file.write_text("synthetic-cron-file-key\n", encoding="utf-8")
                with patch.dict(
                    os.environ,
                    {
                        "RUNTIME_LLM_API_KEY": "",
                        "RUNTIME_LLM_API_KEY_FILE": str(key_file),
                    },
                ):
                    with cron_profile_scope(profile):
                        rebuilt = secret_scope_module.build_profile_secret_scope(profile)
                        self.assertEqual(rebuilt[LLM_ROUTER_KEY_ENV], "synthetic-cron-file-key")
                        set_secret_scope(rebuilt)
                        self.assertEqual(get_secret(LLM_ROUTER_KEY_ENV), "synthetic-cron-file-key")
        finally:
            set_multiplex_active(previous_mode)


class GoalOffloadTests(IsolatedAsyncioTestCase):
    async def test_goal_call_runs_off_the_event_loop(self):
        from contextlib import nullcontext

        from xnobrain.integrations import conversation_runner

        loop_thread = threading.get_ident()
        with TemporaryDirectory() as directory:
            profile = Path(directory)
            with patch(
                "xnobrain.integrations.conversation_credentials.conversation_profile_scope",
                side_effect=lambda _profile: nullcontext(),
            ):
                worker_thread = await conversation_runner._run_goal_call(
                    profile, threading.get_ident
                )
        self.assertNotEqual(worker_thread, loop_thread)


class HermesProfileScopeThreadContractTests(TestCase):
    """The chat path enters conversation_profile_scope via Hermes' _run_agent.

    That scope refreshes MCP sessions over the network, so Hermes must enter it
    inside the synchronous executor body rather than on the event loop.
    """

    def test_run_agent_enters_profile_scope_inside_the_executor_body(self):
        import ast
        import inspect
        import textwrap

        from gateway.platforms.api_server import APIServerAdapter

        tree = ast.parse(textwrap.dedent(inspect.getsource(APIServerAdapter._run_agent)))
        scoped = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and any(
                isinstance(item, ast.Attribute) and item.attr == "_profile_scope"
                for item in ast.walk(node)
            )
        ]
        self.assertTrue(scoped, "_run_agent no longer enters _profile_scope in a sync helper")
        source = inspect.getsource(APIServerAdapter._run_agent)
        self.assertIn("run_in_executor", source)
        self.assertIn(scoped[0].name, source.split("run_in_executor", 1)[1])
