"""{{PLUGIN_NAME}} plugin — {{DESCRIPTION}}

A directory plugin: this ``__init__.py`` + a ``plugin.yaml`` manifest. Hermes
calls ``register(ctx)`` at load time. Install by placing this folder at
``~/.hermes/plugins/{{PLUGIN_NAME}}/`` (user) or ``./.hermes/plugins/{{PLUGIN_NAME}}/``
(project, opt-in), then ``hermes plugins enable {{PLUGIN_NAME}}``.

Demonstrates the three most common contributions:
  1. a tool      -> ctx.register_tool(...)
  2. a hook      -> ctx.register_hook("post_tool_call", ...)
  3. a command   -> ctx.register_command("{{PLUGIN_NAME}}", ...)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# 1. A tool
# --------------------------------------------------------------------------

def _echo(text: str, task_id: str | None = None) -> str:
    text = (text or "").strip()
    if not text:
        # tool_error lives in tools.registry; import lazily so this module
        # imports cleanly even outside a full Hermes runtime.
        from tools.registry import tool_error
        return tool_error("`text` is required.")
    return json.dumps({"echo": text, "task_id": task_id}, ensure_ascii=False)


_ECHO_SCHEMA = {
    "name": "{{PLUGIN_NAME}}_echo",
    "description": (
        "Echo the given text back as JSON. Use to smoke-test the {{PLUGIN_NAME}} "
        "plugin. Returns {\"echo\": <text>, \"task_id\": <id>}."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to echo."},
        },
        "required": ["text"],
    },
}


# --------------------------------------------------------------------------
# 2. A hook (observe every tool call)
# --------------------------------------------------------------------------

def _on_post_tool_call(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    result: Any = None,
    task_id: str = "",
    session_id: str = "",
    **_: Any,
) -> None:
    """Fires after every tool call. Observe only — keep it fast and never raise."""
    logger.debug("[{{PLUGIN_NAME}}] tool %s ran (task=%s)", tool_name, task_id)


# --------------------------------------------------------------------------
# 3. A slash command
# --------------------------------------------------------------------------

_HELP = """\
/{{PLUGIN_NAME}} — {{DESCRIPTION}}

Subcommands:
  help            Show this help
  ping            Reply 'pong'
"""


def _handle_slash(raw_args: str) -> Optional[str]:
    argv = raw_args.strip().split()
    if not argv or argv[0] in {"help", "-h", "--help"}:
        return _HELP
    if argv[0] == "ping":
        return "pong"
    return f"Unknown subcommand: {argv[0]}\n\n{_HELP}"


# --------------------------------------------------------------------------
# Registration entry point
# --------------------------------------------------------------------------

def register(ctx) -> None:
    ctx.register_tool(
        name="{{PLUGIN_NAME}}_echo",
        toolset="{{PLUGIN_NAME}}",
        schema=_ECHO_SCHEMA,
        handler=lambda args, **kw: _echo(text=args.get("text", ""), task_id=kw.get("task_id")),
        emoji="🔌",
    )
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_command(
        "{{PLUGIN_NAME}}",
        handler=_handle_slash,
        description="{{DESCRIPTION}}",
        args_hint="ping|help",
    )
