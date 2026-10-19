"""Exercise product tool imports through the native sandbox and RPC dispatch."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from xnobrain.integrations.code_tools import _with_tool_alias, install_code_tools
from xnobrain.integrations.conversation_prompt import CODE_TOOL_GUIDANCE, ConversationPromptMixin
from xnobrain.integrations.conversation_runner import ConversationRunnerMixin


class CodeToolSourceTests(unittest.TestCase):
    def test_alias_preserves_future_imports_docstrings_and_literals(self):
        sources = (
            '"Tài liệu"; from xnobrain_tools import terminal\nresult = terminal()',
            '"""Tài liệu\nagent"""\nfrom __future__ import annotations\n'
            "from xnobrain_tools import terminal\nresult = terminal()",
            "from __future__ import (\nannotations,\n); "
            "from xnobrain_tools import terminal\nresult = terminal()",
            'import importlib\nresult = importlib.import_module("xnobrain_tools").terminal()',
        )
        native = ModuleType("hermes_tools")
        native.terminal = lambda: "ok"
        for source in sources:
            with self.subTest(source=source), patch.dict(sys.modules, {"hermes_tools": native}):
                scope = {}
                exec(compile(_with_tool_alias(source), "script.py", "exec"), scope)
                self.assertEqual(scope["result"], "ok")
                self.assertIs(sys.modules["xnobrain_tools"], native)
                if source.startswith('"Tài liệu"'):
                    self.assertEqual(scope["__doc__"], "Tài liệu")

    def test_empty_and_invalid_source_keep_native_validation(self):
        for source in ("", "  ", "from xnobrain_tools import (", "\x00"):
            self.assertEqual(_with_tool_alias(source), source)

    def test_prompt_updates_new_scripts_without_rewriting_prior_content(self):
        manager = ConversationPromptMixin()
        prior = "Example from history: from hermes_tools import terminal"
        prompt = manager._format_runtime_prompt(prior)
        self.assertIn(prior, prompt)
        self.assertIn(CODE_TOOL_GUIDANCE, prompt)
        self.assertEqual(manager._format_runtime_prompt(prompt), prompt)

    def test_wire_schema_updates_an_already_initialized_agent(self):
        schema = {
            "type": "function",
            "function": {
                "name": "execute_code",
                "description": "Call Hermes tools via hermes_tools.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "from hermes_tools import terminal",
                        },
                    },
                },
            },
        }
        agent = SimpleNamespace(_build_api_kwargs=lambda messages: {"tools": [schema]})
        ConversationRunnerMixin._install_provider_runtime_request_guard(agent)
        rendered = json.dumps(agent._build_api_kwargs([])["tools"])
        self.assertIn("xnobrain_tools", rendered)
        self.assertNotIn("hermes_tools", rendered)
        self.assertIn("hermes_tools", json.dumps(schema))


class CodeToolSandboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_code_tools()
        from tools import code_execution_tool
        from tools.registry import registry

        cls.native = code_execution_tool
        cls.registry = registry

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(self.native, "SANDBOX_AVAILABLE", True))
        self.stack.enter_context(
            patch.object(
                self.native, "_load_config", return_value={"timeout": 5, "max_tool_calls": 2}
            )
        )
        self.stack.enter_context(
            patch.object(self.native, "_get_execution_mode", return_value="strict")
        )
        self.stack.enter_context(
            patch("tools.terminal_tool._get_env_config", return_value={"env_type": "local"})
        )
        self.stack.enter_context(
            patch("tools.terminal_tool._docker_has_host_access", return_value=False)
        )
        self.guard = self.stack.enter_context(
            patch("tools.approval.check_execute_code_guard", return_value={"approved": True})
        )
        self.dispatch = self.stack.enter_context(
            patch(
                "model_tools.handle_function_call", return_value=json.dumps({"output": "alias-ok"})
            )
        )

    def execute(self, code, enabled_tools=None):
        args = {"code": code}
        result = self.registry.get_entry("execute_code").handler(
            args, task_id="xno-alias-test", enabled_tools=enabled_tools or ["terminal"]
        )
        self.assertEqual(args, {"code": code})
        return json.loads(result)

    def test_new_and_legacy_import_share_real_local_rpc_connection(self):
        result = self.execute(
            "from xnobrain_tools import terminal\n"
            "import xnobrain_tools, hermes_tools\n"
            "assert xnobrain_tools is hermes_tools\n"
            "print(terminal('synthetic-command')['output'])\n"
            "print(hermes_tools.terminal('legacy-command')['output'])\n"
        )
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(result["tool_calls_made"], 2)
        self.assertEqual(self.dispatch.call_count, 2)
        self.assertEqual(
            self.dispatch.call_args.args,
            ("terminal", {"command": "legacy-command", "timeout": None, "workdir": None}),
        )
        self.assertEqual(self.dispatch.call_args.kwargs["task_id"], "xno-alias-test")

    def test_alias_does_not_expose_disabled_tools(self):
        result = self.execute("from xnobrain_tools import web_search")
        self.assertEqual(result["status"], "error")
        self.dispatch.assert_not_called()

    def test_native_guard_still_blocks_execution(self):
        self.guard.return_value = {"approved": False, "message": "test denial"}
        result = self.execute("from xnobrain_tools import terminal\nterminal('synthetic-command')")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "test denial")
        self.dispatch.assert_not_called()
        self.assertIn("terminal('synthetic-command')", self.guard.call_args.args[0])

    def test_native_tool_call_limit_is_shared_by_both_imports(self):
        result = self.execute(
            "import xnobrain_tools, hermes_tools\n"
            "xnobrain_tools.terminal('one')\n"
            "hermes_tools.terminal('two')\n"
            "print(xnobrain_tools.terminal('three'))\n"
        )
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(self.dispatch.call_count, 2)
        self.assertIn("Tool call limit reached", result["output"])

    def test_alias_keeps_native_timeout(self):
        with patch.object(self.native, "_load_config", return_value={"timeout": 0.1}):
            result = self.execute("import xnobrain_tools, time\ntime.sleep(5)")
        self.assertEqual(result["status"], "timeout", result)
        self.dispatch.assert_not_called()

    def test_new_import_uses_native_remote_file_transport(self):
        # Run the remote protocol in a temporary local fixture, without SSH,
        # Docker nesting, or a real remote infrastructure dependency.
        with TemporaryDirectory() as root:

            def execute(command, cwd=None, timeout=10):
                result = subprocess.run(
                    command,
                    shell=True,
                    cwd=cwd or root,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env={"PATH": os.environ["PATH"]},
                )
                return {"output": result.stdout + result.stderr, "returncode": result.returncode}

            environment = SimpleNamespace(execute=execute, get_temp_dir=lambda: root)
            with (
                patch("tools.terminal_tool._get_env_config", return_value={"env_type": "ssh"}),
                patch.object(self.native, "_get_or_create_env", return_value=(environment, "ssh")),
            ):
                result = self.execute(
                    "from xnobrain_tools import terminal\nprint(terminal('remote')['output'])"
                )
            self.assertEqual(result["status"], "success", result)
            self.assertEqual(result["tool_calls_made"], 1)
            self.assertIn("alias-ok", result["output"])
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_schema_is_product_named_filtered_and_installation_is_idempotent(self):
        execute = self.native.execute_code
        install_code_tools()
        self.assertIs(self.native.execute_code, execute)
        for schema in (
            self.registry.get_entry("execute_code").schema,
            self.native.build_execute_code_schema({"terminal"}),
        ):
            serialized = json.dumps(schema)
            self.assertIn("xnobrain_tools", serialized)
            self.assertNotIn("hermes_tools", serialized)
            self.assertNotIn("Hermes tools", serialized)
            self.assertEqual(schema["name"], "execute_code")
            self.assertEqual(schema["parameters"]["required"], ["code"])
        description = self.native.build_execute_code_schema({"terminal"})["description"]
        self.assertNotIn("web_search(", description)


if __name__ == "__main__":
    unittest.main()
