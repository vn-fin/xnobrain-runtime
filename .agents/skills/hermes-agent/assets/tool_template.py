#!/usr/bin/env python3
"""{{TOOL_NAME}} tool — {{DESCRIPTION}}

Drop this file in ``.tools/hermes-agent/tools/`` (convention:
``{{TOOL_NAME}}_tool.py``). It self-registers at import time; Hermes'
``discover_builtin_tools()`` picks it up automatically because it contains a
top-level ``registry.register(...)`` call.

Contract recap:
  - handler is called as ``handler(args_dict, **context_kwargs)``.
  - ``args_dict`` matches the JSON schema ``parameters`` (use .get with defaults).
  - context kwargs include: task_id, session_id, enabled_tools, tool_call_id.
  - the handler MUST return a ``str`` (JSON-encode structured results).
  - return ``tool_error("...")`` for failures.
"""

import json

from tools.registry import registry, tool_error

# Optional: gate availability on an env var / backend so the tool is HIDDEN
# (not broken) when unusable. Delete if the tool is always available.
from utils import env_var_enabled


def {{TOOL_NAME}}(text: str, task_id: str | None = None) -> str:
    """Core logic — a plain function, unit-testable without the registry."""
    text = (text or "").strip()
    if not text:
        return tool_error("`text` is required.")
    result = {"echo": text, "task_id": task_id}
    return json.dumps(result, ensure_ascii=False)


def check_{{TOOL_NAME}}_requirements() -> bool:
    """Return True when this tool can run in the current environment."""
    # Example gate — replace with your real check, or delete and drop check_fn.
    return env_var_enabled("ENABLE_{{TOOL_NAME_UPPER}}")


{{TOOL_NAME_UPPER}}_SCHEMA = {
    "name": "{{TOOL_NAME}}",
    "description": (
        # Write this FOR THE MODEL: when to use it, inputs, and output shape.
        "{{DESCRIPTION}} "
        "Returns JSON {\"echo\": <text>, \"task_id\": <id>}."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The text to echo back.",
            },
        },
        "required": ["text"],
    },
}


registry.register(
    name="{{TOOL_NAME}}",
    toolset="{{TOOLSET}}",
    schema={{TOOL_NAME_UPPER}}_SCHEMA,
    handler=lambda args, **kw: {{TOOL_NAME}}(
        text=args.get("text", ""),
        task_id=kw.get("task_id"),
    ),
    check_fn=check_{{TOOL_NAME}}_requirements,  # delete this line if always available
    emoji="{{EMOJI}}",
)
