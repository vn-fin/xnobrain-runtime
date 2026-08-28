#!/usr/bin/env python3
"""Scaffold a new XNOBrain runtime plugin (tool + hook + command).

Generates ``<dest>/<name>/{plugin.yaml, __init__.py}``.

Usage:
    python3 new_plugin.py <plugin_name> [--description "..."] [--dest DIR] [--force]

A destination is required so generated code stays in an XNOBrain-owned extension path.

Examples:
    python3 new_plugin.py my_plugin
    python3 new_plugin.py audit_logger --description "Log every tool call." \\
        --dest extensions/plugins
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = SKILL_DIR / "assets" / "plugin_template"

# Plugin dir names are used as registry keys; keep them filesystem/URL safe.
VALID_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")


def render(text: str, name: str, description: str) -> str:
    return text.replace("{{PLUGIN_NAME}}", name).replace("{{DESCRIPTION}}", description)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="plugin name (e.g. my_plugin)")
    ap.add_argument("--description", default="What this plugin does.")
    ap.add_argument("--dest", type=Path, required=True,
                    help="XNOBrain-owned parent directory for the plugin")
    ap.add_argument("--force", action="store_true", help="overwrite existing files")
    args = ap.parse_args()

    if not VALID_NAME.match(args.name):
        ap.error(f"invalid plugin name {args.name!r}: use [a-z][a-z0-9_-]*")
    if not TEMPLATE_DIR.is_dir():
        ap.error(f"template dir not found: {TEMPLATE_DIR}")

    dest_parent = args.dest.expanduser()
    if not dest_parent.exists():
        ap.error(f"destination {dest_parent} does not exist (create it or pass --dest)")

    plugin_dir = dest_parent / args.name
    plugin_dir.mkdir(parents=True, exist_ok=True)

    wrote = []
    for tpl in ("plugin.yaml", "__init__.py"):
        out = plugin_dir / tpl
        if out.exists() and not args.force:
            ap.error(f"{out} already exists (use --force to overwrite)")
        rendered = render((TEMPLATE_DIR / tpl).read_text(encoding="utf-8"),
                          args.name, args.description)
        out.write_text(rendered, encoding="utf-8")
        wrote.append(out)

    for p in wrote:
        print(f"✓ wrote {p}")
    print(f"  next: python3 {SKILL_DIR/'scripts'/'verify_extension.py'} --plugin {args.name}")
    print("        verify discovery through the XNOBrain runtime integration")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
