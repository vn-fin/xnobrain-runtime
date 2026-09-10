"""No-network regressions for workspace keys in goal/background profile scopes."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
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
