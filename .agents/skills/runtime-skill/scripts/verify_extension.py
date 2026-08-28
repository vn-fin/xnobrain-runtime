#!/usr/bin/env python3
"""Verify a Hermes tool or plugin actually registers, against the real source.

It puts the Hermes source (``.tools/hermes-agent`` by default) on ``sys.path``,
imports your extension, and reports what got registered. This catches import
errors, missing top-level ``registry.register`` calls, bad hook names, and
handler wiring mistakes BEFORE you run the full agent.

Usage:
    python3 verify_extension.py --tool <tool_name> [--hermes-src DIR]
    python3 verify_extension.py --plugin <plugin_name> [--hermes-src DIR]
    python3 verify_extension.py --tool-file path/to/foo_tool.py
    python3 verify_extension.py --plugin-dir path/to/plugin_folder

Exit code 0 = registered OK, non-zero = a problem was found.

Note: run with the Hermes venv for full fidelity, e.g.
    cd .tools/hermes-agent && uv run python <skill>/scripts/verify_extension.py --tool foo
Plugin checks use a lightweight recording context and need no heavy deps.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import sys
from pathlib import Path

def _default_src() -> Path:
    """Resolve the Hermes source dir, whether run from the repo root or inside it.

    - If the current dir already IS the Hermes source (has run_agent.py), use it.
      This is the recommended ``cd .tools/hermes-agent && uv run python ...`` flow.
    - Otherwise fall back to ``./.tools/hermes-agent`` under the repo root.
    """
    cwd = Path.cwd()
    if (cwd / "run_agent.py").exists() and (cwd / "tools" / "registry.py").exists():
        return cwd
    return cwd / ".tools" / "hermes-agent"


DEFAULT_SRC = _default_src()


def _add_src_to_path(src: Path) -> None:
    if not src.exists():
        print(f"✗ Hermes source not found at {src}. Pass --hermes-src.", file=sys.stderr)
        raise SystemExit(2)
    sys.path.insert(0, str(src))


# ---------------------------------------------------------------------------
# Tool verification — import the module, diff the registry's tool names.
# ---------------------------------------------------------------------------

def verify_tool(module_name: str | None, tool_file: Path | None, src: Path) -> int:
    _add_src_to_path(src)
    try:
        from tools.registry import registry
    except Exception as e:  # pragma: no cover - env dependent
        print(f"✗ could not import tools.registry from {src}: {e}", file=sys.stderr)
        print("  Try running under the Hermes venv: cd .tools/hermes-agent && uv run python ...",
              file=sys.stderr)
        return 2

    before = set(registry.get_all_tool_names()) if hasattr(registry, "get_all_tool_names") else set(_names(registry))

    try:
        if tool_file is not None:
            _import_from_path("hermes_tool_under_test", tool_file)
        else:
            importlib.import_module(module_name if module_name.startswith("tools.")
                                    else f"tools.{module_name}")
    except Exception as e:
        print(f"✗ import failed: {e}", file=sys.stderr)
        return 1

    after = set(registry.get_all_tool_names()) if hasattr(registry, "get_all_tool_names") else set(_names(registry))
    new = sorted(after - before)

    if not new:
        print("✗ no new tool registered. Is there a TOP-LEVEL registry.register(...) call?",
              file=sys.stderr)
        return 1

    print(f"✓ registered tool(s): {', '.join(new)}")
    _report_tool_details(registry, new)
    return 0


def _names(registry) -> list:
    # Fallbacks for slightly different registry APIs across versions.
    for attr in ("_tools", "tools"):
        d = getattr(registry, attr, None)
        if isinstance(d, dict):
            return list(d.keys())
    return []


def _report_tool_details(registry, names) -> None:
    getter = getattr(registry, "get", None) or getattr(registry, "get_tool", None)
    tools = getattr(registry, "_tools", {})
    for n in names:
        entry = tools.get(n) if isinstance(tools, dict) else (getter(n) if getter else None)
        if entry is None:
            continue
        toolset = getattr(entry, "toolset", "?")
        has_check = getattr(entry, "check_fn", None) is not None
        schema = getattr(entry, "schema", {}) or {}
        params = (schema.get("parameters", {}) or {}).get("properties", {})
        print(f"  • {n}: toolset={toolset} check_fn={'yes' if has_check else 'no'} "
              f"params={list(params)}")
        if schema.get("name") and schema["name"] != n:
            print(f"    ! schema name {schema['name']!r} != registered name {n!r}")


# ---------------------------------------------------------------------------
# Plugin verification — run register(ctx) against a recording context.
# ---------------------------------------------------------------------------

class RecordingContext:
    """Minimal stand-in for PluginContext: records what register(ctx) wires."""

    def __init__(self, valid_hooks: set[str] | None):
        self.tools: list[dict] = []
        self.hooks: list[str] = []
        self.commands: list[str] = []
        self.providers: list[str] = []
        self.skills: list[str] = []
        self.problems: list[str] = []
        self._valid_hooks = valid_hooks

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools.append({"name": name, "toolset": toolset})
        if not callable(handler):
            self.problems.append(f"tool {name}: handler is not callable")
        if isinstance(schema, dict) and schema.get("name") and schema["name"] != name:
            self.problems.append(f"tool {name}: schema name {schema['name']!r} != {name!r}")

    def register_hook(self, hook_name, callback):
        self.hooks.append(hook_name)
        if not callable(callback):
            self.problems.append(f"hook {hook_name}: callback is not callable")
        if self._valid_hooks is not None and hook_name not in self._valid_hooks:
            self.problems.append(
                f"hook {hook_name!r} is not in VALID_HOOKS "
                f"(valid: {', '.join(sorted(self._valid_hooks))})")

    def register_command(self, name, handler, description="", args_hint="", **kw):
        self.commands.append(name)
        if not callable(handler):
            self.problems.append(f"command {name}: handler is not callable")

    def register_cli_command(self, *a, **k):
        self.commands.append(f"(cli) {a[0] if a else '?'}")

    def register_skill(self, name, path, description="", **k):
        self.skills.append(name)

    # Any register_*_provider call just gets recorded.
    def __getattr__(self, item):
        if item.startswith("register_"):
            def _rec(*a, **k):
                self.providers.append(item)
            return _rec
        raise AttributeError(item)


def verify_plugin(plugin_name: str | None, plugin_dir: Path | None, src: Path) -> int:
    _add_src_to_path(src)

    if plugin_dir is None:
        plugin_dir = src / "plugins" / plugin_name
    if not plugin_dir.is_dir():
        print(f"✗ plugin dir not found: {plugin_dir}", file=sys.stderr)
        return 2

    init = plugin_dir / "__init__.py"
    manifest = plugin_dir / "plugin.yaml"
    if not init.exists():
        print(f"✗ missing __init__.py in {plugin_dir}", file=sys.stderr)
        return 1
    if not manifest.exists():
        print(f"! no plugin.yaml in {plugin_dir} (required for real discovery)", file=sys.stderr)

    valid_hooks = None
    try:
        from hermes_cli.plugins import VALID_HOOKS  # type: ignore
        valid_hooks = set(VALID_HOOKS)
    except Exception:
        print("! could not import VALID_HOOKS (hook names won't be validated). "
              "Run under the Hermes venv for full checks.", file=sys.stderr)

    try:
        mod = _import_from_path(f"hermes_plugin_{plugin_dir.name}", init)
    except Exception as e:
        print(f"✗ import of {init} failed: {e}", file=sys.stderr)
        return 1

    register = getattr(mod, "register", None)
    if not callable(register):
        print("✗ plugin has no callable register(ctx) function.", file=sys.stderr)
        return 1

    ctx = RecordingContext(valid_hooks)
    try:
        register(ctx)
    except Exception as e:
        print(f"✗ register(ctx) raised: {e}", file=sys.stderr)
        return 1

    print(f"✓ register(ctx) ran for plugin '{plugin_dir.name}'")
    if ctx.tools:
        print(f"  tools:    {', '.join(t['name'] for t in ctx.tools)}")
    if ctx.hooks:
        print(f"  hooks:    {', '.join(ctx.hooks)}")
    if ctx.commands:
        print(f"  commands: {', '.join('/' + c for c in ctx.commands)}")
    if ctx.skills:
        print(f"  skills:   {', '.join(ctx.skills)}")
    if ctx.providers:
        print(f"  providers:{', '.join(ctx.providers)}")
    if not any([ctx.tools, ctx.hooks, ctx.commands, ctx.skills, ctx.providers]):
        print("  ! register(ctx) wired nothing — did you forget the ctx.register_* calls?")

    if ctx.problems:
        print("\n✗ problems:", file=sys.stderr)
        for p in ctx.problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    return 0


def _import_from_path(mod_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--tool", metavar="NAME", help="tool module name under tools/ (without _tool.py)")
    g.add_argument("--tool-file", type=Path, help="path to a tool .py file")
    g.add_argument("--plugin", metavar="NAME", help="plugin folder name under plugins/")
    g.add_argument("--plugin-dir", type=Path, help="path to a plugin folder")
    ap.add_argument("--hermes-src", type=Path, default=DEFAULT_SRC,
                    help="path to Hermes source (default: ./.tools/hermes-agent)")
    args = ap.parse_args()

    src = args.hermes_src.expanduser()

    if args.tool or args.tool_file:
        # Tool module names are stored as tools.<file_stem>; strip a _tool suffix
        # only for the friendly name, but import by the actual file stem.
        mod = None
        if args.tool:
            mod = args.tool if args.tool.startswith("tools.") else f"{args.tool}_tool"
        return verify_tool(mod, args.tool_file, src)

    return verify_plugin(args.plugin, args.plugin_dir, src)


if __name__ == "__main__":
    raise SystemExit(main())
