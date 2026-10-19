"""Product-facing imports for the embedded engine's code execution sandbox."""

from __future__ import annotations

import ast
import copy
import threading
from functools import wraps

_INSTALL_LOCK = threading.Lock()
_ALIAS_BOOTSTRAP = "__import__('sys').modules['xnobrain_tools'] = __import__('hermes_tools')"


def _with_tool_alias(code: str) -> str:
    """Bind both names to the same session-scoped RPC module in the child only."""
    if not code or not code.strip():
        return code
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        # Keep native syntax-error reporting and empty-input validation intact.
        return code
    insertion = None
    for index, node in enumerate(tree.body):
        is_docstring = (
            index == 0
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
        if is_docstring or (isinstance(node, ast.ImportFrom) and node.module == "__future__"):
            insertion = node
        else:
            break
    if insertion is None:
        return f"{_ALIAS_BOOTSTRAP}\n{code}"
    # AST columns are UTF-8 byte offsets. Insert after docstrings/future imports,
    # including when another statement follows on the same physical line.
    lines = code.split("\n")
    line_index = insertion.end_lineno - 1
    line = lines[line_index].encode("utf-8")
    column = insertion.end_col_offset
    lines[line_index] = (
        line[:column].decode("utf-8") + "; " + _ALIAS_BOOTSTRAP + line[column:].decode("utf-8")
    )
    return "\n".join(lines)


def code_tool_schema(schema: dict) -> dict:
    """Change only the execute_code documentation, retaining its wire contract."""
    result = copy.deepcopy(schema)

    def update(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "description" and isinstance(item, str):
                    value[key] = item.replace("hermes_tools", "xnobrain_tools").replace(
                        "Hermes tools", "XNOBrain tools"
                    )
                else:
                    update(item)
        elif isinstance(value, list):
            for item in value:
                update(item)

    update(result)
    return result


def install_code_tools() -> None:
    """Adapt native execution and both static and per-session tool descriptions."""
    from tools import code_execution_tool as native
    from tools.registry import registry

    with _INSTALL_LOCK:
        if getattr(native.execute_code, "_xnobrain_tool_alias", False):
            return
        execute = native.execute_code
        build_schema = native.build_execute_code_schema

        @wraps(execute)
        def execute_code(code, task_id=None, enabled_tools=None):
            # Native approval, tool allowlists, credentials, transports, limits,
            # and cancellation still own execution. Never change user arguments
            # in the registry/event history or rewrite tool output.
            return execute(
                _with_tool_alias(code) if isinstance(code, str) else code,
                task_id=task_id,
                enabled_tools=enabled_tools,
            )

        @wraps(build_schema)
        def build_execute_code_schema(*args, **kwargs):
            return code_tool_schema(build_schema(*args, **kwargs))

        execute_code._xnobrain_tool_alias = True
        native.execute_code = execute_code
        native.build_execute_code_schema = build_execute_code_schema
        native.EXECUTE_CODE_SCHEMA = code_tool_schema(native.EXECUTE_CODE_SCHEMA)
        entry = registry.get_entry("execute_code")
        if entry is not None:
            entry.schema = code_tool_schema(entry.schema)
            entry.description = entry.schema["description"]
